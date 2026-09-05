"""
weekly_update_cli.py
~~~~~~~~~~~~~~~~~~~~
Weekly Update 的命令行执行引擎。
由 /weekly-update skill 调用，读取 Obsidian 的 _weekly_input.md。

执行顺序（与 Dashboard 一致）：
  1. 解析并校验输入
  2. 短信解析（定投份额/净值/NCF）
  3. 道指抵扣（预付池扣减）
  4. 手动交易登记
  5. 净值拉取（自动拉取 + 手动固收覆盖）
  6. 对账校验
  7. dry-run 预览 / --confirm 写入

用法：
    python weekly_update_cli.py [--input <path>] [--confirm]
"""

import argparse
import os
import re
import sys
import json
import shutil
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

# ── 路径 ──────────────────────────────────────────────────────────────────────

DATA_DIR  = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.expanduser(
        '~/Library/Mobile Documents/com~apple~CloudDocs/'
        'Project_shared_files/FamilyFund/data'
    )
)
SON_DATA_DIR = os.environ.get(
    'FAMILYFUND_SON_DATA',
    os.path.join(DATA_DIR, 'son')
)
CSV_PATH    = os.path.join(DATA_DIR, 'portfolio.csv')
SON_CSV_PATH = os.path.join(SON_DATA_DIR, 'portfolio.csv')
TX_PATH     = os.path.join(DATA_DIR, 'transaction.csv')
VAULT_DIR   = os.path.expanduser(
    '~/Library/Mobile Documents/iCloud~md~obsidian/Documents/vault'
)
INPUT_PATH  = os.path.join(VAULT_DIR, 'familyfund', '_weekly_input.md')
ARCHIVE_DIR = os.path.join(VAULT_DIR, 'familyfund', 'weekly_archive')

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# ── 手动填写标的（API 确认无净值数据，永久无法拉取）─────────────────────────

MANUAL_PRICE_ASSETS = {
    '月月宝': 'JY040214',   # 货币基金，天天基金 API 无净值
    '季季宝': '133038A',    # 货币基金，天天基金 API 无净值
}

# 退避重试策略：第一次失败等 30s，第二次失败等 60s，再失败交互
RETRY_DELAYS = [30, 60]


# ── 解析 _weekly_input.md ─────────────────────────────────────────────────────

