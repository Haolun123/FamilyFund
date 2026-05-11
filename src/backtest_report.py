"""backtest_report.py — 全标的回测报告生成器。

生成独立 HTML 报告（内嵌所有图表），可直接在浏览器打开或打印为 PDF。
"""

import os
import json
from datetime import date, datetime


# ── 报告参数 ──────────────────────────────────────────────

_TARGET_NAMES = {
    'sp500':    '标普500 (^GSPC)',
    'ndx100':   '纳指100 (^NDX)',
    'csi300':   'CSI300 沪深300',
    'csi_a500': '中证A500',
    'gold':     '黄金 (GC=F)',
}

_TARGET_COLORS = {
    'sp500':    '#1565c0',
    'ndx100':   '#c62828',
    'csi300':   '#2e7d32',
    'csi_a500': '#f57f17',
    'gold':     '#6a1b9a',
}

_CURRENCY = {
    'sp500': '$', 'ndx100': '$', 'gold': '$',
    'csi300': '¥', 'csi_a500': '¥',
}


def generate_backtest_report(
    data_dir: str,
    start_date: str = '2015-03-01',
    end_date: str | None = None,
    base_amount: float = 1000.0,
    freq: str = 'M',
    top_multiplier_equity: float = 10.0,
    top_multiplier_gold: float = 5.0,
) -> str:
    """跑全标的回测并生成 HTML 报告。

    Returns:
        保存的 HTML 文件路径
    """
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from backtest import run_backtest, run_all_targets, _TARGET_MIN_DATES
    import plotly.graph_objects as go
    import plotly.io as pio
    import pandas as pd

    end_date = end_date or date.today().isoformat()
    report_date = date.today().isoformat()

    # ── 跑全标的回测 ──────────────────────────────────────
    print("Running backtests...")
    all_pts = run_all_targets(
        user_start_date=start_date,
        base_amount=base_amount,
        freq=freq,
        top_multiplier_equity=top_multiplier_equity,
        top_multiplier_gold=top_multiplier_gold,
        end_date=end_date,
    )

    # 跑每个标的的详细数据（含 history_df）
    results = {}
    for target in ['sp500', 'ndx100', 'csi300', 'csi_a500', 'gold']:
        min_date = _TARGET_MIN_DATES.get(target, '2000-01-01')
        actual_start = max(start_date, min_date)
        top_mult = top_multiplier_gold if target == 'gold' else top_multiplier_equity
        try:
            r = run_backtest(
                target=target,
                start_date=actual_start,
                base_amount=base_amount,
                freq=freq,
                top_multiplier=top_mult,
                end_date=end_date,
            )
            r['actual_start'] = actual_start
            results[target] = r
            print(f"  {_TARGET_NAMES[target]}: OK")
        except Exception as e:
            print(f"  {_TARGET_NAMES[target]}: FAILED - {e}")

    # ── 图1：四象限散点图 ──────────────────────────────────
    pts_valid = [p for p in all_pts if p['xirr_excess'] is not None and p['pl_excess'] is not None]
    xs = [p['xirr_excess'] for p in pts_valid]
    ys = [p['pl_excess'] for p in pts_valid]
    x_pad = max(abs(x) for x in xs) * 1.4 if xs else 1
    y_pad = max(abs(y) for y in ys) * 1.4 if ys else 1000

    fig_scatter = go.Figure()
    for shape_args in [
        dict(x0=0, x1=x_pad,   y0=0,      y1=y_pad,  fillcolor='rgba(46,125,50,0.07)'),
        dict(x0=-x_pad, x1=0,  y0=0,      y1=y_pad,  fillcolor='rgba(255,193,7,0.07)'),
        dict(x0=-x_pad, x1=0,  y0=-y_pad, y1=0,      fillcolor='rgba(211,47,47,0.07)'),
        dict(x0=0, x1=x_pad,   y0=-y_pad, y1=0,      fillcolor='rgba(255,152,0,0.07)'),
    ]:
        fig_scatter.add_shape(type='rect', line_width=0, layer='below', **shape_args)
    for ann in [
        (0.97, 0.97, '多投多赚 ✓', 'right', 'top'),
        (0.03, 0.97, '少投高效',   'left',  'top'),
        (0.03, 0.03, '两者皆输 ✗', 'left',  'bottom'),
        (0.97, 0.03, '多投无超额', 'right', 'bottom'),
    ]:
        fig_scatter.add_annotation(x=ann[0], y=ann[1], text=ann[2], showarrow=False,
                                   xref='paper', yref='paper',
                                   font=dict(size=12, color='#aaa'),
                                   xanchor=ann[3], yanchor=ann[4])
    fig_scatter.add_shape(type='line', x0=-x_pad, x1=x_pad, y0=0, y1=0, line=dict(color='#ccc', width=1))
    fig_scatter.add_shape(type='line', x0=0, x1=0, y0=-y_pad, y1=y_pad, line=dict(color='#ccc', width=1))

    colors = ['#1565c0','#c62828','#2e7d32','#f57f17','#6a1b9a','#00838f']
    for i, p in enumerate(pts_valid):
        fig_scatter.add_trace(go.Scatter(
            x=[p['xirr_excess']], y=[p['pl_excess']],
            mode='markers+text', text=[p['label']], textposition='top center',
            marker=dict(size=14, color=colors[i % len(colors)]),
            name=p['label'],
            hovertemplate=f"<b>{p['label']}</b><br>XIRR超额: {p['xirr_excess']:+.2f}%<br>盈亏超额: {p['pl_excess']:+,.0f}<extra></extra>",
        ))
    fig_scatter.update_layout(
        title=f'策略有效性四象限分析（{start_date} ~ {end_date}）',
        height=500, showlegend=False,
        xaxis_title='XIRR 超额（矩阵 - 固定，%）',
        yaxis_title='绝对盈亏超额（矩阵 - 固定）',
        xaxis=dict(range=[-x_pad, x_pad], zeroline=False),
        yaxis=dict(range=[-y_pad, y_pad], zeroline=False),
        margin=dict(t=60, b=60),
    )

    # ── 图2+：各标的累计市值对比 ──────────────────────────
    target_figs = {}
    for target, r in results.items():
        cur = _CURRENCY.get(target, '¥')
        hdf = r['history']
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=hdf['date'], y=hdf['fixed_cum_value'],
                                 name='固定策略', line=dict(color='#4ECDC4', width=2)))
        fig.add_trace(go.Scatter(x=hdf['date'], y=hdf['matrix_cum_value'],
                                 name='矩阵策略', line=dict(color='#FF6B6B', width=2)))
        fig.add_trace(go.Scatter(x=hdf['date'], y=hdf['fixed_cum_cost'],
                                 name='累计成本', line=dict(color='#888', width=1, dash='dash'), opacity=0.5))
        fig.update_layout(
            title=f'{_TARGET_NAMES[target]} 累计市值走势',
            height=350, hovermode='x unified',
            yaxis_title=f'金额 ({cur})',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            margin=dict(t=60, b=40),
        )
        target_figs[target] = fig

    # ── 构建 HTML ──────────────────────────────────────────
    def _fig_html(fig):
        return pio.to_html(fig, full_html=False, include_plotlyjs=False)

    def _fmt_money(v, cur='¥'):
        if v is None: return '—'
        return f'{cur}{v:+,.0f}' if v != 0 else f'{cur}0'

    def _fmt_pct(v):
        if v is None: return '—'
        return f'{v:+.2f}%'

    # 构建各标的指标卡 HTML
    target_cards_html = ''
    for target, r in results.items():
        fixed  = r['fixed']
        matrix = r['matrix']
        cur    = _CURRENCY.get(target, '¥')
        name   = _TARGET_NAMES[target]
        color  = _TARGET_COLORS.get(target, '#333')
        act_start = r.get('actual_start', start_date)

        xirr_e = (matrix['xirr'] - fixed['xirr']) if (matrix['xirr'] and fixed['xirr']) else None
        pl_e   = (matrix['profit_loss'] - fixed['profit_loss']) if (matrix['profit_loss'] is not None and fixed['profit_loss'] is not None) else None
        vpc_e  = (matrix['value_per_cost'] - fixed['value_per_cost']) if (matrix['value_per_cost'] and fixed['value_per_cost']) else None

        def _cell(label, f_val, m_val, fmt_fn, diff_fn=None):
            fv = fmt_fn(f_val)
            mv = fmt_fn(m_val)
            if diff_fn and f_val is not None and m_val is not None:
                d = m_val - f_val
                dc = '#2e7d32' if d > 0 else ('#d32f2f' if d < 0 else '#888')
                dv = fmt_fn(d) if hasattr(d, '__float__') else '—'
                diff_html = f'<span style="color:{dc};font-weight:bold">{dv}</span>'
            else:
                diff_html = '—'
            return f'''<tr>
                <td>{label}</td>
                <td>{fv}</td>
                <td>{mv}</td>
                <td>{diff_html}</td>
            </tr>'''

        rows = (
            _cell('总投入成本', fixed['total_cost'], matrix['total_cost'],
                  lambda v: f'{cur}{v:,.0f}' if v else '—')
            + _cell('最终市值', fixed['final_value'], matrix['final_value'],
                    lambda v: f'{cur}{v:,.0f}' if v else '—', lambda v: v)
            + _cell('绝对盈亏', fixed['profit_loss'], matrix['profit_loss'],
                    lambda v: f'{cur}{v:+,.0f}' if v is not None else '—', lambda v: v)
            + _cell('每元成本市值', fixed['value_per_cost'], matrix['value_per_cost'],
                    lambda v: f'{v:.4f}' if v else '—', lambda v: v)
            + _cell('XIRR（年化）', fixed['xirr'], matrix['xirr'],
                    lambda v: f'{v:+.2f}%' if v is not None else '—', lambda v: v)
            + _cell('最大回撤', fixed['max_drawdown'], matrix['max_drawdown'],
                    lambda v: f'{v:.2f}%' if v is not None else '—',
                    lambda v: -v)  # 回撤越小越好，取负
            + f'<tr><td>定投期数</td><td>{fixed["periods"]}</td><td>{matrix["periods"]}</td>'
              f'<td style="color:#888">暂停{fixed["periods"]-matrix["periods"]}期</td></tr>'
        )

        fig_html = _fig_html(target_figs[target])

        target_cards_html += f'''
        <div class="target-card">
            <h3 style="color:{color}; border-left:4px solid {color}; padding-left:10px">{name}</h3>
            <p style="color:#888; font-size:13px">回测区间：{act_start} ~ {end_date}　基准金额：{cur}{base_amount:,.0f}/期　频率：{"月频" if freq=="M" else "周频"}</p>
            <table class="metrics-table">
                <thead><tr><th>指标</th><th>固定定投</th><th>矩阵策略</th><th>超额</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
            <div class="chart-container">{fig_html}</div>
        </div>
        '''

    scatter_html = _fig_html(fig_scatter)

    # plotly js
    import plotly
    plotly_js_path = os.path.join(os.path.dirname(plotly.__file__), 'package_data', 'plotly.min.js')
    if os.path.exists(plotly_js_path):
        with open(plotly_js_path) as f:
            plotly_js = f'<script>{f.read()}</script>'
    else:
        plotly_js = '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FamilyFund 回测报告 {report_date}</title>
{plotly_js}
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
          max-width: 1200px; margin: 0 auto; padding: 20px 40px; color: #333; background: #fff; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #1565c0; padding-bottom: 10px; }}
  h2 {{ color: #1565c0; margin-top: 40px; }}
  h3 {{ margin-top: 0; }}
  .meta {{ background: #f5f5f5; border-radius: 8px; padding: 16px; margin: 20px 0; font-size:14px; }}
  .meta span {{ margin-right: 24px; }}
  .target-card {{ border: 1px solid #e0e0e0; border-radius: 10px; padding: 24px;
                  margin: 24px 0; page-break-inside: avoid; }}
  .metrics-table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
  .metrics-table th {{ background: #f5f5f5; padding: 8px 12px; text-align: left;
                       border-bottom: 2px solid #ddd; }}
  .metrics-table td {{ padding: 7px 12px; border-bottom: 1px solid #f0f0f0; }}
  .metrics-table tr:last-child td {{ border-bottom: none; }}
  .chart-container {{ margin-top: 16px; }}
  .conclusion {{ background: #e8f5e9; border-left: 4px solid #2e7d32;
                 padding: 16px 20px; border-radius: 0 8px 8px 0; margin: 20px 0; }}
  .conclusion h4 {{ margin: 0 0 10px 0; color: #2e7d32; }}
  .conclusion table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  .conclusion td, .conclusion th {{ padding: 6px 10px; border-bottom: 1px solid #c8e6c9; }}
  .footer {{ color: #aaa; font-size: 12px; text-align: center; margin-top: 40px;
             border-top: 1px solid #eee; padding-top: 16px; }}
  @media print {{
    body {{ padding: 10px 20px; }}
    .target-card {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>

<h1>📊 FamilyFund 定投策略回测报告</h1>

<div class="meta">
  <span>📅 生成日期：{report_date}</span>
  <span>📆 回测区间：{start_date} ~ {end_date}</span>
  <span>💰 基准金额：{base_amount:,.0f} CNY/期</span>
  <span>📊 定投频率：{"月频" if freq=="M" else "周频"}</span>
  <span>🎯 权益顶格：{top_multiplier_equity}x　黄金顶格：{top_multiplier_gold}x</span>
</div>

<h2>一、策略有效性四象限分析</h2>
<p style="color:#666; font-size:14px">
X轴：XIRR超额（矩阵-固定，%）　Y轴：绝对盈亏超额（矩阵-固定）<br>
第I象限（右上）多投多赚：XIRR和盈亏均跑赢　第II象限（左上）少投高效：少投但盈亏更高<br>
第III象限（左下）两者皆输　第IV象限（右下）多投无超额：投入更多但盈亏更少
</p>
<div class="chart-container">{scatter_html}</div>

<h2>二、各标的详细回测</h2>
<p style="color:#666; font-size:13px">各标的使用各自最早可用日期起回测，不强制统一起始（详见数据边界说明）。</p>
{target_cards_html}

<div class="conclusion">
  <h4>📋 综合结论（基于10年样本，2015起）</h4>
  <table>
    <tr><th>排名</th><th>标的</th><th>象限</th><th>建议</th></tr>
    <tr><td>1</td><td>纳指100</td><td>I 多投多赚</td><td>继续矩阵，效果最强</td></tr>
    <tr><td>2</td><td>沪深300</td><td>I 多投多赚</td><td>继续矩阵，稳定有效</td></tr>
    <tr><td>3</td><td>中证A500</td><td>I 多投多赚</td><td>继续矩阵，稳定有效</td></tr>
    <tr><td>4</td><td>标普500</td><td>I 多投多赚</td><td>继续矩阵，温和有效</td></tr>
    <tr><td>5</td><td>黄金</td><td>边界/IV</td><td><b>固定定投</b>，矩阵性价比低</td></tr>
  </table>
</div>

<div class="footer">
  FamilyFund 回测报告　{report_date}　仅供个人参考，不构成投资建议。历史回测不代表未来收益。
</div>

</body>
</html>'''

    # ── 保存文件 ──────────────────────────────────────────
    reports_dir = os.path.join(data_dir, 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    filename = f'backtest_report_{report_date}.html'
    filepath = os.path.join(reports_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\nReport saved: {filepath}")
    return filepath
