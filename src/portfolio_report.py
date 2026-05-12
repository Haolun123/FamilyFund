"""portfolio_report.py — Portfolio HTML 报告生成器（替代 pdf_report.py）。

生成独立 HTML 报告，内嵌 Plotly 图表，无中文字体问题。
用浏览器打开后可打印为 PDF。
"""

import os
from datetime import date


def generate_report(df, fund_nav_df, class_nav_dict, allocation_df, cost_basis_df) -> bytes:
    """生成 Portfolio HTML 报告，返回 bytes（与 pdf_report.generate_report 接口兼容）。"""
    import plotly.graph_objects as go
    import plotly.io as pio
    import pandas as pd

    report_date = date.today().isoformat()

    # ── 基础数据 ────────────────────────────────────────────
    from nav_engine import CLASS_DISPLAY_NAMES

    latest_fund = fund_nav_df.iloc[-1]
    latest_date = latest_fund['Date']
    latest_nav  = latest_fund['NAV']
    cum_return  = latest_fund['Cumulative_Return(%)']
    total_value = latest_fund['Total_Value']

    # 总成本 = 建仓日全部持仓 + 后续所有外部入金（Cash 行正 NCF，非建仓日）
    first_date = df['Date'].min()
    initial_investment = df[df['Date'] == first_date]['Total_Value'].sum()
    subsequent_inflows = df[
        (df['Date'] > first_date) &
        (df['Asset_Class'] == 'Cash') &
        (df['Net_Cash_Flow'] > 0)
    ]['Net_Cash_Flow'].sum()
    total_cost = initial_investment + subsequent_inflows
    total_pl = total_value - total_cost
    total_pl_rate = total_pl / total_cost * 100 if total_cost else 0

    # ── 图1: NAV 走势 ────────────────────────────────────────
    nav_vals = fund_nav_df['NAV']
    nav_min = float(nav_vals.min())
    nav_max = float(nav_vals.max())
    nav_padding = max((nav_max - nav_min) * 0.15, 0.02)

    fig_nav = go.Figure()
    fig_nav.add_trace(go.Scatter(
        x=fund_nav_df['Date'], y=fund_nav_df['NAV'],
        name='单位净值', line=dict(color='#1565c0', width=2.5),
        fill='tonexty', fillcolor='rgba(21,101,192,0.08)',
    ))
    fig_nav.add_hline(y=1.0, line_dash='dash', line_color='#888', annotation_text='基准 1.0')
    fig_nav.update_layout(
        title='基金净值走势', height=380,
        yaxis_title='单位净值', hovermode='x unified',
        yaxis=dict(range=[nav_min - nav_padding, nav_max + nav_padding]),
        legend=dict(orientation='h', yanchor='bottom', y=1.02),
        margin=dict(t=50, b=40),
    )

    # ── 图2: 分类净值对比 ────────────────────────────────────
    fig_class = go.Figure()
    colors = ['#1565c0','#c62828','#2e7d32','#f57f17','#6a1b9a','#00838f','#4e342e']
    all_class_navs = []
    for i, (cls, nav_df) in enumerate(class_nav_dict.items()):
        if cls == 'Cash':
            continue
        name = CLASS_DISPLAY_NAMES.get(cls, cls)
        fig_class.add_trace(go.Scatter(
            x=nav_df['Date'], y=nav_df['NAV'],
            name=name, line=dict(color=colors[i % len(colors)], width=1.8),
        ))
        all_class_navs.extend(nav_df['NAV'].tolist())
    fig_class.add_hline(y=1.0, line_dash='dash', line_color='#ccc')
    if all_class_navs:
        cn_min = min(all_class_navs)
        cn_max = max(all_class_navs)
        cn_pad = max((cn_max - cn_min) * 0.1, 0.05)
        fig_class.update_yaxes(range=[cn_min - cn_pad, cn_max + cn_pad])
    fig_class.update_layout(
        title='分类净值对比', height=420, hovermode='x unified',
        yaxis_title='分类净值',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        margin=dict(t=50, b=40),
    )

    # ── 图3: 资产配置饼图 ────────────────────────────────────
    alloc = allocation_df[allocation_df['Asset_Class'] != 'Cash'].copy()
    alloc['Display'] = alloc['Asset_Class'].map(CLASS_DISPLAY_NAMES).fillna(alloc['Asset_Class'])
    fig_pie = go.Figure(go.Pie(
        labels=alloc['Display'],
        values=alloc['Total_Value'],
        hole=0.45,
        textinfo='label+percent',
        marker=dict(colors=colors[:len(alloc)]),
    ))
    fig_pie.update_layout(
        title='资产配置（不含现金）', height=320,
        showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=-0.2),
        margin=dict(t=50, b=60),
    )

    # ── 盈亏数据 ────────────────────────────────────────────
    pl_rows = ''
    if cost_basis_df is not None and len(cost_basis_df) > 0:
        pl_df = cost_basis_df[
            (cost_basis_df['Asset_Class'] != 'Cash') &
            (cost_basis_df['Market_Value'] > 0)
        ].copy()
        pl_df['Display'] = pl_df['Asset_Class'].map(CLASS_DISPLAY_NAMES).fillna(pl_df['Asset_Class'])
        pl_df['Profit_Loss'] = pl_df['Market_Value'] - pl_df['Cost_Basis']
        pl_df['PL_Rate'] = pl_df['Profit_Loss'] / pl_df['Cost_Basis'] * 100

        for _, row in pl_df.iterrows():
            pl_color = '#2e7d32' if row['Profit_Loss'] >= 0 else '#c62828'
            pl_rows += f'''<tr>
                <td>{row["Name"]}</td>
                <td>{row["Display"]}</td>
                <td>¥{row["Cost_Basis"]:,.0f}</td>
                <td>¥{row["Market_Value"]:,.0f}</td>
                <td style="color:{pl_color};font-weight:bold">¥{row["Profit_Loss"]:+,.0f}</td>
                <td style="color:{pl_color}">{row["PL_Rate"]:+.2f}%</td>
            </tr>'''

    # ── 分类业绩表 ────────────────────────────────────────
    perf_rows = ''
    for cls, nav_df in class_nav_dict.items():
        if cls == 'Cash' or nav_df.empty:
            continue
        latest = nav_df.iloc[-1]
        alloc_row = allocation_df[allocation_df['Asset_Class'] == cls]
        alloc_pct = alloc_row['Allocation_Percent'].values[0] * 100 if len(alloc_row) > 0 else 0
        cr = latest['Cumulative_Return(%)']
        cr_color = '#2e7d32' if cr >= 0 else '#c62828'
        name = CLASS_DISPLAY_NAMES.get(cls, cls)
        perf_rows += f'''<tr>
            <td>{name}</td>
            <td>{latest["NAV"]:.4f}</td>
            <td style="color:{cr_color};font-weight:bold">{cr:+.2f}%</td>
            <td>¥{latest["Total_Value"]:,.0f}</td>
            <td>{alloc_pct:.1f}%</td>
        </tr>'''

    # ── Plotly JS ────────────────────────────────────────────
    import plotly
    plotly_js_path = os.path.join(os.path.dirname(plotly.__file__), 'package_data', 'plotly.min.js')
    if os.path.exists(plotly_js_path):
        with open(plotly_js_path) as f:
            plotly_js = f'<script>{f.read()}</script>'
    else:
        plotly_js = '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>'

    def _fig_html(fig):
        return pio.to_html(fig, full_html=False, include_plotlyjs=False)

    # ── 组装 HTML ────────────────────────────────────────────
    cr_color = '#2e7d32' if cum_return >= 0 else '#c62828'
    pl_color  = '#2e7d32' if total_pl >= 0 else '#c62828'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>FamilyFund Portfolio 报告 {report_date}</title>
{plotly_js}
<style>
  body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
        max-width:1100px;margin:0 auto;padding:24px 40px;color:#333;background:#fff}}
  h1{{color:#1a237e;border-bottom:3px solid #1565c0;padding-bottom:10px;margin-bottom:20px}}
  h2{{color:#1565c0;margin-top:36px;margin-bottom:12px}}
  .meta{{background:#f5f5f5;border-radius:8px;padding:12px 16px;
          font-size:13px;color:#666;margin-bottom:24px}}
  .kpi-row{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px}}
  .kpi{{background:#f8f9ff;border:1px solid #e3e8f0;border-radius:10px;
         padding:16px 20px;flex:1;min-width:140px}}
  .kpi-label{{font-size:12px;color:#888;margin-bottom:4px}}
  .kpi-value{{font-size:20px;font-weight:bold;color:#1a237e}}
  .kpi-value.pos{{color:#2e7d32}}
  .kpi-value.neg{{color:#c62828}}
  .chart-full{{margin:20px 0}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
  th{{background:#1565c0;color:white;padding:8px 10px;text-align:left;font-weight:600}}
  td{{padding:7px 10px;border-bottom:1px solid #f0f0f0}}
  tr:nth-child(even) td{{background:#fafafa}}
  .footer{{color:#aaa;font-size:12px;text-align:center;margin-top:40px;
            border-top:1px solid #eee;padding-top:16px}}
  @media print{{
    body{{padding:10px 20px}}
    .charts-grid{{grid-template-columns:1fr}}
    h2{{page-break-before:always}}
    h2:first-of-type{{page-break-before:avoid}}
  }}
</style></head><body>

<h1>📊 FamilyFund Portfolio 报告</h1>
<div class="meta">生成日期：{report_date}　数据截至：{latest_date}　建仓日：{first_date}</div>

<div class="kpi-row">
  <div class="kpi"><div class="kpi-label">总资产</div>
    <div class="kpi-value">¥{total_value:,.0f}</div></div>
  <div class="kpi"><div class="kpi-label">单位净值</div>
    <div class="kpi-value">{latest_nav:.4f}</div></div>
  <div class="kpi"><div class="kpi-label">累计收益率</div>
    <div class="kpi-value {'pos' if cum_return>=0 else 'neg'}">{cum_return:+.2f}%</div></div>
  <div class="kpi"><div class="kpi-label">绝对盈亏</div>
    <div class="kpi-value {'pos' if total_pl>=0 else 'neg'}">¥{total_pl:+,.0f}</div></div>
  <div class="kpi"><div class="kpi-label">总投入成本</div>
    <div class="kpi-value">¥{total_cost:,.0f}</div></div>
</div>

<h2>净值走势</h2>
<div class="chart-full">{_fig_html(fig_nav)}</div>

<h2>分类净值对比</h2>
<div class="chart-full">{_fig_html(fig_class)}</div>

<h2>资产配置</h2>
<div class="chart-full">{_fig_html(fig_pie)}</div>

<h2>分类业绩一览</h2>
<table>
  <thead><tr><th>资产类别</th><th>净值</th><th>收益率</th><th>市值</th><th>占比</th></tr></thead>
  <tbody>{perf_rows}</tbody>
</table>

<h2>持仓盈亏明细</h2>
<table>
  <thead><tr><th>名称</th><th>类别</th><th>成本</th><th>市值</th><th>盈亏</th><th>收益率</th></tr></thead>
  <tbody>{pl_rows if pl_rows else '<tr><td colspan="6" style="text-align:center;color:#aaa">暂无数据</td></tr>'}</tbody>
</table>

<div class="footer">
  FamilyFund Portfolio 报告　{report_date}　仅供个人资产管理参考，不构成投资建议
</div>
</body></html>"""

    return html.encode('utf-8')