def parse_weekly_input(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        content = f.read()

    result = {
        'date':           None,
        'status':         'pending',
        'sms_lines':      [],
        'son_sms_lines':  [],
        'dow_topup':      None,
        'trades':         [],
        'sap_events':     [],      # SAP 归属事件列表
        'manual_prices':  {},
        'cash_delta':     None,
        'notes':          '',
        'errors':         [],
        'warnings':       [],
    }

    # status
    m = re.search(r'^status:\s*(\S+)', content, re.MULTILINE)
    if m:
        result['status'] = m.group(1)

    # 日期
    m = re.search(r'^date:\s*(\d{4}-\d{2}-\d{2})', content, re.MULTILINE)
    if m:
        result['date'] = m.group(1)
    else:
        result['errors'].append('❌ 日期未填，请填写 date: YYYY-MM-DD')

    # 短信区（保留空行，parse_sms 用空行分割多条短信）
    sms_block = _section(content, '步骤一：短信区', '步骤二：道指抵扣')
    if sms_block:
        for line in sms_block.splitlines():
            stripped = line.strip()
            if stripped.startswith('<!--') or stripped.startswith('>'):
                continue  # 跳过注释
            if re.match(r'^-{3,}$', stripped):
                continue  # 跳过 --- 分隔符
            result['sms_lines'].append(stripped)  # 空行保留（作为短信分隔符）
        # 去掉头尾多余空行
        while result['sms_lines'] and not result['sms_lines'][0]:
            result['sms_lines'].pop(0)
        while result['sms_lines'] and not result['sms_lines'][-1]:
            result['sms_lines'].pop()

    # 道指补仓（可选）
    dow_block = _section(content, '步骤二：道指抵扣', '步骤三：手动交易登记')
    if dow_block:
        m = re.search(r'^道指补仓:\s*([\d,.]+)', dow_block, re.MULTILINE)
        if m:
            try:
                result['dow_topup'] = float(m.group(1).replace(',', ''))
            except ValueError:
                result['warnings'].append('⚠️ 道指补仓金额格式错误，已忽略')

    # 手动交易（表格格式）
    # 列：方向 | Code | 名称 | 金额CNY | 成交价 | 份额
    trade_block = _section(content, '步骤三：手动交易登记', '步骤四：固收净值')
    if trade_block:
        for line in trade_block.splitlines():
            line = line.strip()
            if not line or line.startswith('<!--') or line.startswith('>'):
                continue
            # 跳过表头行和分隔行
            if re.match(r'^\|[\s\-|]+\|$', line):  # 分隔行 | --- | --- |
                continue
            if not line.startswith('|') or not line.endswith('|'):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 4:
                continue
            # 表头行（包含中文字段名）
            if cells[0] in ('方向', '---', ''):
                continue

            direction  = cells[0] if len(cells) > 0 else ''
            code       = cells[1] if len(cells) > 1 else ''
            name       = cells[2] if len(cells) > 2 else ''
            amount_str = cells[3] if len(cells) > 3 else ''
            price_str  = cells[4] if len(cells) > 4 else ''
            shares_str = cells[5] if len(cells) > 5 else ''

            # 跳过全空行
            if not any([direction, code, name, amount_str]):
                continue

            if direction not in ('买入', '卖出', '赎回'):
                result['errors'].append(
                    f'❌ 方向无效（买入/卖出/赎回）：「{direction}」'
                    f'（Code={code}，名称={name}）'
                )
                continue

            if not amount_str:
                result['errors'].append(
                    f'❌ 金额未填（方向={direction}，Code={code}，名称={name}）'
                )
                continue

            # Code 和名称至少填一个
            if not code and not name:
                result['errors'].append(f'❌ Code 和名称均未填，无法匹配标的')
                continue

            try:
                amount = float(amount_str.replace(',', ''))
                price  = float(price_str.replace(',', ''))  if price_str  else None
                shares = float(shares_str.replace(',', '')) if shares_str else None
            except ValueError as e:
                result['errors'].append(
                    f'❌ 数字格式错误（Code={code}）：{e}'
                )
                continue

            result['trades'].append({
                'direction': direction,
                'code':      code,
                'name':      name,
                'amount':    amount,
                'price':     price,
                'shares':    shares,
            })


    # 固收净值（手动）
    nav_block = _section(content, '步骤四：固收净值', '💵 外部资金变动')
    for line in (nav_block or '').splitlines():
        line = line.strip()
        if not line or line.startswith('<!--') or line.startswith('>'):
            continue
        m = re.match(r'^(.+?):\s*(\?|[\d.]+)\s*$', line)
        if not m:
            continue
        name, val = m.group(1).strip(), m.group(2).strip()
        if val == '?':
            result['errors'].append(
                f'❌ 固收净值未填：{name}（在招行 App 查询后填入）'
            )
        else:
            try:
                result['manual_prices'][name] = float(val)
            except ValueError:
                result['errors'].append(f'❌ 净值格式错误：{line}')

    # 检查必填的手动净值是否都有
    for name in MANUAL_PRICE_ASSETS:
        if name not in result['manual_prices'] and not result['errors']:
            result['errors'].append(
                f'❌ 固收净值未填：{name}（请在步骤四填入）'
            )

    # 外部资金变动
    cash_block = _section(content, '💵 外部资金变动', '💼 SAP 归属')
    for line in (cash_block or '').splitlines():
        line = line.strip()
        if not line or line.startswith('<!--') or line.startswith('>'):
            continue
        if not re.search(r'[-\d]', line):
            continue
        try:
            result['cash_delta'] = float(line.replace(',', ''))
        except ValueError:
            result['warnings'].append(f'⚠️ 外部资金变动格式错误：{line}，已忽略')

    # SAP 归属（表格格式）
    # 列：类型 | 日期 | 股价EUR | 税率/汇率EUR | Match CNY | Match股数 | Purchase CNY | Purchase股数 | RSU股数
    sap_block = _section(content, '💼 SAP 归属', '👶 Son Fund 短信区')
    if sap_block:
        VALID_SAP_TYPES = ('ESPP', 'RSU', 'ESPP_DIVIDEND', 'RSU_DIVIDEND',
                           'ESPP-DIVIDEND', 'RSU-DIVIDEND')
        for line in sap_block.splitlines():
            line = line.strip()
            if not line or line.startswith('<!--') or line.startswith('>'):
                continue
            if re.match(r'^\|[\s\-|]+\|$', line):
                continue
            if not line.startswith('|') or not line.endswith('|'):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 2:
                continue
            # 跳过表头
            if cells[0] in ('类型', '---', ''):
                continue
            # 跳过全空行
            if not any(c for c in cells):
                continue

            def cell(i): return cells[i].strip() if i < len(cells) else ''

            sap_type = cell(0).upper().replace('-', '_')
            if sap_type not in ('ESPP', 'RSU', 'ESPP_DIVIDEND', 'RSU_DIVIDEND'):
                if sap_type:  # 非空但无效
                    result['errors'].append(
                        f'❌ SAP 类型无效（应为 ESPP/RSU/ESPP-Dividend/RSU-Dividend）：{cell(0)}'
                    )
                continue

            date_val    = cell(1)
            price_eur   = cell(2)
            tax_or_fx   = cell(3)   # ESPP→税率，RSU→汇率EUR
            match_cny   = cell(4)
            match_qty   = cell(5)
            purch_cny   = cell(6)
            purch_qty   = cell(7)
            rsu_qty     = cell(8)

            if not date_val:
                result['errors'].append(f'❌ SAP 归属行缺少日期（类型={sap_type}）')
                continue
            if not price_eur:
                result['errors'].append(f'❌ SAP 归属行缺少股价EUR（{date_val}）')
                continue

            # 组装 fields dict，复用 step_sap 的解析逻辑
            fields = {
                '类型':    cell(0),
                '日期':    date_val,
                '股价EUR': price_eur,
            }

            if sap_type == 'ESPP':
                missing = []
                if not tax_or_fx: missing.append('税率')
                if not match_cny: missing.append('Match CNY')
                if not match_qty: missing.append('Match股数')
                if not purch_cny: missing.append('Purchase CNY')
                if not purch_qty: missing.append('Purchase股数')
                if missing:
                    result['errors'].append(
                        f'❌ ESPP行缺少字段：{", ".join(missing)}（{date_val}）'
                    )
                    continue
                fields.update({'税率': tax_or_fx, 'Match CNY': match_cny,
                                'Match 股数': match_qty, 'Purchase CNY': purch_cny,
                                'Purchase 股数': purch_qty})

            elif sap_type == 'RSU':
                qty_val = rsu_qty or match_cny  # RSU股数在第9列，兼容只填4列的情况
                if not tax_or_fx:
                    result['errors'].append(f'❌ RSU行缺少汇率EUR（{date_val}）')
                    continue
                if not qty_val:
                    result['errors'].append(f'❌ RSU行缺少股数（{date_val}）')
                    continue
                fields.update({'汇率EUR': tax_or_fx, '股数': qty_val})

            elif sap_type in ('ESPP_DIVIDEND', 'RSU_DIVIDEND'):
                qty_val = rsu_qty or match_cny
                if not tax_or_fx:
                    result['errors'].append(f'❌ Dividend行缺少汇率EUR（{date_val}）')
                    continue
                if not qty_val:
                    result['errors'].append(f'❌ Dividend行缺少股数（{date_val}）')
                    continue
                fields.update({'汇率EUR': tax_or_fx, '股数': qty_val})

            result['sap_events'].append({
                'type':   sap_type,
                'date':   date_val,
                'fields': fields,
            })


    # Son Fund 短信区（保留空行）
    son_block = _section(content, '👶 Son Fund 短信区', '📝 备注')
    if son_block:
        for line in son_block.splitlines():
            stripped = line.strip()
            if stripped.startswith('<!--') or stripped.startswith('>'):
                continue
            if re.match(r'^-{3,}$', stripped):
                continue
            result['son_sms_lines'].append(stripped)
        while result['son_sms_lines'] and not result['son_sms_lines'][0]:
            result['son_sms_lines'].pop(0)
        while result['son_sms_lines'] and not result['son_sms_lines'][-1]:
            result['son_sms_lines'].pop()

    # 备注
    notes_block = _section(content, '📝 备注', None)
    if notes_block:
        result['notes'] = '\n'.join(
            l for l in notes_block.splitlines()
            if l.strip() and not l.strip().startswith('<!--')
        )

    return result


def _section(content: str, start: str, end: Optional[str]) -> str:
    m_start = re.search(rf'##[^#\n]*{re.escape(start)}', content)
    if not m_start:
        return ''
    pos = m_start.end()
    if end:
        m_end = re.search(rf'##[^#\n]*{re.escape(end)}', content[pos:])
        if m_end:
            return content[pos: pos + m_end.start()]
    return content[pos:]


# ── 日期校验 ──────────────────────────────────────────────────────────────────

def validate_date(date_str: str, csv_path: str) -> tuple[bool, list]:
    """
    校验记账日期：
    - 格式正确
    - 距离上次快照约 7 天（±3 天容忍）
    - 不是未来日期
    - 未重复
    """
    messages = []
    try:
        target = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return False, ['❌ 日期格式错误，应为 YYYY-MM-DD']

    today = datetime.today()
    if target > today + timedelta(days=1):
        return False, [f'❌ 日期 {date_str} 是未来日期（今天 {today.strftime("%Y-%m-%d")}）']

    # 读取上次快照日期
    try:
        df = pd.read_csv(csv_path)
        last_date = datetime.strptime(df['Date'].max(), '%Y-%m-%d')
    except Exception:
        return True, ['⚠️ 无法读取历史快照，跳过周期校验']

    # 重复检查
    existing_dates = df['Date'].unique()
    if date_str in existing_dates:
        return False, [
            f'❌ {date_str} 已有快照记录，不能重复保存。',
            '   如需修正，请先在 Dashboard 删除该日期快照，再重新执行。'
        ]

    # 周期校验
    delta = (target - last_date).days
    if delta < 4:
        messages.append(
            f'⚠️ 距上次快照仅 {delta} 天（{last_date.strftime("%Y-%m-%d")} → {date_str}），'
            f'通常应间隔 7 天，请确认'
        )
    elif delta > 10:
        messages.append(
            f'⚠️ 距上次快照 {delta} 天（{last_date.strftime("%Y-%m-%d")} → {date_str}），'
            f'超过 10 天，请确认是否有手动快照未记录'
        )
    else:
        messages.append(
            f'✅ 日期校验通过：{last_date.strftime("%Y-%m-%d")} → {date_str}（间隔 {delta} 天）'
        )

    return True, messages


# ── 步骤一：短信解析 ──────────────────────────────────────────────────────────

def _interactive_add_sms_format(raw_sms: str) -> Optional[dict]:
    """
    交互式引导用户为无法识别的短信创建新解析格式。
    返回 fmt dict（可传给 save_custom_format），或 None（用户跳过）。
    """
    from sms_parser import build_regex_from_anchors, _try_custom_format

    print('\n📋 发现无法识别的短信格式，引导你创建解析规则：')
    print(f'   原始短信：\n   {raw_sms}\n')
    print('   是否创建解析规则？(直接回车=是，n=跳过本条)')
    if input('   > ').strip().lower() in ('n', 'no', '否'):
        return None

    print('\n   请根据短信内容回答以下问题（直接回车=该字段不存在/跳过）：\n')

    def _ask(prompt, required=False):
        while True:
            val = input(f'   {prompt}：').strip()
            if val or not required:
                return val or None
            print('   此字段为必填，请重新输入')

    # 格式名（用于识别和去重）
    fmt_name = _ask('格式名称（如"招行某基金"，用于区分）', required=True)

    # 触发词（快速判断是否尝试该格式）
    print('   触发词：短信中一定出现的唯一文字（如发送方名称或特征词，用于快速过滤）')
    trigger = _ask('触发词（如"招商银行"、"嘉实基金"）')

    # 日期
    print('\n   --- 日期字段 ---')
    date_prefix = _ask('日期前面的文字（如"于"、"申购日期"，无则回车跳过）')
    date_format = None
    if date_prefix:
        print('   日期格式：1=MM月DD日（如"9月4日"）  2=YYYY-MM-DD  3=8位数字（如"20260904"）')
        df_choice = input('   选择(1/2/3，回车=1)：').strip() or '1'
        date_format = {'1': 'MM月DD日', '2': 'YYYY-MM-DD', '3': '8位数字'}.get(df_choice, 'MM月DD日')

    # 基金名称
    print('\n   --- 基金名称字段 ---')
    fn_prefix = _ask('基金名称前面的文字（如"申购"、"定投"）')
    fn_suffix = None
    if fn_prefix:
        fn_suffix = _ask('基金名称后面的文字（可选，如"基金"、"，"，用于精确定位）')

    # 金额
    print('\n   --- 金额字段 ---')
    amt_prefix = _ask('金额前面的文字（如"申购金额"、"扣款金额"）', required=True)

    # 份额
    print('\n   --- 份额字段 ---')
    sh_prefix = _ask('份额前面的文字（如"申购确认份额"、"确认份额"）', required=True)

    # 净值
    print('\n   --- 净值字段 ---')
    print('   若短信没有净值，系统会从金额/份额自动反算，直接回车跳过')
    nav_prefix = _ask('净值前面的文字（如"基金净值"、"成交净值"）')

    anchors = {
        'date_prefix':       date_prefix,
        'date_format':       date_format or 'MM月DD日',
        'fund_name_prefix':  fn_prefix,
        'fund_name_suffix':  fn_suffix,
        'amount_prefix':     amt_prefix,
        'shares_prefix':     sh_prefix,
        'nav_prefix':        nav_prefix,
    }

    pattern = build_regex_from_anchors(anchors)

    # 在当前短信上验证
    print('\n   生成的正则：', pattern)
    print('   正在验证...')

    test_fmt = {'name': fmt_name, 'trigger': trigger or '', 'pattern': pattern, 'anchors': anchors}
    result   = _try_custom_format(raw_sms, test_fmt)

    if result is None:
        print('\n   ❌ 验证失败：正则未能匹配当前短信。')
        print('   可能原因：锚点文字不完全匹配（注意全角/半角、空格），请重试。')
        retry = input('   重新填写？(y/n)：').strip().lower()
        if retry in ('y', 'yes', '是'):
            return _interactive_add_sms_format(raw_sms)
        return None

    print(f'\n   ✅ 验证成功！提取结果：')
    print(f'      基金名称：{result.get("fund_name")}')
    print(f'      金额：¥{result.get("amount"):,.2f}')
    print(f'      份额：{result.get("shares"):.4f}')
    print(f'      净值：{result.get("nav")}')
    print(f'      日期：{result.get("confirm_date")}')

    confirm = input('\n   结果正确，保存格式？(直接回车=是，n=不保存)：').strip().lower()
    if confirm in ('n', 'no', '否'):
        return None

    return test_fmt


def _interactive_new_asset(fund_name: str, shares: float, amount: float, nav: float,
                            date_str: str) -> Optional[dict]:
    """
    交互式引导用户填写新标的信息。
    返回 new_asset dict，或 None（用户选择跳过）。
    """
    CLASS_SHORTCUTS = {
        '1': 'US_Blend_Fund',   '美股宽基': 'US_Blend_Fund',
        '2': 'US_Growth_Fund',  '美股成长': 'US_Growth_Fund',  '纳指': 'US_Growth_Fund',
        '3': 'CN_Index_Fund',   'A股指数': 'CN_Index_Fund',
        '4': 'Smart_Beta',      'smartbeta': 'Smart_Beta',
        '5': 'Individual_Stock','个股': 'Individual_Stock',
        '6': 'Fixed_Income',    '固收': 'Fixed_Income',
        '7': 'Gold',            '黄金': 'Gold',
        '8': 'Company_Stock',   '公司股': 'Company_Stock',
    }

    print(f'\n🆕 发现新标的：「{fund_name}」')
    print(f'   本次买入：¥{amount:,.0f}，份额 {shares:.4f}，申购净值 {nav}')
    print('   是否建立新持仓？(直接回车=是，n=跳过)')
    ans = input('   > ').strip().lower()
    if ans in ('n', 'no', '否', '跳过'):
        return None

    print(f'\n   请填写「{fund_name}」的基本信息：')

    # 代码
    code = input('   证券/基金代码（如 021000）：').strip()
    if not code:
        print('   代码不能为空，跳过')
        return None

    # 名称（默认用 fund_name）
    name = input(f'   持仓名称（回车使用「{fund_name}」）：').strip() or fund_name

    # 资产类别
    print('   资产类别：')
    print('   1=美股宽基  2=美股成长/纳指  3=A股指数  4=SmartBeta')
    print('   5=个股      6=固收            7=黄金     8=公司股票')
    cls_input = input('   选择（输入数字或名称）：').strip()
    asset_class = CLASS_SHORTCUTS.get(cls_input.lower(), cls_input)
    if asset_class not in {
        'US_Blend_Fund','US_Growth_Fund','CN_Index_Fund','Smart_Beta',
        'Individual_Stock','Fixed_Income','Gold','Company_Stock'
    }:
        print(f'   ⚠️ 类别「{asset_class}」无效，跳过')
        return None

    # 平台
    platform = input('   交易平台（如：纳指场外、中信证券，回车=场外）：').strip() or '场外'

    # 货币
    ccy_input = input('   货币（CNY/HKD/USD/EUR，回车=CNY）：').strip().upper() or 'CNY'

    # yf_symbol（可选）
    yf_sym = input('   yfinance 代码（可选，如 0700.HK，回车跳过）：').strip()

    new_asset = {
        'Code':        code,
        'Name':        name,
        'Asset_Class': asset_class,
        'Platform':    platform,
        'Currency':    ccy_input,
        'Exchange_Rate': 1.0,
        'yf_symbol':   yf_sym,
    }

    print(f'\n   ✅ 将新增持仓：{name}（{code}）/ {asset_class} / {platform}')

    # 写入 sms_code_map.json，下次自动精确匹配
    try:
        from sms_parser import add_sms_mapping
        add_sms_mapping(DATA_DIR, fund_name, code, name)
        print(f'   ✅ 已记录短信匹配规则：「{fund_name}」→ {code}')
    except Exception as e:
        print(f'   ⚠️ sms_code_map 写入失败：{e}')

    return new_asset


def step_sms(sms_lines: list, template_df: pd.DataFrame, date_str: str,
             interactive: bool = False) -> tuple[pd.DataFrame, list, list]:
    warnings, transactions = [], []
    if not sms_lines:
        return template_df, transactions, warnings

    df = template_df.copy()
    sms_text = '\n'.join(sms_lines)

    try:
        from sms_parser import parse_sms
        holdings = df[['Name', 'Code', 'Asset_Class', 'Shares',
                        'Current_Price', 'Platform']].to_dict('records')
        parsed = parse_sms(sms_text, holdings, data_dir=DATA_DIR)

        if not parsed:
            warnings.append('⚠️ 短信未解析到任何定投记录，请检查格式或确认本周无定投')
            return df, transactions, warnings

        for item in parsed:
            if item.get('parse_error'):
                raw = item.get('raw', '')
                if interactive:
                    # 交互式引导创建新格式
                    fmt = _interactive_add_sms_format(raw)
                    if fmt is not None:
                        # 保存格式
                        from sms_parser import save_custom_format, _try_custom_format
                        save_custom_format(DATA_DIR, fmt)
                        # 用新格式立即重新解析本条短信
                        retried = _try_custom_format(raw, fmt)
                        if retried:
                            # 重新走后续匹配逻辑
                            item = retried
                            # fall through（不 continue）
                        else:
                            warnings.append(f'⚠️ 新格式保存成功，但本条短信重解析失败，已跳过')
                            continue
                    else:
                        warnings.append(f'⚠️ 短信格式无法识别，已跳过：{raw[:60]}…')
                        continue
                else:
                    warnings.append(
                        f'⚠️ 短信格式无法识别（{raw[:40]}…）。'
                        f'确认执行时将引导你创建新解析规则。'
                    )
                    continue

            fund_name = item.get('fund_name', '')
            code      = item.get('matched_code') or ''
            name      = item.get('matched_name') or fund_name
            shares    = float(item.get('shares') or 0)
            amount    = float(item.get('amount') or 0)
            nav       = float(item.get('nav') or 0)

            # 未匹配到持仓
            mask = (df['Name'] == name) | (df['Code'] == code)
            if not mask.any():
                if interactive:
                    # 交互式引导建立新持仓
                    new_asset = _interactive_new_asset(
                        fund_name, shares, amount, nav, date_str
                    )
                    if new_asset is None:
                        warnings.append(f'— 跳过「{fund_name}」（用户选择不建仓）')
                        continue

                    # 构造新行追加到 df
                    cur_price = nav if nav > 0 else amount / shares if shares > 0 else 1.0
                    total_val = shares * cur_price
                    new_row = pd.DataFrame([{
                        'Asset_Class':    new_asset['Asset_Class'],
                        'Platform':       new_asset['Platform'],
                        'Name':           new_asset['Name'],
                        'Code':           new_asset['Code'],
                        'Currency':       new_asset['Currency'],
                        'Exchange_Rate':  new_asset['Exchange_Rate'],
                        'Shares':         shares,
                        'Current_Price':  cur_price,
                        'Total_Value':    total_val,
                        'Net_Cash_Flow':  amount,  # 建仓日 NCF = 买入金额
                    }])
                    df = pd.concat([df, new_row], ignore_index=True)
                    code = new_asset['Code']
                    name = new_asset['Name']
                    warnings.append(f'✅ 新建持仓：{name}（{code}），'
                                     f'份额 {shares:.4f}，NCF ¥{amount:,.0f}')
                else:
                    # dry-run 模式：提示但不操作
                    warnings.append(
                        f'⚠️ 「{fund_name}」未匹配到持仓。'
                        f'确认执行时将引导你建立新持仓。'
                    )
                    continue

            else:
                if shares <= 0 and amount <= 0:
                    warnings.append(f'⚠️ 短信：{name} 份额和金额均为0，已跳过')
                    continue

                old_shares = float(df.loc[mask, 'Shares'].values[0])
                df.loc[mask, 'Shares'] = old_shares + shares
                df.loc[mask, 'Net_Cash_Flow'] = (
                    df.loc[mask, 'Net_Cash_Flow'].fillna(0) + amount
                )
                # 短信净值是申购确认净值，不更新 Current_Price
                df.loc[mask, 'Total_Value'] = (
                    df.loc[mask, 'Shares'] * df.loc[mask, 'Current_Price']
                )

            # 短信 nav 是申购净值（CNY计价），直接作为 CNY 单价
            price_cny = round(nav, 6) if nav and nav > 0 else (
                round(amount / shares, 6) if shares and shares > 0 else 0.0
            )
            transactions.append({
                'Date':           date_str,
                'Asset_Class':    str(df.loc[mask, 'Asset_Class'].values[0]) if mask.any() else '',
                'Platform':       str(df.loc[mask, 'Platform'].values[0]) if mask.any() and 'Platform' in df.columns else '',
                'Name':           name,
                'Code':           code,
                'Type':           '买入',
                'Amount_CNY':     round(amount, 2),
                'Price':          price_cny,
                'Price_Currency': 'CNY',
                'Fee_CNY':        0.0,
                'Source':         'SMS',
            })

    except Exception as e:
        warnings.append(f'⚠️ 短信解析异常：{e}')

    return df, transactions, warnings


# ── 步骤一c：SAP 归属 ────────────────────────────────────────────────────────

def step_sap(sap_events: list, template_df: pd.DataFrame, date_str: str,
             confirm: bool = False) -> tuple[pd.DataFrame, list]:
    """
    处理 SAP 归属事件（ESPP / RSU / Dividend）。
    dry-run：只预览，不写文件。
    confirm：写入 own_sap.csv / move_sap.csv，更新 portfolio 快照行。
    返回 (updated_df, warnings)
    """
    if not sap_events:
        return template_df, []

    warnings = []
    df = template_df.copy()

    own_sap_path  = os.path.join(DATA_DIR, 'own_sap.csv')
    move_sap_path = os.path.join(DATA_DIR, 'move_sap.csv')

    new_own_rows  = []
    new_move_rows = []

    # 预加载已有记录，用于重复检查
    existing_own_dates  = set()
    existing_move_dates = set()
    if os.path.exists(own_sap_path):
        try:
            _own_existing = pd.read_csv(own_sap_path)
            existing_own_dates = set(_own_existing['Date'].astype(str).unique())
        except Exception:
            pass
    if os.path.exists(move_sap_path):
        try:
            _move_existing = pd.read_csv(move_sap_path)
            existing_move_dates = set(_move_existing['Date'].astype(str).unique())
        except Exception:
            pass

    for event in sap_events:
        sap_type = event['type']
        fields   = event['fields']
        evt_date = fields.get('日期', date_str)

        try:
            if sap_type == 'ESPP':
                price_eur   = float(fields['股价EUR'])
                match_cny   = float(fields['Match CNY'].replace(',', ''))
                match_qty   = float(fields['Match 股数'].replace(',', ''))
                purch_cny   = float(fields['Purchase CNY'].replace(',', ''))
                purch_qty   = float(fields['Purchase 股数'].replace(',', ''))

                total_qty  = match_qty + purch_qty
                total_cost = round(match_cny * 0.25 + purch_cny * 0.75, 2)

                # 台账重复检查
                already_in_own = evt_date in existing_own_dates
                if already_in_own:
                    warnings.append(
                        f'⏭ ESPP {evt_date}：own_sap.csv 已有该日期记录，跳过台账写入'
                    )
                else:
                    new_own_rows.append({
                        'Date': evt_date, 'Activity': 'Match',
                        'Price_EUR': round(price_eur, 6), 'Quantity': round(match_qty, 6),
                        'Discount_Ratio': 0.25, 'CNY': round(match_cny, 2),
                        'Cost_CNY': round(match_cny * 0.25, 2),
                    })
                    new_own_rows.append({
                        'Date': evt_date, 'Activity': 'Purchase',
                        'Price_EUR': round(price_eur, 6), 'Quantity': round(purch_qty, 6),
                        'Discount_Ratio': 0.75, 'CNY': round(purch_cny, 2),
                        'Cost_CNY': round(purch_cny * 0.75, 2),
                    })

                warnings.append(
                    f'✅ ESPP 归属 {evt_date}：+{total_qty:.4f}股，成本 ¥{total_cost:,.2f}'
                    + ('（台账已存在，仅更新portfolio）' if already_in_own else '')
                )

                # portfolio 快照始终更新
                mask = (df['Asset_Class'] == 'Company_Stock') & \
                       (df['Name'].str.contains('Own', case=False, na=False) |
                        df['Code'].str.contains('SAP', case=False, na=False))
                if mask.any():
                    df.loc[mask, 'Shares'] = float(df.loc[mask, 'Shares'].values[0]) + total_qty
                    df.loc[mask, 'Net_Cash_Flow'] = \
                        df.loc[mask, 'Net_Cash_Flow'].fillna(0) + total_cost
                else:
                    warnings.append('⚠️ ESPP：未找到 Own SAP 持仓行，portfolio 快照未更新')

            elif sap_type == 'RSU':
                price_eur  = float(fields['股价EUR'])
                fx_rate    = float(fields['汇率EUR'])
                activity   = fields.get('Activity', 'Award')
                shares_str = fields['股数']
                tranches   = [float(s.strip()) for s in shares_str.split(',') if s.strip()]
                total_qty  = sum(tranches)
                total_cny  = round(sum(q * price_eur * fx_rate for q in tranches), 2)
                ncf_amount = total_cny if activity == 'Award' else 0.0

                # 台账重复检查
                already_in_move = evt_date in existing_move_dates
                if already_in_move:
                    warnings.append(
                        f'⏭ RSU {activity} {evt_date}：move_sap.csv 已有该日期记录，跳过台账写入'
                    )
                else:
                    for qty in tranches:
                        new_move_rows.append({
                            'Date': evt_date, 'Activity': activity,
                            'Price_EUR': round(price_eur, 6), 'Quantity': round(qty, 6),
                            'FX_Rate': round(fx_rate, 4),
                            'CNY': round(qty * price_eur * fx_rate, 2),
                        })

                warnings.append(
                    f'✅ RSU {activity} {evt_date}：+{total_qty:.4f}股，FMV ¥{total_cny:,.2f}'
                    + ('（Dividend，NCF=0）' if activity == 'Dividend' else '')
                    + ('（台账已存在，仅更新portfolio）' if already_in_move else '')
                )

                # portfolio 快照始终更新
                mask = (df['Asset_Class'] == 'Company_Stock') & \
                       (~df['Name'].str.contains('Own', case=False, na=False))
                if not mask.any():
                    mask = df['Asset_Class'] == 'Company_Stock'
                if mask.any():
                    df.loc[mask, 'Shares'] = float(df.loc[mask, 'Shares'].values[0]) + total_qty
                    df.loc[mask, 'Net_Cash_Flow'] = \
                        df.loc[mask, 'Net_Cash_Flow'].fillna(0) + ncf_amount
                else:
                    warnings.append('⚠️ RSU：未找到 Move SAP 持仓行，portfolio 快照未更新')

            elif sap_type in ('ESPP_DIVIDEND', 'RSU_DIVIDEND'):
                price_eur = float(fields['股价EUR'])
                fx_rate   = float(fields['汇率EUR'])
                qty       = float(fields['股数'].replace(',', ''))
                cny_val   = round(qty * price_eur * fx_rate, 2)
                is_own    = (sap_type == 'ESPP_DIVIDEND')

                if is_own:
                    already = evt_date in existing_own_dates
                    if not already:
                        new_own_rows.append({
                            'Date': evt_date, 'Activity': 'Dividend',
                            'Price_EUR': round(price_eur, 6), 'Quantity': round(qty, 6),
                            'Discount_Ratio': 1.0, 'CNY': cny_val, 'Cost_CNY': 0.0,
                        })
                    mask = (df['Asset_Class'] == 'Company_Stock') & \
                           df['Name'].str.contains('Own', case=False, na=False)
                else:
                    already = evt_date in existing_move_dates
                    if not already:
                        new_move_rows.append({
                            'Date': evt_date, 'Activity': 'Dividend',
                            'Price_EUR': round(price_eur, 6), 'Quantity': round(qty, 6),
                            'FX_Rate': round(fx_rate, 4), 'CNY': cny_val,
                        })
                    mask = (df['Asset_Class'] == 'Company_Stock') & \
                           (~df['Name'].str.contains('Own', case=False, na=False))
                    if not mask.any():
                        mask = df['Asset_Class'] == 'Company_Stock'

                if mask.any():
                    df.loc[mask, 'Shares'] = float(df.loc[mask, 'Shares'].values[0]) + qty

                src = 'ESPP' if is_own else 'RSU'
                warnings.append(
                    f'✅ {src} Dividend {evt_date}：+{qty:.4f}股，NCF=0'
                    + ('（台账已存在，仅更新portfolio）' if already else '')
                )

        except (KeyError, ValueError) as e:
            warnings.append(f'⚠️ SAP 归属解析失败（{sap_type} {evt_date}）：{e}')
            continue

    # 写入 CSV（仅 confirm 模式）
    if confirm:
        from nav_engine import _atomic_write_csv
        if new_own_rows:
            new_df = pd.DataFrame(new_own_rows)
            existing = pd.read_csv(own_sap_path) if os.path.exists(own_sap_path) else pd.DataFrame()
            _atomic_write_csv(pd.concat([existing, new_df], ignore_index=True), own_sap_path)
            warnings.append(f'✅ own_sap.csv 已写入 {len(new_own_rows)} 行')
        if new_move_rows:
            new_df = pd.DataFrame(new_move_rows)
            existing = pd.read_csv(move_sap_path) if os.path.exists(move_sap_path) else pd.DataFrame()
            _atomic_write_csv(pd.concat([existing, new_df], ignore_index=True), move_sap_path)
            warnings.append(f'✅ move_sap.csv 已写入 {len(new_move_rows)} 行')
    else:
        if new_own_rows or new_move_rows:
            warnings.append(
                f'— dry-run：将写入 {len(new_own_rows)} 行到 own_sap.csv，'
                f'{len(new_move_rows)} 行到 move_sap.csv（确认执行时写入）'
            )

    return df, warnings


# ── 步骤二：道指抵扣（计算建议，不立即执行） ────────────────────────────────

def compute_dow_suggestion(
    transactions: list,
    dow_topup: Optional[float],
    date_str: str,
) -> dict:
    """
    根据本周短信/手动交易的实际纳指/标普投入，计算道指抵扣建议。
    只计算，不写入 dca_prepaid.json——等用户确认后才执行。

    返回：
    {
        'dow_deduct':    float,   # 建议抵扣金额（0表示无需抵扣）
        'sp_actual':     float,
        'ndx_actual':    float,
        'sp_target':     float,
        'ndx_target':    float,
        'ndx_surplus':   float,
        'balance_now':   float,   # 当前预付池余额（未扣减）
        'balance_after': float,   # 抵扣后预付池余额
        'need_topup':    bool,
        'topup_applied': float,   # 本次补仓金额（若有）
        'msg':           str,     # 给用户看的说明
        'warnings':      list,
    }
    """
    warnings = []
    result = {
        'dow_deduct': 0.0, 'sp_actual': 0.0, 'ndx_actual': 0.0,
        'sp_target': 0.0, 'ndx_target': 0.0, 'ndx_surplus': 0.0,
        'balance_now': 0.0, 'balance_after': 0.0,
        'need_topup': False, 'topup_applied': dow_topup or 0.0,
        'msg': '', 'warnings': warnings,
    }

    try:
        from synthetic_sp import (
            load_prepaid, compute_dow_deduct as _compute_dd, topup_prepaid,
        )
        from market_monitor import get_market_data
    except ImportError as e:
        warnings.append(f'⚠️ 道指模块加载失败：{e}')
        return result

    prepaid    = load_prepaid(DATA_DIR)
    balance    = prepaid['dow_prepaid'].get('balance', 0.0)
    result['balance_now'] = balance

    # 若本次有补仓，先更新余额（但还不写入文件）
    if dow_topup and dow_topup > 0:
        balance += dow_topup
        warnings.append(f'✅ 道指补仓 ¥{dow_topup:,.0f}（确认后写入）')

    # 从本周 transactions 汇总 sp_actual / ndx_actual
    # 需要知道每个 Code 对应的 Asset_Class
    raw_df = pd.read_csv(CSV_PATH)
    latest = raw_df[raw_df['Date'] == raw_df['Date'].max()]
    code2class = dict(zip(latest['Code'].astype(str), latest['Asset_Class']))

    sp_actual  = 0.0
    ndx_actual = 0.0
    for t in transactions:
        if t.get('Direction') != '买入':
            continue
        code = str(t.get('Code', ''))
        cls  = code2class.get(code, '')
        if cls == 'US_Blend_Fund':
            sp_actual  += float(t.get('Amount_CNY', 0))
        elif cls == 'US_Growth_Fund':
            ndx_actual += float(t.get('Amount_CNY', 0))

    result['sp_actual']  = sp_actual
    result['ndx_actual'] = ndx_actual

    if sp_actual == 0 and ndx_actual == 0:
        result['msg'] = '本周无美股买入，无需道指抵扣'
        return result

    try:
        market_data = get_market_data(DATA_DIR)
        dd = _compute_dd(
            market_data, prepaid['config'],
            sp_actual=sp_actual, ndx_actual=ndx_actual,
        )
        dow_deduct = dd.get('dow_deduct', 0.0)

        result.update({
            'dow_deduct':    dow_deduct,
            'sp_target':     dd.get('sp_target', 0),
            'ndx_target':    dd.get('ndx_target', 0),
            'ndx_surplus':   dd.get('ndx_surplus', 0),
            'balance_after': round(balance - dow_deduct, 2),
            'need_topup':    round(balance - dow_deduct, 2) <= 0,
        })

        if dow_deduct > 0:
            result['msg'] = (
                f'纳指实际 ¥{ndx_actual:,.0f}（目标 ¥{dd["ndx_target"]:,.0f}），'
                f'超额 ¥{dd["ndx_surplus"]:,.0f}，'
                f'建议道指抵扣 ¥{dow_deduct:,.0f}，'
                f'抵扣后预付池余额 ¥{result["balance_after"]:,.0f}'
                + ('  ⚠️ 余额不足，建议补仓' if result['need_topup'] else '')
            )
        else:
            result['msg'] = (
                f'纳指实际 ¥{ndx_actual:,.0f}（目标 ¥{dd["ndx_target"]:,.0f}），'
                f'未超额，无需道指抵扣'
            )

    except Exception as e:
        warnings.append(f'⚠️ 道指抵扣计算失败：{e}')

    return result


def execute_dow_deduct(suggestion: dict, date_str: str):
    """
    用户确认后，真正执行道指抵扣：写入 dca_prepaid.json。
    """
    from synthetic_sp import load_prepaid, consume_prepaid, topup_prepaid

    # 补仓
    if suggestion.get('topup_applied', 0) > 0:
        topup_prepaid(DATA_DIR, date_str, suggestion['topup_applied'])

    # 抵扣
    if suggestion.get('dow_deduct', 0) > 0:
        consume_prepaid(DATA_DIR, date_str, suggestion['dow_deduct'])



# ── 步骤三：手动交易 ──────────────────────────────────────────────────────────

def step_trades(trades: list, template_df: pd.DataFrame, date_str: str
                ) -> tuple[pd.DataFrame, list, list]:
    warnings, transactions = [], []
    df = template_df.copy()

    for trade in trades:
        direction    = trade['direction']
        code         = trade.get('code', '').strip()
        name_hint    = trade.get('name', '').strip()
        amount       = trade['amount']
        price        = trade.get('price')
        shares_given = trade.get('shares')

        # 匹配逻辑：优先 Code 精确匹配，fallback 到 Name
        mask = pd.Series([False] * len(df))
        if code:
            mask = df['Code'].astype(str) == code
        if not mask.any() and name_hint:
            mask = df['Name'] == name_hint
        if not mask.any() and code:
            # 最后尝试 Name 包含 code（处理部分匹配）
            mask = df['Name'].str.contains(code, case=False, na=False)

        if not mask.any():
            label = code or name_hint
            warnings.append(
                f'⚠️ 找不到标的（Code={code}，名称={name_hint}）'
                f'，请确认 Code 与 portfolio.csv 一致'
            )
            continue

        row_name  = df.loc[mask, 'Name'].values[0]
        row_code  = df.loc[mask, 'Code'].values[0]
        currency  = str(df.loc[mask, 'Currency'].values[0]) if 'Currency' in df.columns else 'CNY'
        cur_price = float(df.loc[mask, 'Current_Price'].values[0])
        is_foreign = currency not in ('CNY', '')

        # ── 份额/成交价校验 ───────────────────────────────────────────
        if is_foreign:
            # 外币资产：份额必填
            if not shares_given:
                warnings.append(
                    f'❌ {name}（{currency}）是外币资产，份额为必填字段，已跳过。'
                    f'请在交易块里填写「份额: <股数>」'
                )
                continue
            shares = shares_given
            # 成交价备注用，不用于份额计算
        else:
            # CNY资产：份额和成交价至少填一个
            if not shares_given and not price:
                warnings.append(
                    f'❌ {row_name} 份额和成交价均未填写，已跳过。'
                    f'请至少填写其中一个。'
                )
                continue
            if shares_given and price:
                implied_amount = shares_given * price
                diff_pct = abs(implied_amount - amount) / amount if amount > 0 else 0
                if diff_pct > 0.01:
                    warnings.append(
                        f'⚠️ {row_name} 一致性校验：份额×成交价={implied_amount:,.2f}，'
                        f'填写金额={amount:,.2f}，差异{diff_pct*100:.1f}%，请检查'
                    )
                shares = shares_given
            elif shares_given:
                shares = shares_given
            else:
                shares = amount / price

        # ── 更新 DataFrame ────────────────────────────────────────────
        old_shares = float(df.loc[mask, 'Shares'].values[0])

        if direction == '买入':
            ncf = amount  # 买入：金额已含手续费
            df.loc[mask, 'Net_Cash_Flow'] = df.loc[mask, 'Net_Cash_Flow'].fillna(0) + ncf
            df.loc[mask, 'Shares']        = old_shares + shares
            df.loc[mask, 'Total_Value']   = df.loc[mask, 'Shares'] * cur_price
            _adj_cash(df, -amount)

        elif direction in ('卖出', '赎回'):
            ncf = -amount  # 卖出/赎回：金额已是到手（扣费后）
            df.loc[mask, 'Net_Cash_Flow'] = df.loc[mask, 'Net_Cash_Flow'].fillna(0) + ncf
            df.loc[mask, 'Shares']        = max(0, old_shares - shares)
            df.loc[mask, 'Total_Value']   = df.loc[mask, 'Shares'] * cur_price
            _adj_cash(df, amount)

        # 成交价统一存 CNY 单价（Amount_CNY / 份额）
        price_cny = round(amount / shares, 6) if shares and shares > 0 else 0.0
        transactions.append({
            'Date':           date_str,
            'Asset_Class':    str(df.loc[mask, 'Asset_Class'].values[0]),
            'Platform':       str(df.loc[mask, 'Platform'].values[0]) if 'Platform' in df.columns else '',
            'Name':           row_name,
            'Code':           row_code,
            'Type':           direction,
            'Amount_CNY':     round(amount, 2),
            'Price':          price_cny,
            'Price_Currency': 'CNY',
            'Fee_CNY':        0.0,
            'Source':         'Manual',
        })

        # 预览提示
        warnings.append(
            f'✅ {direction} {row_name}：¥{amount:,.0f}，{shares:,.4f}份'
            f'，CNY单价 ¥{price_cny:.4f}'
        )

    return df, transactions, warnings


def _adj_cash(df: pd.DataFrame, delta: float):
    mask = df['Asset_Class'] == 'Cash'
    if mask.any():
        df.loc[mask, 'Total_Value'] = float(df.loc[mask, 'Total_Value'].values[0]) + delta
        df.loc[mask, 'Net_Cash_Flow'] = (
            df.loc[mask, 'Net_Cash_Flow'].fillna(0) + delta
        )


# ── 步骤四：净值拉取（带退避重试） ──────────────────────────────────────────

def _fetch_one_with_retry(code: str, name: str) -> tuple[Optional[dict], str]:
    """
    拉取单个标的价格，失败后按退避策略重试。
    返回 (result_dict_or_None, status_msg)
    result_dict 包含 price, exchange_rate 等字段。
    """
    import time
    from price_fetcher import _route

    last_err = ''
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay > 0:
            print(f'  ⏳ {name}（{code}）拉取失败，{delay}s 后重试（第{attempt}次）...',
                  flush=True)
            time.sleep(delay)

        result = _route(code)
        status = result.get('status', 'error')
        price  = result.get('price')

        if status == 'ok' and price is not None:
            return result, 'ok'

        if status == 'manual':
            return None, 'manual'

        last_err = result.get('msg', 'unknown error')

    return None, f'重试耗尽：{last_err}'


def step_prices(template_df: pd.DataFrame, manual_prices: dict,
                interactive: bool = True) -> tuple[pd.DataFrame, list, list]:
    """
    拉取所有标的价格：
    - 手动填写标的（月月宝/季季宝）：直接用 manual_prices
    - 其他标的：自动拉取，失败时退避重试，最终失败可交互填写
    返回 (updated_df, warnings, failed_codes)
    failed_codes：仍然失败且未被填写的标的列表（供 Skill 交互提示）
    """
    warnings  = []
    failed    = []  # [(name, code)] 最终失败的
    df        = template_df.copy()

    # ── 手动净值覆盖（月月宝/季季宝）────────────────────────────────────────
    for name, nav in manual_prices.items():
        code = MANUAL_PRICE_ASSETS.get(name, '')
        mask = (df['Name'] == name) | (df['Code'] == code)
        if not mask.any():
            warnings.append(f'⚠️ 手动净值：找不到标的"{name}"，已跳过')
            continue
        df.loc[mask, 'Current_Price'] = nav
        df.loc[mask, 'Total_Value'] = (
            df.loc[mask, 'Shares'] * nav *
            df.loc[mask, 'Exchange_Rate'].fillna(1.0)
        )
        warnings.append(f'✅ 手动净值：{name} = {nav}')

    # ── 自动拉取（排除手动标的）─────────────────────────────────────────────
    manual_codes = set(MANUAL_PRICE_ASSETS.values())
    latest_date  = df['Date'].max() if 'Date' in df.columns else None

    # 逐标的拉取（限速：每个标的间隔 0.3s，避免 rate limit）
    import time
    rows_to_fetch = df[~df['Code'].isin(manual_codes)].drop_duplicates('Code')

    ok_count = 0
    for _, row in rows_to_fetch.iterrows():
        code = str(row['Code'])
        name = str(row['Name'])

        if code == 'CASH':
            continue

        result, status_msg = _fetch_one_with_retry(code, name)

        if result is not None:
            mask  = df['Code'] == code
            price = result['price']
            rate  = result.get('exchange_rate')
            df.loc[mask, 'Current_Price'] = price
            if rate:
                df.loc[mask, 'Exchange_Rate'] = rate
            df.loc[mask, 'Total_Value'] = (
                df.loc[mask, 'Shares'] *
                df.loc[mask, 'Current_Price'] *
                df.loc[mask, 'Exchange_Rate'].fillna(1.0)
            )
            ok_count += 1
        elif status_msg == 'manual':
            warnings.append(f'⚠️ {name}（{code}）无自动价格来源，使用上期价格')
        else:
            failed.append((name, code, status_msg))
            warnings.append(f'❌ {name}（{code}）拉取失败，使用上期价格')

    warnings.insert(0, f'✅ 自动拉取价格：{ok_count} 个成功，{len(failed)} 个失败')

    # ── 交互填写（仅 CLI 交互模式）──────────────────────────────────────────
    if interactive and failed:
        print('\n以下标的价格拉取失败，请手动输入（直接回车跳过，使用上期价格）：')
        still_failed = []
        for name, code, err in failed:
            cur = float(df.loc[df['Code'] == code, 'Current_Price'].values[0])
            val = input(f'  {name}（{code}，上期价格 {cur}）当前净值/价格：').strip()
            if val:
                try:
                    price = float(val)
                    mask = df['Code'] == code
                    df.loc[mask, 'Current_Price'] = price
                    df.loc[mask, 'Total_Value'] = (
                        df.loc[mask, 'Shares'] * price *
                        df.loc[mask, 'Exchange_Rate'].fillna(1.0)
                    )
                    warnings.append(f'✅ 手动输入：{name} = {price}')
                except ValueError:
                    warnings.append(f'⚠️ {name} 输入格式错误，使用上期价格')
                    still_failed.append((name, code))
            else:
                warnings.append(f'— {name} 跳过，使用上期价格')
                still_failed.append((name, code))
        failed = still_failed

    return df, warnings, failed


# ── 对账校验 ──────────────────────────────────────────────────────────────────

def reconcile(snapshot_df: pd.DataFrame, transactions: list
              ) -> tuple[bool, list]:
    messages = []

    buy_total  = sum(
        t['Amount_CNY'] + t.get('Fee_CNY', 0)
        for t in transactions if t.get('Direction') == '买入'
    )
    sell_total = sum(
        t['Amount_CNY'] - t.get('Fee_CNY', 0)
        for t in transactions if t.get('Direction') in ('卖出', '赎回')
    )

    if buy_total == 0 and sell_total == 0:
        messages.append('✅ 本周无新交易，对账通过')
        return True, messages

    snapshot_ncf  = float(snapshot_df['Net_Cash_Flow'].fillna(0).sum())
    expected_ncf  = buy_total - sell_total
    diff          = abs(snapshot_ncf - expected_ncf)
    tol           = max(50.0, abs(expected_ncf) * 0.001)

    if diff <= tol:
        messages.append(
            f'✅ 对账通过：买入 ¥{buy_total:,.0f}，卖出 ¥{sell_total:,.0f}，'
            f'差异 ¥{diff:.0f}（容忍 ¥{tol:.0f}）'
        )
        return True, messages
    else:
        messages.append(
            f'❌ 对账不平：snapshot NCF ¥{snapshot_ncf:,.0f}，'
            f'预期 ¥{expected_ncf:,.0f}，差异 ¥{diff:,.0f}'
        )
        messages.append('   检查：①短信是否漏粘贴 ②手动交易金额是否正确')
        return False, messages


# ── 预览报告 ──────────────────────────────────────────────────────────────────

def build_preview(date_str, snapshot_df, transactions,
                  date_msgs, price_warns, sms_warns,
                  dow_suggestion, trade_warns, recon_ok, recon_msgs,
                  sap_warns=None, failed_codes=None) -> str:
    total = snapshot_df['Total_Value'].sum()
    cash  = float(snapshot_df.loc[
        snapshot_df['Asset_Class'] == 'Cash', 'Total_Value'
    ].sum())

    lines = [
        f'## Weekly Update 预览 — {date_str}',
        '',
        '### 📅 日期校验',
    ]
    for m in date_msgs: lines.append(f'  {m}')

    lines += ['', '### 📊 快照预览',
              f'  总资产：¥{total:,.0f}　现金：¥{cash:,.0f}', '']

    lines.append('### 📱 步骤一：短信解析')
    sms_tx = [t for t in transactions if t.get('Source') == 'SMS']
    if sms_tx:
        for t in sms_tx:
            lines.append(
                f'  ✅ {t["Name"]}：买入 ¥{t["Amount_CNY"]:,.0f}'
                + (f'，+{t["Shares_Delta"]:.4f}份' if t.get("Shares_Delta") else '')
            )
    else:
        lines.append('  — 无短信定投')
    for w in sms_warns: lines.append(f'  {w}')

    lines += ['', '### 💼 步骤一c：SAP 归属']
    if sap_warns:
        for w in sap_warns: lines.append(f'  {w}')
    else:
        lines.append('  — 本周无 SAP 归属')

    lines += ['', '### 📊 步骤二：道指抵扣']
    dow_deduct = dow_suggestion.get('dow_deduct', 0)
    if dow_deduct > 0:
        lines.append(f'  💡 {dow_suggestion["msg"]}')
        lines.append(f'  → 确认执行时将询问是否抵扣')
    else:
        lines.append(f'  — {dow_suggestion.get("msg", "无需抵扣")}')
    for w in dow_suggestion.get('warnings', []):
        lines.append(f'  {w}')

    lines += ['', '### 🔄 步骤三：手动交易']
    manual_tx = [t for t in transactions if t.get('Source') == 'Manual']
    if not manual_tx:
        lines.append('  — 无手动交易')
    for w in trade_warns: lines.append(f'  {w}')

    lines += ['', '### 💹 步骤四：净值拉取']
    for w in price_warns: lines.append(f'  {w}')

    lines += ['', '### ✅ 对账校验']
    for m in recon_msgs: lines.append(f'  {m}')

    changed = snapshot_df[snapshot_df['Net_Cash_Flow'].fillna(0) != 0]
    if not changed.empty:
        lines += ['', '### 📋 持仓变化（有 NCF 的标的）']
        for _, row in changed.iterrows():
            lines.append(
                f'  {row["Name"]}：NCF {row["Net_Cash_Flow"]:+,.0f}，'
                f'市值 ¥{row["Total_Value"]:,.0f}'
            )

    if not recon_ok:
        lines.append('**❌ 对账不平，请修正后重新运行。**')
        return '\n'.join(lines)

    if failed_codes:
        lines.append(
            f'**⚠️ {len(failed_codes)} 个标的使用上期价格（'
            + '、'.join(n for n, _, _ in failed_codes)
            + '），总资产可能略有偏差。**'
        )
    lines.append('**✅ 预览通过，回复"确认执行"保存快照。**')
    return '\n'.join(lines)


# ── 写入 & 备份重置 ───────────────────────────────────────────────────────────

def save_and_reset(date_str: str, snapshot_df: pd.DataFrame,
                   transactions: list, input_path: str,
                   cash_delta: Optional[float] = None,
                   son_template: Optional[pd.DataFrame] = None) -> str:
    """写入 CSV，归档输入文件，重置为空模板。"""

    from nav_engine import _atomic_write_csv

    # 外部资金变动
    if cash_delta is not None and cash_delta != 0:
        mask = snapshot_df['Asset_Class'] == 'Cash'
        snapshot_df.loc[mask, 'Total_Value'] = (
            float(snapshot_df.loc[mask, 'Total_Value'].values[0]) + cash_delta
        )
        snapshot_df.loc[mask, 'Net_Cash_Flow'] = (
            snapshot_df.loc[mask, 'Net_Cash_Flow'].fillna(0) + cash_delta
        )

    # 主基金写入
    snapshot_df.insert(0, 'Date', date_str)
    existing = pd.read_csv(CSV_PATH)
    combined = pd.concat([existing, snapshot_df], ignore_index=True)
    _atomic_write_csv(combined, CSV_PATH)

    # Son Fund 写入（若有模板且净值已刷新）
    son_msg = ''
    if son_template is not None and os.path.exists(SON_CSV_PATH):
        # Son Fund 也需要刷新净值（南方纳指100 I类，走 fetch_latest_prices）
        try:
            from price_fetcher import fetch_latest_prices
            price_map = fetch_latest_prices(son_template, data_dir=DATA_DIR)
            for code, info in price_map.items():
                price = info.get('price')
                if price:
                    mask = son_template['Code'].astype(str) == str(code)
                    if mask.any():
                        son_template.loc[mask, 'Current_Price'] = price
                        son_template.loc[mask, 'Total_Value'] = (
                            son_template.loc[mask, 'Shares'] * price
                        )
        except Exception:
            pass  # 价格失败用上期价格

        son_template.insert(0, 'Date', date_str)
        son_existing = pd.read_csv(SON_CSV_PATH)
        son_combined = pd.concat([son_existing, son_template], ignore_index=True)
        _atomic_write_csv(son_combined, SON_CSV_PATH)
        son_total = float(son_template['Total_Value'].sum())
        son_msg = f'\n✅ Son Fund 快照已保存：¥{son_total:,.0f}'

    # transaction.csv — 走 _atomic_write_csv 获得备份保护
    if transactions:
        tx_df = pd.DataFrame(transactions)
        if os.path.exists(TX_PATH):
            tx_df = pd.concat([pd.read_csv(TX_PATH), tx_df], ignore_index=True)
        _atomic_write_csv(tx_df, TX_PATH)

    # 归档输入文件
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    archive_path = os.path.join(ARCHIVE_DIR, f'_weekly_input_{date_str}.md')
    shutil.copy2(input_path, archive_path)

    # 重置输入文件
    _reset_input(input_path)

    # Obsidian 同步
    obs_msg = ''
    try:
        from nav_engine import (
            compute_fund_nav, compute_class_nav, compute_allocation,
            compute_cost_basis, compute_xirr, compute_sharpe, compute_calmar,
        )
        from obsidian_sync import sync_to_obsidian

        updated = pd.read_csv(CSV_PATH)
        fund_nav  = compute_fund_nav(updated)
        alloc     = compute_allocation(updated)
        cost      = compute_cost_basis(updated)
        class_nav = compute_class_nav(updated)
        xirr      = compute_xirr(updated)
        sharpe    = compute_sharpe(fund_nav)
        calmar    = compute_calmar(fund_nav)
        navs      = fund_nav['NAV'].astype(float)
        mdd       = float(((navs - navs.cummax()) / navs.cummax()).min())
        first     = updated['Date'].min()
        inv       = float(updated[updated['Date'] == first]['Total_Value'].sum())
        infl      = float(updated[
            (updated['Date'] > first) &
            (updated['Asset_Class'].isin(['Cash', 'Company_Stock'])) &
            (updated['Net_Cash_Flow'] > 0)
        ]['Net_Cash_Flow'].sum())

        son_csv = os.path.join(DATA_DIR, 'son', 'portfolio.csv')
        son_nav_df = son_cost_df = None
        if os.path.exists(son_csv):
            sdf = pd.read_csv(son_csv)
            son_nav_df  = compute_fund_nav(sdf)
            son_cost_df = compute_cost_basis(sdf)

        sync_to_obsidian(
            date_str=date_str, data_dir=DATA_DIR,
            fund_nav_df=fund_nav, allocation_df=alloc, cost_basis_df=cost,
            xirr=xirr, max_drawdown=mdd, raw_df=updated,
            class_nav_dict=class_nav, sharpe=sharpe, calmar=calmar,
            total_invested=inv + infl,
            son_nav_df=son_nav_df, son_cost_df=son_cost_df,
        )
        obs_msg = '✅ Obsidian dashboard 已同步'
    except Exception as e:
        obs_msg = f'⚠️ Obsidian 同步失败（快照已保存）：{e}'

    return (
        f'✅ 快照已保存：{date_str}\n'
        f'{son_msg}\n'
        f'✅ 输入文件已归档：{os.path.basename(archive_path)}\n'
        f'✅ _weekly_input.md 已重置，等待下周填写\n'
        f'{obs_msg}'
    )


def _reset_input(path: str):
    """重置 _weekly_input.md 为空模板（保留结构，清空内容）。"""
    template = """\
---
date: ""
status: pending
---

# Weekly Update Input
> 填好后，在 Claude Code 里执行 `/weekly-update`

---

## 📅 记账日期
date:

---

## 📱 步骤一：短信区


---

## 📊 步骤二：道指抵扣
<!-- Skill 自动计算，无需手动填 -->
<!-- 只有本周买入了道指ETF实物补仓时，填金额 -->
道指补仓:

---

## 🔄 步骤三：手动交易登记
<!-- 买入金额=实际支出含手续费，卖出/赎回金额=实际到手 -->
<!-- CNY资产：成交价和份额至少填一个；外币资产：份额必填 -->
<!-- Code格式：港股=HK0700，A股=601838，场外基金=021000，SAP=SAP.DE -->

| 方向 | Code | 名称 | 金额CNY | 成交价 | 份额 |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |

---

## 💰 步骤四：固收净值（手动填，2个标的）
月月宝:
季季宝:

---

## 💵 外部资金变动（仅在有外部入金/出金时填写）


---

## 💼 SAP 归属（无归属本周则留空）
<!-- ESPP→own_sap.csv，RSU→move_sap.csv，Dividend→NCF=0 -->
<!-- 税率/汇率EUR列：ESPP填税率(如33)，RSU/Dividend填汇率(如7.85) -->

| 类型 | 日期 | 股价EUR | 税率/汇率EUR | Match CNY | Match股数 | Purchase CNY | Purchase股数 | RSU股数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |

---

## 👶 Son Fund 短信区


---

## 📝 备注


"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(template)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(input_path: str = INPUT_PATH, confirm: bool = False) -> str:
    # 1. 解析输入
    inp = parse_weekly_input(input_path)

    # status 检查
    if inp['status'].startswith('done'):
        return (
            f'⚠️ _weekly_input.md 的 status 为 {inp["status"]}，'
            f'本周似乎已执行过。\n'
            f'如需重新执行，请先删除本周快照（Dashboard → 历史快照管理），'
            f'再将 status 改回 pending。'
        )

    if inp['errors']:
        return '## ❌ 输入有误，请修正后重试\n\n' + '\n'.join(inp['errors'])

    date_str = inp['date']

    # 2. 日期校验
    date_ok, date_msgs = validate_date(date_str, CSV_PATH)
    if not date_ok:
        return '## ❌ 日期校验失败\n\n' + '\n'.join(date_msgs)

    # 3. 构建主基金模板（上次快照，NCF 归零）
    raw_df = pd.read_csv(CSV_PATH)
    raw_df['Date'] = pd.to_datetime(raw_df['Date']).dt.strftime('%Y-%m-%d')
    latest_date = raw_df['Date'].max()
    template = raw_df[raw_df['Date'] == latest_date].copy()
    template['Net_Cash_Flow'] = 0.0

    # 3b. 构建 Son Fund 模板（若存在）
    son_template = None
    son_sms_warns = []
    son_sms_tx = []
    if os.path.exists(SON_CSV_PATH):
        son_raw = pd.read_csv(SON_CSV_PATH)
        son_raw['Date'] = pd.to_datetime(son_raw['Date']).dt.strftime('%Y-%m-%d')
        son_latest = son_raw[son_raw['Date'] == son_raw['Date'].max()].copy()
        son_latest['Net_Cash_Flow'] = 0.0
        son_template = son_latest

    all_transactions = []

    # 步骤一：短信解析（主基金）
    template, sms_tx, sms_warns = step_sms(
        inp['sms_lines'], template, date_str, interactive=confirm
    )
    all_transactions += sms_tx

    # 步骤一b：Son Fund 短信解析
    if son_template is not None and inp['son_sms_lines']:
        son_template, son_sms_tx, son_sms_warns = step_sms(
            inp['son_sms_lines'], son_template, date_str, interactive=confirm
        )

    # 步骤一c：SAP 归属
    sap_warns = []
    if inp['sap_events']:
        template, sap_warns = step_sap(inp['sap_events'], template, date_str, confirm=confirm)

    # 步骤二：道指抵扣——计算建议（不执行）
    dow_suggestion = compute_dow_suggestion(all_transactions, inp['dow_topup'], date_str)

    # 步骤三：手动交易
    template, manual_tx, trade_warns = step_trades(inp['trades'], template, date_str)
    all_transactions += manual_tx

    # 步骤四：净值拉取
    template, price_warns, failed_codes = step_prices(
        template, inp['manual_prices'], interactive=confirm
    )

    # 对账
    recon_ok, recon_msgs = reconcile(template, all_transactions)

    # 预览报告
    preview = build_preview(
        date_str, template, all_transactions,
        date_msgs, price_warns, sms_warns,
        dow_suggestion, trade_warns, recon_ok, recon_msgs,
        sap_warns=sap_warns, failed_codes=failed_codes,
    )

    # Son Fund 预览附加
    if son_template is not None:
        son_total = float(son_template['Total_Value'].sum())
        preview += f'\n\n### 👶 Son Fund\n  总资产：¥{son_total:,.0f}'
        if son_sms_tx:
            for t in son_sms_tx:
                preview += f'\n  ✅ {t["Name"]}：买入 ¥{t["Amount_CNY"]:,.0f}'
        elif inp['son_sms_lines']:
            preview += '\n  ⚠️ 短信解析未匹配到任何记录'
        else:
            preview += '\n  — 本周无 Son Fund 定投'
        for w in son_sms_warns:
            preview += f'\n  {w}'

    # 写入
    if confirm:
        if not recon_ok:
            return preview + '\n\n❌ 对账不平，已拒绝写入，请修正后重试。'

        # 道指抵扣交互确认
        actual_dow_deduct = 0.0
        if dow_suggestion['dow_deduct'] > 0:
            suggested = dow_suggestion['dow_deduct']
            balance   = dow_suggestion['balance_now'] + dow_suggestion.get('topup_applied', 0)
            print(f'\n📊 道指抵扣建议：{dow_suggestion["msg"]}')
            print(f'   建议金额 ¥{suggested:,.0f}（取整到10元），预付池余额 ¥{balance:,.0f}')
            print(f'   直接回车 = 接受建议金额；输入数字 = 自定义金额；0 或 n = 跳过')
            ans = input(f'抵扣金额（建议 ¥{suggested:,.0f}）：').strip()

            if ans in ('', 'y', 'yes', '是'):
                actual_dow_deduct = suggested
            elif ans in ('0', 'n', 'no', '否', '跳过'):
                print('— 跳过道指抵扣')
            else:
                try:
                    custom = float(ans.replace(',', ''))
                    if custom < 0:
                        print('⚠️ 金额不能为负，已跳过')
                    elif custom > balance:
                        print(f'⚠️ ¥{custom:,.0f} 超过预付池余额 ¥{balance:,.0f}，已跳过')
                    else:
                        actual_dow_deduct = round(custom / 10) * 10  # 取整到10元
                        print(f'  自定义金额取整为 ¥{actual_dow_deduct:,.0f}')
                except ValueError:
                    print('⚠️ 输入格式错误，已跳过道指抵扣')

            if actual_dow_deduct > 0:
                # 写入 dca_prepaid.json
                from synthetic_sp import consume_prepaid, topup_prepaid
                if dow_suggestion.get('topup_applied', 0) > 0:
                    topup_prepaid(DATA_DIR, date_str, dow_suggestion['topup_applied'])
                consume_prepaid(DATA_DIR, date_str, actual_dow_deduct)
                # 在 snapshot 里记录道指ETF NCF
                dow_mask = template['Code'] == '513400'
                if dow_mask.any():
                    template.loc[dow_mask, 'Net_Cash_Flow'] = (
                        template.loc[dow_mask, 'Net_Cash_Flow'].fillna(0) +
                        actual_dow_deduct
                    )
                print(f'✅ 道指抵扣 ¥{actual_dow_deduct:,.0f} 已执行')

        elif dow_suggestion.get('topup_applied', 0) > 0:
            # 无需抵扣，但有补仓
            from synthetic_sp import topup_prepaid
            topup_prepaid(DATA_DIR, date_str, dow_suggestion['topup_applied'])
            print(f'✅ 道指补仓 ¥{dow_suggestion["topup_applied"]:,.0f} 已写入')

        save_msg = save_and_reset(
            date_str, template, all_transactions,
            input_path, inp['cash_delta'],
            son_template=son_template,
        )
        return preview + f'\n\n{save_msg}'

    return preview


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FamilyFund Weekly Update CLI')
    parser.add_argument('--input',   default=INPUT_PATH)
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    print(run(args.input, args.confirm))
