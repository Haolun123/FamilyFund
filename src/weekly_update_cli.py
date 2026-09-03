"""
weekly_update_cli.py
~~~~~~~~~~~~~~~~~~~~
Weekly Update 的命令行执行引擎。
由 /weekly-update skill 调用，读取 Obsidian 的 _weekly_input.md，
执行价格刷新、短信解析、交易登记、对账校验、保存快照。

用法：
    python weekly_update_cli.py --input <path_to_weekly_input.md> [--confirm]

模式：
    默认（dry-run）：只预览，不写入
    --confirm：预览通过后正式写入
"""

import argparse
import os
import re
import sys
import json
from datetime import datetime
from typing import Optional

import pandas as pd

# ── 路径常量 ──────────────────────────────────────────────────────────────────

DATA_DIR   = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.expanduser(
        '~/Library/Mobile Documents/com~apple~CloudDocs/'
        'Project_shared_files/FamilyFund/data'
    )
)
CSV_PATH   = os.path.join(DATA_DIR, 'portfolio.csv')
TX_PATH    = os.path.join(DATA_DIR, 'transaction.csv')
VAULT_DIR  = os.path.expanduser(
    '~/Library/Mobile Documents/iCloud~md~obsidian/Documents/vault'
)
INPUT_PATH = os.path.join(VAULT_DIR, 'familyfund', '_weekly_input.md')

