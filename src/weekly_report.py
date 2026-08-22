"""weekly_report.py — 每周快照保存后自动生成 Markdown 周报。

生成路径：$FAMILYFUND_DATA/weekly_reports/YYYY-MM-DD.md

报告结构：
  1. 总览（总资产、净值、外部入金、主动买入/卖出）
  2. 本周交易（按类别汇总）
  3. 有交易标的变化（市值变化 = 价格涨跌 + 加仓贡献）
  4. 无交易标的变化（按类别）
  5. 公司股票 SAP（独立节：价格涨跌 / ESPP归属 / 汇率影响）
  6. 合成标普β状态
  7. 资产配置变化
"""

import os
import json
import pandas as pd


CLASS_LABEL = {
    'US_Blend_Fund':  '美股宽基',
    'US_Growth_Fund': '美股成长',
    'CN_Index_Fund':  'A股宽基',
    'Gold':           '黄金',
    'Fixed_Income':   '固定收益',
    'ETF_Stock':      '个股/ETF',
    'Company_Stock':  '公司股票',
    'Cash':           '现金',
}


def _fmt(val: float, prefix='¥') -> str:
    return f'{prefix}{val:,.0f}'


def generate_weekly_report(
    data_dir: str,
    this_week: str,
    last_week: str | None = None,
) -> str:
    """生成周报 Markdown 并写入 weekly_reports/，返回写入路径。

    Args:
        data_dir:  $FAMILYFUND_DATA 路径
        this_week: 本周快照日期，格式 YYYY-MM-DD
        last_week: 上周快照日期（None 则自动取倒数第二个日期）

    Returns:
        写入的文件路径
    """
    from nav_engine import load_portfolio, compute_fund_nav

    df = load_portfolio(os.path.join(data_dir, 'portfolio.csv'))
    tx_path = os.path.join(data_dir, 'transaction.csv')
    tx_all = pd.read_csv(tx_path) if os.path.exists(tx_path) else pd.DataFrame()
    if not tx_all.empty and 'Code' in tx_all.columns:
        tx_all['Code'] = tx_all['Code'].astype(str).str.zfill(6).where(
            tx_all['Code'].astype(str).str.match(r'^\d{1,6}$'),
            tx_all['Code'].astype(str)
        )

    # 确定上周日期
    if last_week is None:
        dates = sorted(df['Date'].unique())
        idx = dates.index(this_week) if this_week in dates else -1
        last_week = dates[idx - 1] if idx > 0 else None

    tw = df[df['Date'] == this_week].copy()
    lw = df[df['Date'] == last_week].copy() if last_week else pd.DataFrame()

    total_tw = tw['Total_Value'].sum()
    total_lw = lw['Total_Value'].sum() if not lw.empty else 0.0
    delta_total = total_tw - total_lw

    # 单位净值
    fund_nav_df = compute_fund_nav(df)
    nav_row_tw = fund_nav_df[fund_nav_df['Date'] == this_week]
    nav_row_lw = fund_nav_df[fund_nav_df['Date'] == last_week] if last_week else pd.DataFrame()
    nav_tw = float(nav_row_tw['NAV'].iloc[0]) if not nav_row_tw.empty else 0.0
    nav_lw = float(nav_row_lw['NAV'].iloc[0]) if not nav_row_lw.empty else 0.0
    ncf_tw = float(nav_row_tw['Net_Cash_Flow'].iloc[0]) if not nav_row_tw.empty else 0.0
    ncf_lw = float(nav_row_lw['Net_Cash_Flow'].iloc[0]) if not nav_row_lw.empty else 0.0

    # 本周交易
    cutoff = last_week + 'T' if last_week else '1970-01-01'
    tx_week = tx_all[tx_all['Date'] > last_week].copy() if last_week and not tx_all.empty else tx_all.copy()
    # 过滤掉 SAP（不算主动交易）
    tx_week_noSAP = tx_week[tx_week['Code'] != 'SAP.DE'] if not tx_week.empty else tx_week
    tx_buy  = tx_week_noSAP[tx_week_noSAP['Type'] == '买入']['Amount_CNY'].sum() if not tx_week_noSAP.empty else 0.0
    tx_sell = tx_week_noSAP[tx_week_noSAP['Type'] == '卖出']['Amount_CNY'].sum() if not tx_week_noSAP.empty else 0.0
    traded_codes = set(tx_week_noSAP['Code'].dropna().astype(str)) if not tx_week_noSAP.empty else set()

    # 合并本周/上周数据
    if not lw.empty:
        merged = tw.merge(
            lw[['Code', 'Total_Value', 'Shares', 'Current_Price', 'Exchange_Rate']].rename(
                columns={'Total_Value': 'tv_lw', 'Shares': 'shares_lw',
                         'Current_Price': 'price_lw', 'Exchange_Rate': 'fx_lw'}),
            on='Code', how='left')
    else:
        merged = tw.copy()
        merged['tv_lw'] = 0.0
        merged['shares_lw'] = 0.0
        merged['price_lw'] = 0.0
        merged['fx_lw'] = merged['Exchange_Rate']

    merged['tv_lw']     = merged['tv_lw'].fillna(0.0)
    merged['shares_lw'] = merged['shares_lw'].fillna(0.0)
    merged['price_lw']  = merged['price_lw'].fillna(0.0)
    merged['fx_lw']     = merged['fx_lw'].fillna(merged['Exchange_Rate'])
    merged['delta_tv']     = merged['Total_Value'] - merged['tv_lw']
    merged['delta_shares'] = merged['Shares'] - merged['shares_lw']
    # 价格涨跌（含汇率变动）= 上周份额 × (本周价×本周汇率 - 上周价×上周汇率)
    merged['price_gain']  = merged['shares_lw'] * (
        merged['Current_Price'] * merged['Exchange_Rate'] -
        merged['price_lw'] * merged['fx_lw'])
    merged['buy_contrib'] = merged['delta_tv'] - merged['price_gain']

    # 合成标普β
    dca_path = os.path.join(data_dir, 'dca_prepaid.json')
    dca = None
    if os.path.exists(dca_path):
        with open(dca_path, encoding='utf-8') as f:
            dca = json.load(f)

    lines = []

    # ── 1. 总览 ──
    lines += [f'# 周报 {this_week}\n']
    lines += ['## 总览\n']
    lines += ['| 指标 | 本周 | 上周 | 变化 |']
    lines += ['|------|------|------|------|']
    lines += [f'| 总资产 | {_fmt(total_tw)} | {_fmt(total_lw)} | {delta_total:+,.0f} ({delta_total/total_lw*100:+.2f}%) |' if total_lw else f'| 总资产 | {_fmt(total_tw)} | — | — |']
    if nav_lw:
        lines += [f'| 单位净值 | {nav_tw:.4f} | {nav_lw:.4f} | {nav_tw-nav_lw:+.4f} ({(nav_tw/nav_lw-1)*100:+.2f}%) |']
    else:
        lines += [f'| 单位净值 | {nav_tw:.4f} | — | — |']
    _ncf_delta = ncf_tw - ncf_lw
    _ncf_lw_str = _fmt(ncf_lw) if ncf_lw else '—'
    lines += [f'| 外部入金（Cash+SAP口径） | {_fmt(ncf_tw)} | {_ncf_lw_str} | {_ncf_delta:+,.0f} |']
    lines += [f'| 本周主动买入 | {_fmt(tx_buy)} | — | — |']
    if tx_sell > 0:
        lines += [f'| 本周卖出 | {_fmt(tx_sell)} | — | — |']
    lines += ['']

    # ── 2. 本周交易（按类别汇总）──
    lines += ['## 本周交易（按类别）\n']
    if not tx_week_noSAP.empty:
        lines += ['| 类别 | 标的 | Code | 买入 | 卖出 | 笔数 |']
        lines += ['|------|------|------|------|------|------|']
        for (cls, name, code), grp in tx_week_noSAP.groupby(['Asset_Class', 'Name', 'Code']):
            buy  = grp[grp['Type'] == '买入']['Amount_CNY'].sum()
            sell = grp[grp['Type'] == '卖出']['Amount_CNY'].sum()
            n    = len(grp)
            label  = CLASS_LABEL.get(cls, cls)
            buy_s  = _fmt(buy)  if buy  > 0 else '—'
            sell_s = _fmt(sell) if sell > 0 else '—'
            lines += [f'| {label} | {name} | {code} | {buy_s} | {sell_s} | {n}笔 |']
        sell_summary = f'**{_fmt(tx_sell)}**' if tx_sell > 0 else '—'
        lines += [f'| **合计** | — | — | **{_fmt(tx_buy)}** | {sell_summary} | {len(tx_week_noSAP)}笔 |']
    else:
        lines += ['_本周无交易记录_']
    lines += ['']

    # ── 3. 有交易标的变化 ──
    traded = merged[merged['Code'].isin(traded_codes)]
    if not traded.empty:
        lines += ['## 有交易标的变化\n']
        lines += ['| 标的 | Code | 本周市值 | 市值变化 | 价格涨跌 | 加仓贡献 | 持仓变化 |']
        lines += ['|------|------|---------|---------|---------|---------|---------|']
        for _, r in traded.iterrows():
            shares_str = f"{r['delta_shares']:+.2f}份" if abs(r['delta_shares']) > 0.001 else '—'
            if r['tv_lw'] == 0:
                lines += [f"| {r['Name']} | {r['Code']} | {_fmt(r['Total_Value'])} "
                          f"| {r['delta_tv']:+,.0f} | 新建仓 | {r['buy_contrib']:+,.0f} | {shares_str} |"]
            else:
                lines += [f"| {r['Name']} | {r['Code']} | {_fmt(r['Total_Value'])} "
                          f"| {r['delta_tv']:+,.0f} | {r['price_gain']:+,.0f} | {r['buy_contrib']:+,.0f} | {shares_str} |"]
        lines += ['']
        lines += ['> 市值变化 = 价格涨跌 + 加仓贡献\n']

    # ── 4. 无交易标的变化（按类别，不含 SAP/Cash）──
    untrade = merged[
        (~merged['Code'].isin(traded_codes)) &
        (~merged['Asset_Class'].isin(['Cash', 'Company_Stock']))]
    if not untrade.empty:
        lines += ['## 无交易标的变化（按类别）\n']
        lines += ['| 类别 | 本周市值 | 上周市值 | 价格涨跌 |']
        lines += ['|------|---------|---------|---------|']
        for cls, grp in untrade.groupby('Asset_Class'):
            tv   = grp['Total_Value'].sum()
            tv_l = grp['tv_lw'].sum()
            gain = grp['price_gain'].sum()
            gain_pct = gain / tv_l * 100 if tv_l > 0 else 0
            lines += [f'| {CLASS_LABEL.get(cls, cls)} | {_fmt(tv)} | {_fmt(tv_l)} | {gain:+,.0f} ({gain_pct:+.1f}%) |']
        lines += ['']

    # ── 5. 公司股票 SAP（独立节）──
    sap_tw_rows = tw[tw['Code'] == 'SAP.DE']
    sap_lw_rows = lw[lw['Code'] == 'SAP.DE'] if not lw.empty else pd.DataFrame()
    if not sap_tw_rows.empty:
        sap_tw = sap_tw_rows.iloc[0]
        sap_lw = sap_lw_rows.iloc[0] if not sap_lw_rows.empty else None

        lines += ['## 公司股票（SAP）\n']
        lines += ['| 项目 | 数值 |']
        lines += ['|------|------|']

        if sap_lw is not None:
            sap_new_shares  = sap_tw['Shares'] - sap_lw['Shares']
            sap_price_gain  = sap_lw['Shares'] * (
                sap_tw['Current_Price'] * sap_tw['Exchange_Rate'] -
                sap_lw['Current_Price'] * sap_lw['Exchange_Rate'])
            sap_fx_impact   = sap_lw['Shares'] * sap_lw['Current_Price'] * (
                sap_tw['Exchange_Rate'] - sap_lw['Exchange_Rate'])
            sap_delta       = sap_tw['Total_Value'] - sap_lw['Total_Value']
            sap_ncf         = sap_tw['Net_Cash_Flow']
            sap_espp_contrib = sap_delta - sap_price_gain
            sap_discount    = sap_espp_contrib - sap_ncf

            lines += [f"| 持仓股数 | {sap_lw['Shares']:.2f} → {sap_tw['Shares']:.2f} 股（{sap_new_shares:+.2f}）|"]
            lines += [f"| 股价（EUR） | €{sap_lw['Current_Price']:.2f} → €{sap_tw['Current_Price']:.2f}（{sap_tw['Current_Price']-sap_lw['Current_Price']:+.2f}）|"]
            fx_change = sap_tw['Exchange_Rate'] - sap_lw['Exchange_Rate']
            fx_str = f"{sap_tw['Exchange_Rate']:.4f}（{fx_change:+.4f}）" if abs(fx_change) > 0.0001 else f"{sap_tw['Exchange_Rate']:.4f}（无变化）"
            lines += [f"| EUR/CNY 汇率 | {sap_lw['Exchange_Rate']:.4f} → {fx_str} |"]
            lines += [f"| 本周市值 | {_fmt(sap_tw['Total_Value'])}（上周 {_fmt(sap_lw['Total_Value'])}）|"]
            lines += [f"| 总市值变化 | {sap_delta:+,.0f} |"]
            lines += [f"| 其中：价格涨跌 | {sap_price_gain:+,.0f}（{sap_lw['Shares']:.2f}股 × €{sap_tw['Current_Price']-sap_lw['Current_Price']:+.2f} × {sap_tw['Exchange_Rate']:.4f}）|"]
            if sap_ncf > 0:
                lines += [f"| 其中：ESPP/RSU 归属 | {sap_espp_contrib:+,.0f}（成本 {_fmt(sap_ncf)}，折扣收益 {_fmt(sap_discount)}）|"]
            if abs(sap_fx_impact) >= 1:
                lines += [f"| 其中：汇率影响 | {sap_fx_impact:+,.0f} |"]
        else:
            lines += [f"| 持仓股数 | {sap_tw['Shares']:.2f} 股 |"]
            lines += [f"| 本周市值 | {_fmt(sap_tw['Total_Value'])} |"]
        lines += ['']

    # ── 6. 合成标普β状态 ──
    if dca:
        dca_log     = dca['dow_prepaid']['consumption_log']
        dca_balance = dca['dow_prepaid']['balance']
        week_consumed = sum(
            x['consumed'] for x in dca_log
            if last_week and x['date'] > last_week)
        lines += ['## 合成标普β状态\n']
        lines += [f'- 预付池余额：{_fmt(dca_balance)}']
        if week_consumed > 0:
            lines += [f'- 本周抵扣：{_fmt(week_consumed)}']
            for x in dca_log:
                if last_week and x['date'] > last_week:
                    lines += [f"  - {x['date']}：SP倍数 {x['sp_multiplier']}，抵扣 {_fmt(x['consumed'])}，余额 {_fmt(x['balance_after'])}"]
        else:
            lines += ['- 本周抵扣：**未执行** ⚠️']
        lines += ['']

    # ── 7. 资产配置变化 ──
    lines += ['## 资产配置变化\n']
    lines += ['| 类别 | 本周占比 | 上周占比 | 变化 |']
    lines += ['|------|---------|---------|------|']
    for cls, label in CLASS_LABEL.items():
        tv   = tw[tw['Asset_Class'] == cls]['Total_Value'].sum()
        tv_l = lw[lw['Asset_Class'] == cls]['Total_Value'].sum() if not lw.empty else 0.0
        if tv > 0 or tv_l > 0:
            pct   = tv   / total_tw * 100 if total_tw else 0
            pct_l = tv_l / total_lw * 100 if total_lw else 0
            lines += [f'| {label} | {pct:.1f}% | {pct_l:.1f}% | {pct-pct_l:+.1f}% |']
    lines += ['']

    # 写入文件
    report = '\n'.join(lines)
    out_dir = os.path.join(data_dir, 'weekly_reports')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{this_week}.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)

    return out_path