SRC_DIR = os.path.join(os.path.dirname(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


# ── 解析 _weekly_input.md ─────────────────────────────────────────────────────

def parse_weekly_input(path: str) -> dict:
    """
    解析 _weekly_input.md，返回结构化数据。
    """
    with open(path, encoding='utf-8') as f:
        content = f.read()

    result = {
        'date':        None,
        'sms_lines':   [],
        'fixed_nav':   {},   # {'季季宝': 1.0229, ...}
        'trades':      [],   # [{'direction':'买入','code':'09633.HK','amount':21372,'fee':52}]
        'cash':        None,
        'notes':       '',
        'errors':      [],
    }

    # ── 日期 ──
    m = re.search(r'^date:\s*(\d{4}-\d{2}-\d{2})', content, re.MULTILINE)
    if m:
        result['date'] = m.group(1)
    else:
        result['errors'].append('❌ 日期未填或格式错误，请填写 date: YYYY-MM-DD')

    # ── 短信区 ──
    sms_block = _extract_section(content, '📱 短信区', '💰 固收净值')
    if sms_block:
        for line in sms_block.splitlines():
            line = line.strip()
            if line and not line.startswith('<!--') and not line.startswith('>'):
                result['sms_lines'].append(line)

    # ── 固收净值 ──
    nav_block = _extract_section(content, '💰 固收净值', '🔄 手动交易登记')
    if nav_block:
        for line in nav_block.splitlines():
            line = line.strip()
            if not line or line.startswith('<!--') or line.startswith('>'):
                continue
            m = re.match(r'^(.+?):\s*(\?|[\d.]+)\s*$', line)
            if m:
                name = m.group(1).strip()
                val  = m.group(2).strip()
                if val == '?':
                    result['errors'].append(f'❌ 固收净值未填：{name}，请在招行 App 查询后填入')
                else:
                    try:
                        result['fixed_nav'][name] = float(val)
                    except ValueError:
                        result['errors'].append(f'❌ 固收净值格式错误：{line}')

    # ── 手动交易 ──
    trade_block = _extract_section(content, '🔄 手动交易登记', '💵 现金余额')
    if trade_block:
        for line in trade_block.splitlines():
            line = line.strip()
            if not line or line.startswith('<!--') or line.startswith('>'):
                continue
            parts = line.split()
            if len(parts) < 3:
                result['errors'].append(f'❌ 交易格式错误（需要：方向 标的 金额 [手续费]）：{line}')
                continue
            direction = parts[0]
            if direction not in ('买入', '卖出', '赎回'):
                result['errors'].append(f'❌ 交易方向无效（应为买入/卖出/赎回）：{line}')
                continue
            code_or_name = parts[1]
            try:
                amount = float(parts[2].replace(',', ''))
                fee    = float(parts[3].replace(',', '')) if len(parts) > 3 else 0.0
            except ValueError:
                result['errors'].append(f'❌ 交易金额格式错误：{line}')
                continue
            result['trades'].append({
                'direction': direction,
                'code_or_name': code_or_name,
                'amount': amount,
                'fee': fee,
            })

    # ── 现金余额 ──
    cash_block = _extract_section(content, '💵 现金余额', '📝 备注')
    if cash_block:
        for line in cash_block.splitlines():
            line = line.strip()
            if not line or line.startswith('<!--') or line.startswith('>') or line.startswith('#'):
                continue
            # 跳过纯文字说明行（不含数字）
            if not re.search(r'\d', line):
                continue
            try:
                result['cash'] = float(line.replace(',', ''))
            except ValueError:
                result['errors'].append(f'❌ 现金余额格式错误：{line}')

    # ── 备注 ──
    notes_block = _extract_section(content, '📝 备注', None)
    if notes_block:
        result['notes'] = '\n'.join(
            l for l in notes_block.splitlines()
            if l.strip() and not l.strip().startswith('<!--')
        )

    return result


def _extract_section(content: str, start_header: str, end_header: Optional[str]) -> str:
    """提取两个 ## 标题之间的内容（不含标题行）。"""
    pattern_start = rf'##[^#].*{re.escape(start_header)}'
    m_start = re.search(pattern_start, content)
    if not m_start:
        return ''
    pos = m_start.end()

    if end_header:
        pattern_end = rf'##[^#].*{re.escape(end_header)}'
        m_end = re.search(pattern_end, content[pos:])
        if m_end:
            return content[pos: pos + m_end.start()]

    return content[pos:]


# ── 价格刷新 ──────────────────────────────────────────────────────────────────

def refresh_prices(template_df: pd.DataFrame, fixed_nav: dict) -> tuple[pd.DataFrame, list]:
    """
    刷新价格：
    - yfinance 拉取港股/A股/SAP/ETF
    - 固收净值用 fixed_nav 覆盖
    - 黄金用 akshare
    返回 (updated_df, warnings)
    """
    warnings = []
    df = template_df.copy()

    # 固收净值更新
    for name, nav in fixed_nav.items():
        mask = df['Name'] == name
        if mask.sum() == 0:
            warnings.append(f'⚠️  固收净值：找不到标的"{name}"，已跳过')
            continue
        df.loc[mask, 'Current_Price'] = nav
        df.loc[mask, 'Total_Value'] = df.loc[mask, 'Shares'] * nav

    # yfinance 价格刷新
    try:
        from price_fetcher import fetch_latest_prices
        price_map = fetch_latest_prices(df, data_dir=DATA_DIR)
        for code, info in price_map.items():
            mask = df['Code'] == code
            if mask.sum() == 0:
                continue
            price = info.get('price')
            rate  = info.get('exchange_rate')
            if price and price == price:  # not NaN
                df.loc[mask, 'Current_Price'] = price
            if rate and rate == rate:
                df.loc[mask, 'Exchange_Rate'] = rate
            # 重算 Total_Value
            df.loc[mask, 'Total_Value'] = (
                df.loc[mask, 'Shares'] *
                df.loc[mask, 'Current_Price'] *
                df.loc[mask, 'Exchange_Rate']
            )
    except Exception as e:
        warnings.append(f'⚠️  价格刷新异常：{e}（各标的保留上期价格）')

    # 检查 NaN
    nan_rows = df[df['Total_Value'].isna() | (df['Total_Value'] == 0)]
    nan_rows = nan_rows[nan_rows['Asset_Class'] != 'Cash']
    for _, row in nan_rows.iterrows():
        warnings.append(
            f'⚠️  {row["Name"]} Total_Value 为空，使用上期价格'
        )
        # fallback：用 template 里的旧值
        old = template_df.loc[template_df['Name'] == row['Name'], 'Total_Value']
        if not old.empty:
            df.loc[df['Name'] == row['Name'], 'Total_Value'] = old.values[0]
            df.loc[df['Name'] == row['Name'], 'Current_Price'] = \
                template_df.loc[template_df['Name'] == row['Name'], 'Current_Price'].values[0]

    return df, warnings


# ── 短信解析 ──────────────────────────────────────────────────────────────────

def apply_sms(sms_lines: list, template_df: pd.DataFrame, date_str: str) -> tuple[pd.DataFrame, list, list]:
    """
    解析短信，更新份额和 NCF。
    返回 (updated_df, transactions, warnings)
    """
    warnings = []
    transactions = []

    if not sms_lines:
        return template_df, transactions, warnings

    sms_text = '\n'.join(sms_lines)
    df = template_df.copy()

    try:
        from sms_parser import parse_sms
        holdings = df[['Name', 'Code', 'Asset_Class', 'Shares', 'Current_Price', 'Platform']].to_dict('records')
        parsed = parse_sms(sms_text, holdings, data_dir=DATA_DIR)

        if parsed is None or len(parsed) == 0:
            warnings.append('⚠️  短信未解析到任何定投记录，请检查短信格式')
            return df, transactions, warnings

        for item in parsed:
            name    = item.get('name') or item.get('Name', '')
            code    = item.get('code') or item.get('Code', '')
            shares  = float(item.get('shares_delta', 0))
            amount  = float(item.get('amount', 0))
            nav     = float(item.get('nav', 0))

            mask = (df['Name'] == name) | (df['Code'] == code)
            if mask.sum() == 0:
                warnings.append(f'⚠️  短信解析：找不到标的"{name}"（{code}），已跳过')
                continue

            if shares <= 0 and amount <= 0:
                warnings.append(f'⚠️  短信解析：{name} 份额和金额均为0，已跳过')
                continue

            # 更新份额和 NCF
            old_shares = float(df.loc[mask, 'Shares'].values[0])
            df.loc[mask, 'Shares'] = old_shares + shares
            df.loc[mask, 'Net_Cash_Flow'] = df.loc[mask, 'Net_Cash_Flow'].fillna(0) + amount
            if nav > 0:
                df.loc[mask, 'Current_Price'] = nav
            df.loc[mask, 'Total_Value'] = df.loc[mask, 'Shares'] * df.loc[mask, 'Current_Price']

            transactions.append({
                'Date': date_str,
                'Name': name,
                'Code': code,
                'Direction': '买入',
                'Amount_CNY': amount,
                'Shares_Delta': shares,
                'Nav': nav,
                'Source': 'SMS',
            })

    except Exception as e:
        warnings.append(f'⚠️  短信解析异常：{e}')

    return df, transactions, warnings


# ── 手动交易 ──────────────────────────────────────────────────────────────────

def apply_trades(trades: list, template_df: pd.DataFrame, date_str: str) -> tuple[pd.DataFrame, list, list]:
    """
    登记手动交易（买入/卖出/赎回）。
    返回 (updated_df, transactions, warnings)
    """
    warnings = []
    transactions = []
    df = template_df.copy()

    for trade in trades:
        direction    = trade['direction']
        code_or_name = trade['code_or_name']
        amount       = trade['amount']
        fee          = trade['fee']

        # 查找标的
        mask = (df['Code'] == code_or_name) | (df['Name'] == code_or_name)
        if mask.sum() == 0:
            warnings.append(f'⚠️  交易标的"{code_or_name}"不在持仓中，请确认代码是否正确')
            continue

        name = df.loc[mask, 'Name'].values[0]
        code = df.loc[mask, 'Code'].values[0]
        cur_price = float(df.loc[mask, 'Current_Price'].values[0])

        if direction == '买入':
            ncf = amount + fee
            df.loc[mask, 'Net_Cash_Flow'] = df.loc[mask, 'Net_Cash_Flow'].fillna(0) + ncf
            # 更新份额（若有价格）
            if cur_price > 0:
                new_shares = amount / cur_price
                df.loc[mask, 'Shares'] = float(df.loc[mask, 'Shares'].values[0]) + new_shares
            df.loc[mask, 'Total_Value'] = df.loc[mask, 'Shares'] * cur_price
            # 同步扣减 Cash
            cash_mask = df['Asset_Class'] == 'Cash'
            if cash_mask.sum() > 0:
                df.loc[cash_mask, 'Total_Value'] -= (amount + fee)
                df.loc[cash_mask, 'Net_Cash_Flow'] = \
                    df.loc[cash_mask, 'Net_Cash_Flow'].fillna(0) - (amount + fee)

        elif direction in ('卖出', '赎回'):
            ncf = -(amount - fee)
            df.loc[mask, 'Net_Cash_Flow'] = df.loc[mask, 'Net_Cash_Flow'].fillna(0) + ncf
            if cur_price > 0:
                reduce_shares = amount / cur_price
                old_shares = float(df.loc[mask, 'Shares'].values[0])
                df.loc[mask, 'Shares'] = max(0, old_shares - reduce_shares)
            df.loc[mask, 'Total_Value'] = df.loc[mask, 'Shares'] * cur_price
            # 同步增加 Cash
            cash_mask = df['Asset_Class'] == 'Cash'
            if cash_mask.sum() > 0:
                df.loc[cash_mask, 'Total_Value'] += (amount - fee)
                df.loc[cash_mask, 'Net_Cash_Flow'] = \
                    df.loc[cash_mask, 'Net_Cash_Flow'].fillna(0) + (amount - fee)

        transactions.append({
            'Date': date_str,
            'Name': name,
            'Code': code,
            'Direction': direction,
            'Amount_CNY': amount,
            'Fee_CNY': fee,
            'Source': 'Manual',
        })

    return df, transactions, warnings


# ── 对账校验 ──────────────────────────────────────────────────────────────────

def reconcile(
    snapshot_df: pd.DataFrame,
    new_transactions: list,
    date_str: str,
) -> tuple[bool, list]:
    """
    对账：本次新增交易的 NCF 总额 vs snapshot 里新增的 NCF 总额
    返回 (ok, messages)
    """
    messages = []

    # 本次买入交易总额（SMS + Manual）
    new_buy_total = sum(
        t['Amount_CNY'] + t.get('Fee_CNY', 0)
        for t in new_transactions
        if t.get('Direction') == '买入'
    )
    new_sell_total = sum(
        t['Amount_CNY'] - t.get('Fee_CNY', 0)
        for t in new_transactions
        if t.get('Direction') in ('卖出', '赎回')
    )

    # snapshot 里本周 NCF 总和（正负相消）
    snapshot_ncf = float(snapshot_df['Net_Cash_Flow'].fillna(0).sum())
    expected_ncf = new_buy_total - new_sell_total

    # 无新交易时直接通过
    if new_buy_total == 0 and new_sell_total == 0:
        messages.append('✅ 本周无新交易，对账通过')
        return True, messages

    diff = abs(snapshot_ncf - expected_ncf)
    tol  = max(50, expected_ncf * 0.001)  # 容忍 0.1% 或 50 元

    if diff <= tol:
        messages.append(
            f'✅ 对账通过：本周买入 ¥{new_buy_total:,.0f}，'
            f'卖出 ¥{new_sell_total:,.0f}，差异 ¥{diff:.0f}'
        )
        return True, messages
    else:
        messages.append(
            f'❌ 对账不平：snapshot NCF ¥{snapshot_ncf:,.0f}，'
            f'预期 ¥{expected_ncf:,.0f}，差异 ¥{diff:,.0f}'
        )
        messages.append('   请检查：①短信是否有漏粘贴 ②手动交易金额是否正确')
        return False, messages


# ── 快照预览 ──────────────────────────────────────────────────────────────────

def build_preview(
    date_str: str,
    snapshot_df: pd.DataFrame,
    transactions: list,
    price_warnings: list,
    sms_warnings: list,
    trade_warnings: list,
    reconcile_ok: bool,
    reconcile_msgs: list,
) -> str:
    """生成 dry-run 预览报告。"""
    lines = [
        f'## Weekly Update 预览 — {date_str}',
        '',
        '### 📊 快照概览',
    ]

    total = snapshot_df['Total_Value'].sum()
    cash  = float(snapshot_df.loc[snapshot_df['Asset_Class'] == 'Cash', 'Total_Value'].sum())
    lines.append(f'总资产：¥{total:,.0f}　现金：¥{cash:,.0f}')
    lines.append('')

    # 价格状态
    lines.append('### 💹 价格刷新')
    if price_warnings:
        for w in price_warnings:
            lines.append(f'  {w}')
    else:
        lines.append('  ✅ 全部成功')
    lines.append('')

    # 短信解析
    lines.append('### 📱 短信解析')
    sms_tx = [t for t in transactions if t.get('Source') == 'SMS']
    if sms_tx:
        for t in sms_tx:
            lines.append(f'  ✅ {t["Name"]}：买入 ¥{t["Amount_CNY"]:,.0f}，+{t.get("Shares_Delta",0):.2f}份')
    else:
        lines.append('  — 无短信定投')
    for w in sms_warnings:
        lines.append(f'  {w}')
    lines.append('')

    # 手动交易
    lines.append('### 🔄 手动交易')
    manual_tx = [t for t in transactions if t.get('Source') == 'Manual']
    if manual_tx:
        for t in manual_tx:
            lines.append(f'  ✅ {t["Direction"]} {t["Name"]}：¥{t["Amount_CNY"]:,.0f}，手续费 ¥{t.get("Fee_CNY",0)}')
    else:
        lines.append('  — 无手动交易')
    for w in trade_warnings:
        lines.append(f'  {w}')
    lines.append('')

    # 对账
    lines.append('### ✅ 对账校验')
    for m in reconcile_msgs:
        lines.append(f'  {m}')
    lines.append('')

    # 持仓变化
    lines.append('### 📋 持仓变化（非零 NCF）')
    changed = snapshot_df[snapshot_df['Net_Cash_Flow'].fillna(0) != 0]
    for _, row in changed.iterrows():
        lines.append(
            f'  {row["Name"]}：NCF {row["Net_Cash_Flow"]:+,.0f}，'
            f'市值 ¥{row["Total_Value"]:,.0f}'
        )
    lines.append('')

    if reconcile_ok:
        lines.append('---')
        lines.append('**✅ 预览通过。回复"确认执行"或在命令行加 --confirm 保存快照。**')
    else:
        lines.append('---')
        lines.append('**❌ 对账不平，请修正后重新运行。**')

    return '\n'.join(lines)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(input_path: str, confirm: bool = False) -> str:
    """
    主执行函数。
    返回预览报告字符串（供 Skill 展示给用户）。
    """
    # 1. 解析输入
    inp = parse_weekly_input(input_path)

    if inp['errors']:
        return '## ❌ 输入有误，请修正后重试\n\n' + '\n'.join(inp['errors'])

    date_str = inp['date']

    # 2. 读取最新快照，构建模板
    raw_df = pd.read_csv(CSV_PATH)
    raw_df['Date'] = pd.to_datetime(raw_df['Date']).dt.strftime('%Y-%m-%d')
    latest_date = raw_df['Date'].max()
    template = raw_df[raw_df['Date'] == latest_date].copy()
    template['Net_Cash_Flow'] = 0.0  # 本周 NCF 从 0 开始累加

    # 3. 固收净值 + 价格刷新
    template, price_warnings = refresh_prices(template, inp['fixed_nav'])

    # 4. 短信解析
    template, sms_tx, sms_warnings = apply_sms(inp['sms_lines'], template, date_str)

    # 5. 手动交易
    template, manual_tx, trade_warnings = apply_trades(inp['trades'], template, date_str)

    # 6. 现金余额覆盖（如填写）
    if inp['cash'] is not None:
        cash_mask = template['Asset_Class'] == 'Cash'
        template.loc[cash_mask, 'Total_Value'] = inp['cash']

    all_transactions = sms_tx + manual_tx

    # 7. 对账
    recon_ok, recon_msgs = reconcile(template, all_transactions, date_str)

    # 8. 预览报告
    preview = build_preview(
        date_str, template,
        all_transactions,
        price_warnings, sms_warnings, trade_warnings,
        recon_ok, recon_msgs,
    )

    # 9. 若 confirm 且对账通过，写入
    if confirm:
        if not recon_ok:
            return preview + '\n\n❌ 对账不平，已拒绝写入。'

        from nav_engine import _atomic_write_csv
        # 加上 Date 列
        template.insert(0, 'Date', date_str)
        existing = pd.read_csv(CSV_PATH)
        combined = pd.concat([existing, template], ignore_index=True)
        _atomic_write_csv(combined, CSV_PATH)

        # 写 transaction.csv
        if all_transactions:
            tx_df = pd.DataFrame(all_transactions)
            if os.path.exists(TX_PATH):
                tx_existing = pd.read_csv(TX_PATH)
                tx_df = pd.concat([tx_existing, tx_df], ignore_index=True)
            tx_df.to_csv(TX_PATH, index=False)

        # Obsidian 同步
        try:
            from nav_engine import (
                compute_fund_nav, compute_class_nav, compute_allocation,
                compute_cost_basis, compute_xirr, compute_sharpe, compute_calmar,
            )
            from obsidian_sync import sync_to_obsidian

            updated_df = pd.read_csv(CSV_PATH)
            fund_nav   = compute_fund_nav(updated_df)
            alloc      = compute_allocation(updated_df)
            cost       = compute_cost_basis(updated_df)
            class_nav  = compute_class_nav(updated_df)
            xirr       = compute_xirr(updated_df)
            sharpe     = compute_sharpe(fund_nav)
            calmar     = compute_calmar(fund_nav)
            navs       = fund_nav['NAV'].astype(float)
            mdd        = float(((navs - navs.cummax()) / navs.cummax()).min())
            first      = updated_df['Date'].min()
            inv        = float(updated_df[updated_df['Date'] == first]['Total_Value'].sum())
            infl       = float(updated_df[
                (updated_df['Date'] > first) &
                (updated_df['Asset_Class'].isin(['Cash', 'Company_Stock'])) &
                (updated_df['Net_Cash_Flow'] > 0)
            ]['Net_Cash_Flow'].sum())

            son_csv  = os.path.join(DATA_DIR, 'son', 'portfolio.csv')
            son_nav_df = son_cost_df = None
            if os.path.exists(son_csv):
                sdf = pd.read_csv(son_csv)
                son_nav_df  = compute_fund_nav(sdf)
                son_cost_df = compute_cost_basis(sdf)

            sync_to_obsidian(
                date_str=date_str, data_dir=DATA_DIR,
                fund_nav_df=fund_nav, allocation_df=alloc, cost_basis_df=cost,
                xirr=xirr, max_drawdown=mdd, raw_df=updated_df,
                class_nav_dict=class_nav, sharpe=sharpe, calmar=calmar,
                total_invested=inv + infl,
                son_nav_df=son_nav_df, son_cost_df=son_cost_df,
            )
            preview += '\n\n✅ 快照已保存，Obsidian 已同步。'
        except Exception as e:
            preview += f'\n\n⚠️  Obsidian 同步失败（快照已保存）：{e}'

        # 清空输入文件的 status
        _mark_input_done(input_path, date_str)

    return preview


def _mark_input_done(path: str, date_str: str) -> None:
    """将 _weekly_input.md 的 status 改为 done，避免重复执行。"""
    with open(path, encoding='utf-8') as f:
        content = f.read()
    content = re.sub(r'^status: pending', f'status: done ({date_str})', content, flags=re.MULTILINE)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FamilyFund Weekly Update CLI')
    parser.add_argument('--input', default=INPUT_PATH, help='_weekly_input.md 路径')
    parser.add_argument('--confirm', action='store_true', help='确认写入（默认 dry-run）')
    args = parser.parse_args()

    result = run(args.input, confirm=args.confirm)
    print(result)
