import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import os
import sys
import json
import contextlib
from datetime import date, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from nav_engine import (
    load_portfolio, validate_portfolio, compute_fund_nav,
    compute_class_nav, compute_allocation, compute_cost_basis,
    compute_xirr, compute_sharpe, compute_calmar, compute_attribution,
    load_target_allocation, save_target_allocation,
    CLASS_DISPLAY_NAMES, VALID_ASSET_CLASSES,
    _atomic_write_csv, update_snapshot, delete_snapshot,
)
from fx_service import get_exchange_rate, get_stock_price, load_sap_price_cache, save_sap_price_cache
from sap_stock import load_own_sap, load_move_sap, own_sap_summary, move_sap_summary
from portfolio_report import generate_report as generate_pdf_report
from benchmark import get_benchmarks, BENCHMARK_DISPLAY_NAMES, BENCHMARK_COLORS
from fundamentals import (
    load_yf_symbols, save_yf_symbols, get_all_fundamentals,
    add_yf_symbol, remove_yf_symbol, get_yf_symbol, get_show_fundamentals, update_show_fundamentals,
)
from market_monitor import (
    get_market_data, set_pe_override, set_vol_override,
    compute_bias, compute_vix_signal, compute_vxn_signal, compute_pe_signal, compute_qvix_signal,
    lookup_multiplier, lookup_a_share_multiplier, lookup_gold_multiplier,
    lookup_gold_hedge_multiplier,
    SP500_PE_BANDS, SP500_VIX_BANDS, SP500_MATRIX,
    NDX100_PE_BANDS, NDX100_VIX_BANDS, NDX100_MATRIX,
    CSI300_PE_BANDS, CSI300_QVIX_BANDS, CSI300_MATRIX,
    CSI_A500_PE_BANDS, CSI_A500_QVIX_BANDS, CSI_A500_MATRIX,
    GOLD_BIAS_BANDS, GOLD_VIX_BANDS, GOLD_MATRIX,
    TARGETS,
)
from backtest import run_backtest, run_all_targets, _TARGET_MIN_DATES

# ─── Page Config ───

st.set_page_config(
    page_title="FamilyFund Dashboard",
    page_icon="📊",
    layout="wide",
)

# ─── Data Loading (cached) ───

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV = os.path.join(BASE_DIR, 'data', 'portfolio.csv')
SAMPLE_CSV = os.path.join(BASE_DIR, 'data', 'portfolio_sample.csv')
OWN_SAP_CSV = os.path.join(BASE_DIR, 'data', 'own_sap.csv')
MOVE_SAP_CSV = os.path.join(BASE_DIR, 'data', 'move_sap.csv')


@st.cache_data
def load_data(csv_path):
    df = load_portfolio(csv_path)
    if df is None:
        return None, None, None, None, None, None
    errors, warnings = validate_portfolio(df)
    if errors:
        return None, None, None, None, None, None
    fund_nav = compute_fund_nav(df)
    class_nav = compute_class_nav(df)
    allocation = compute_allocation(df)
    cost_basis = compute_cost_basis(
        df,
        own_sap_csv=OWN_SAP_CSV if os.path.exists(OWN_SAP_CSV) else None,
        move_sap_csv=MOVE_SAP_CSV if os.path.exists(MOVE_SAP_CSV) else None,
    )
    xirr = compute_xirr(df)
    sharpe = compute_sharpe(fund_nav)
    calmar = compute_calmar(fund_nav)
    return df, fund_nav, class_nav, allocation, cost_basis, xirr, sharpe, calmar


# Pick the best available data file
csv_path = DEFAULT_CSV if os.path.exists(DEFAULT_CSV) else SAMPLE_CSV
raw_df, fund_nav_df, class_nav_dict, allocation_df, cost_basis_df, xirr_value, sharpe_value, calmar_value = load_data(csv_path)

if raw_df is None:
    st.error("无法加载数据文件。请确保 data/portfolio.csv 或 data/portfolio_sample.csv 存在。")
    st.stop()

# ─── Sidebar：系统状态（仅全局信息）───

st.sidebar.title("FamilyFund")
st.sidebar.caption("家庭基金管理系统")
st.sidebar.divider()

data_name = os.path.basename(csv_path)
dates = sorted(raw_df['Date'].unique())
_latest_snap = dates[-1] if dates else '—'
_fund_start  = dates[0]  if dates else '—'

st.sidebar.markdown(f"**最新快照** {_latest_snap}")
st.sidebar.markdown(f"**建仓日期** {_fund_start}")
st.sidebar.markdown(f"**快照期数** {len(dates)} 期")
st.sidebar.markdown(f"**数据文件** `{data_name}`")

# ─── Portfolio Tab 控件（在 Tab 内使用，此处预先计算所需变量）───

# 日期范围、类别筛选、基准对比 → 移至 Portfolio Tab 顶部
# 以下变量在 Tab1 内部定义，此处设默认值供其他 Tab 引用
date_start = dates[0]  if dates else ''
date_end   = dates[-1] if dates else ''
all_classes   = sorted(c for c in raw_df['Asset_Class'].unique() if c != 'Cash')
display_map   = {cls: CLASS_DISPLAY_NAMES.get(cls, cls) for cls in raw_df['Asset_Class'].unique()}
selected_classes  = all_classes
selected_benchmarks = []
benchmark_data    = {}
fund_start_date   = raw_df['Date'].min()

# Filter data（默认全量，Tab1 内部会用用户选择后的值覆盖本地变量）
filtered_raw  = raw_df
filtered_fund = fund_nav_df

# ─── Global session_state initialization ───
# Must run before any tab widgets to prevent widget value= overriding cache
if 'sap_price_initialized' not in st.session_state:
    _sap_cache = load_sap_price_cache()
    if _sap_cache:
        st.session_state['sap_current_price'] = _sap_cache['price_eur']
        st.session_state['sap_fx_rate'] = _sap_cache['fx_rate']
    st.session_state['sap_price_initialized'] = True

# ─── Tabs ───

tab_dashboard, tab_update, tab_sap, tab_market, tab_backtest, tab_quarterly, tab_planning, tab_tenth = st.tabs(
    ["Portfolio", "Ledger", "SAP", "Market", "Backtest", "Quarterly", "Planning", "10th Man"]
)

# ═══════════════════════════════════════════════════════════
# Tab 1: Dashboard
# ═══════════════════════════════════════════════════════════

with tab_dashboard:

    # 加载目标配置比例（不缓存，用户可能刚修改过）
    target_alloc = load_target_allocation(os.path.dirname(csv_path))

    # ─── Portfolio 筛选控件 ───
    with st.expander("🔧 筛选与导出", expanded=False):
        _fc1, _fc2, _fc3 = st.columns([2, 2, 1])
        with _fc1:
            st.markdown("**日期范围**")
            if len(dates) > 1:
                _start_idx, _end_idx = st.select_slider(
                    "日期范围",
                    options=list(range(len(dates))),
                    value=(0, len(dates) - 1),
                    format_func=lambda i: dates[i],
                    label_visibility="collapsed",
                    key="port_date_range",
                )
                date_start = dates[_start_idx]
                date_end   = dates[_end_idx]
            else:
                date_start = date_end = dates[0]

        with _fc2:
            st.markdown("**资产类别**")
            _select_all = st.checkbox("全选", value=True, key="port_select_all")
            if _select_all:
                selected_classes = all_classes
            else:
                selected_classes = st.multiselect(
                    "选择类别", options=all_classes, default=all_classes,
                    format_func=lambda c: display_map[c],
                    label_visibility="collapsed", key="port_classes",
                )

        with _fc3:
            st.markdown("**导出**")
            _html_bytes = generate_pdf_report(raw_df, fund_nav_df, class_nav_dict, allocation_df, cost_basis_df)
            _latest_date = fund_nav_df.iloc[-1]['Date']
            st.download_button(
                label="📄 HTML 报告",
                data=_html_bytes,
                file_name=f"FamilyFund_{_latest_date}.html",
                mime="text/html",
                use_container_width=True,
            )

        st.markdown("**基准对比**")
        _bm_cols = st.columns(len(BENCHMARK_DISPLAY_NAMES))
        for _i, (bkey, bname) in enumerate(BENCHMARK_DISPLAY_NAMES.items()):
            with _bm_cols[_i]:
                if st.checkbox(bname, value=False, key=f"bm_{bkey}"):
                    selected_benchmarks.append(bkey)

        benchmark_data = get_benchmarks(fund_start_date) if selected_benchmarks else {}

    # 根据筛选更新 filtered 变量
    filtered_raw  = raw_df[(raw_df['Date'] >= date_start) & (raw_df['Date'] <= date_end)]
    filtered_fund = fund_nav_df[(fund_nav_df['Date'] >= date_start) & (fund_nav_df['Date'] <= date_end)]

    # ─── Section 1: Fund Overview ───

    st.header("基金总览")

    # KPI metrics
    latest_fund = filtered_fund.iloc[-1] if len(filtered_fund) > 0 else fund_nav_df.iloc[-1]
    latest_date = latest_fund['Date']
    latest_holdings = raw_df[raw_df['Date'] == raw_df['Date'].max()]

    # 累计投入 = 建仓日总市值（初始本金）+ 后续历次外部入金（Cash NCF，非建仓日）
    # 建仓日：NCF = Total_Value（全部记为本金），后续日期：只有 Cash 行的 NCF 是外部入金
    first_date = raw_df['Date'].min()
    initial_investment = raw_df[raw_df['Date'] == first_date]['Total_Value'].sum()
    subsequent_inflows = raw_df[
        (raw_df['Date'] > first_date) &
        (raw_df['Asset_Class'] == 'Cash') &
        (raw_df['Net_Cash_Flow'] > 0)
    ]['Net_Cash_Flow'].sum()
    total_invested = initial_investment + subsequent_inflows
    simple_profit = latest_fund['Total_Value'] - total_invested
    simple_return = (simple_profit / total_invested * 100) if total_invested else 0.0

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("总资产", f"¥{latest_fund['Total_Value']:,.0f}")
    with col2:
        st.metric("单位净值", f"{latest_fund['NAV']:.4f}")
    with col3:
        st.metric("累计收益", f"¥{simple_profit:+,.0f}", delta=f"{simple_return:+.2f}%",
                  help=f"当前总资产 - 累计投入（¥{total_invested:,.0f}）。累计投入 = 建仓本金 + 历次外部入金")
    with col4:
        ann = latest_fund.get('Annualized_Return(%)')
        if ann is not None and not pd.isna(ann):
            st.metric("年化收益率(TWR)", f"{ann:+.2f}%", help="时间加权年化收益率，剔除资金进出影响")
        else:
            st.metric("年化收益率(TWR)", "< 1年")
    with col5:
        if xirr_value is not None:
            st.metric("XIRR(MWR)", f"{xirr_value*100:+.2f}%", help="资金加权年化收益率，反映实际资金投入时机的真实回报")
        else:
            st.metric("XIRR(MWR)", "< 1年")

    col6, col7, col8, col9 = st.columns(4)
    with col6:
        if sharpe_value is not None:
            st.metric("夏普比率", f"{sharpe_value:.2f}", help="年化夏普比率，无风险利率 2.5%。>1 优秀，>0 可接受，<0 不如无风险收益")
        else:
            st.metric("夏普比率", "< 1年")
    with col7:
        if calmar_value is not None:
            st.metric("卡尔马比率", f"{calmar_value:.2f}", help="年化收益率 / 最大回撤。>1 优秀，越高说明相对回撤获取的收益越高")
        else:
            st.metric("卡尔马比率", "< 1年")
    with col8:
        mdd = latest_fund.get('Max_Drawdown(%)')
        if mdd is not None and not pd.isna(mdd):
            st.metric("最大回撤", f"{mdd:.2f}%")
        else:
            st.metric("最大回撤", "—")
    with col9:
        st.metric("持仓数", f"{len(latest_holdings)}")

    # Fund NAV chart
    fig_fund = px.line(
        filtered_fund, x='Date', y='NAV',
        title='基金净值走势',
        labels={'Date': '日期', 'NAV': '单位净值'},
    )
    fig_fund.add_hline(y=1.0, line_dash="dash", line_color="red", opacity=0.5,
                       annotation_text="基准线 1.0")
    fig_fund.update_traces(line_color='#1f77b4', line_width=2.5, name='基金净值')

    # Overlay benchmark lines
    meta = benchmark_data.get('meta', {})
    for bkey in selected_benchmarks:
        bdata = benchmark_data.get(bkey)
        if not bdata:
            st.sidebar.warning(f"{BENCHMARK_DISPLAY_NAMES[bkey]}: 无数据")
            continue
        bdf = pd.DataFrame(bdata)
        bdf = bdf[(bdf['date'] >= date_start) & (bdf['date'] <= date_end)]
        if bdf.empty:
            continue
        until = meta.get(f'{bkey}_data_until', '未知')
        label = f"{BENCHMARK_DISPLAY_NAMES[bkey]}（截至{until}）"
        fig_fund.add_scatter(
            x=bdf['date'], y=bdf['value'],
            mode='lines',
            name=label,
            line=dict(color=BENCHMARK_COLORS[bkey], width=1.5, dash='dot'),
        )

    fig_fund.update_layout(hovermode='x unified', height=400,
                           legend=dict(orientation='h', yanchor='bottom', y=-0.3,
                                       xanchor='center', x=0.5))
    st.plotly_chart(fig_fund, use_container_width=True)

    # ─── AI 周度评估 ───
    from ai_weekly import build_weekly_context, generate_weekly_summary
    _ai_data_dir = os.path.dirname(csv_path)
    _ai_config_exists = os.path.exists(os.path.join(_ai_data_dir, 'tenth_man_config.json'))

    with st.expander("✨ AI 周度评估", expanded=False):
        if not _ai_config_exists:
            st.warning("未找到 tenth_man_config.json，请先配置 API key。")
        else:
            if st.button("生成本周 AI 点评", key="gen_ai_weekly"):
                with st.spinner("GLM 生成中..."):
                    _rb_mkt = get_market_data()
                    _ctx = build_weekly_context(
                        fund_nav_df, raw_df, allocation_df,
                        class_nav_dict, _rb_mkt,
                        xirr_value, sharpe_value,
                        data_dir=_ai_data_dir,
                    )
                    _summary = generate_weekly_summary(_ctx, _ai_data_dir)
                    st.session_state['ai_weekly_summary'] = _summary
                    st.session_state['ai_weekly_date'] = latest_date

            if 'ai_weekly_summary' in st.session_state:
                st.markdown(st.session_state['ai_weekly_summary'])
                _ai_cfg = json.load(open(os.path.join(_ai_data_dir, 'tenth_man_config.json'))) if _ai_config_exists else {}
                st.caption(f"生成于 {st.session_state.get('ai_weekly_date', '')} · {_ai_cfg.get('model', 'GLM')}")

    # ─── Section 2: Asset Class Comparison ───

    st.header("资产类别对比")

    # 固定颜色映射：保证折线图和饼图颜色一致
    CLASS_COLORS = {
        'US_Blend_Fund':   '#2196F3',  # 蓝
        'US_Growth_Fund':  '#9C27B0',  # 紫
        'CN_Index_Fund':   '#FF9800',  # 橙
        'ETF_Stock':       '#4CAF50',  # 绿
        'Fixed_Income':    '#00BCD4',  # 青
        'Gold':            '#FFC107',  # 金
        'Company_Stock':   '#F44336',  # 红
        'Cash':            '#9E9E9E',  # 灰（通常不显示）
    }

    col_chart, col_pie = st.columns([3, 2])

    # Multi-line NAV chart
    with col_chart:
        class_lines = []
        for cls in selected_classes:
            if cls in class_nav_dict:
                nav_df = class_nav_dict[cls].copy()
                nav_df = nav_df[(nav_df['Date'] >= date_start) & (nav_df['Date'] <= date_end)]
                nav_df['Display_Name'] = display_map[cls]
                nav_df['_cls'] = cls
                class_lines.append(nav_df)

        if class_lines:
            combined = pd.concat(class_lines, ignore_index=True)
            # 按 Asset_Class 建立颜色映射（Display_Name → color）
            color_map = {display_map[cls]: CLASS_COLORS.get(cls, '#888888')
                         for cls in selected_classes}
            fig_class = px.line(
                combined, x='Date', y='NAV', color='Display_Name',
                title='分类净值对比',
                labels={'Date': '日期', 'NAV': '净值', 'Display_Name': '资产类别'},
                color_discrete_map=color_map,
            )
            fig_class.add_hline(y=1.0, line_dash="dash", line_color="red", opacity=0.3)
            fig_class.update_layout(hovermode='x unified', height=420, legend=dict(
                orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5,
            ))
            st.plotly_chart(fig_class, use_container_width=True)
        else:
            st.info("请在侧边栏选择至少一个资产类别")

    # Donut pie chart
    with col_pie:
        pie_data = allocation_df[allocation_df['Asset_Class'].isin(selected_classes)].copy()
        if len(pie_data) > 0:
            pie_data['Display_Name'] = pie_data['Asset_Class'].map(display_map)
            # 使用与折线图一致的颜色映射
            pie_color_map = {display_map[cls]: CLASS_COLORS.get(cls, '#888888')
                             for cls in pie_data['Asset_Class']}
            fig_pie = px.pie(
                pie_data, values='Total_Value', names='Display_Name',
                title='资产配置',
                hole=0.45,
                color='Display_Name',
                color_discrete_map=pie_color_map,
            )
            fig_pie.update_traces(
                textinfo='percent+label',
                textposition='outside',
                hovertemplate='%{label}<br>¥%{value:,.0f}<br>%{percent}',
            )
            fig_pie.update_layout(
                height=420,
                showlegend=False,
                annotations=[dict(
                    text=f"¥{pie_data['Total_Value'].sum():,.0f}",
                    x=0.5, y=0.5, font_size=14, showarrow=False,
                )],
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("无数据")

    # Class performance table
    st.subheader("分类业绩一览")
    perf_rows = []

    # 按类别汇总成本和市值，用于计算收益额
    _cls_cost = {}
    if cost_basis_df is not None and len(cost_basis_df) > 0:
        _cb = cost_basis_df[cost_basis_df['Market_Value'] > 0].groupby('Asset_Class').agg(
            cost=('Cost_Basis', 'sum'), mktval=('Market_Value', 'sum')
        )
        for _ac, _row in _cb.iterrows():
            _cls_cost[_ac] = _row['mktval'] - _row['cost']

    for cls in selected_classes:
        if cls in class_nav_dict:
            nav_df = class_nav_dict[cls]
            filtered_cls = nav_df[(nav_df['Date'] >= date_start) & (nav_df['Date'] <= date_end)]
            if len(filtered_cls) > 0:
                latest = filtered_cls.iloc[-1]
                alloc_row = allocation_df[allocation_df['Asset_Class'] == cls]
                alloc_pct = alloc_row['Allocation_Percent'].values[0] * 100 if len(alloc_row) > 0 else 0
                pl = _cls_cost.get(cls)
                pl_str = f"¥{pl:+,.0f}" if pl is not None else '—'
                perf_rows.append({
                    '资产类别': display_map[cls],
                    '净值': f"{latest['NAV']:.4f}",
                    '收益率': f"{latest['Cumulative_Return(%)']:+.2f}%",
                    '收益额': pl_str,
                    '市值': f"¥{latest['Total_Value']:,.0f}",
                    '占比': f"{alloc_pct:.1f}%",
                })

    if perf_rows:
        perf_df = pd.DataFrame(perf_rows)
        st.dataframe(perf_df, use_container_width=True, hide_index=True)

    # ─── Section 3: Holdings Detail ───

    st.header("持仓明细")

    # Get latest date holdings
    latest_date_all = raw_df['Date'].max()
    holdings = raw_df[raw_df['Date'] == latest_date_all].copy()

    # Filters
    fcol1, fcol2 = st.columns(2)
    with fcol1:
        class_filter = st.selectbox(
            "资产类别",
            options=["全部"] + [display_map[c] for c in sorted(holdings['Asset_Class'].unique())],
        )
    with fcol2:
        platform_filter = st.selectbox(
            "交易平台",
            options=["全部"] + sorted(holdings['Platform'].unique().tolist()),
        )

    # Apply filters
    if class_filter != "全部":
        reverse_map = {v: k for k, v in display_map.items()}
        holdings = holdings[holdings['Asset_Class'] == reverse_map[class_filter]]
    if platform_filter != "全部":
        holdings = holdings[holdings['Platform'] == platform_filter]

    # Format display table
    display_holdings = holdings[['Asset_Class', 'Platform', 'Name', 'Code', 'Currency',
                                 'Exchange_Rate', 'Shares', 'Current_Price', 'Total_Value', 'Net_Cash_Flow']].copy()
    display_holdings['Asset_Class'] = display_holdings['Asset_Class'].map(display_map)
    display_holdings = display_holdings.rename(columns={
        'Asset_Class': '资产类别',
        'Platform': '平台',
        'Name': '名称',
        'Code': '代码',
        'Currency': '币种',
        'Exchange_Rate': '汇率',
        'Shares': '份额',
        'Current_Price': '当前价格',
        'Total_Value': '总市值',
        'Net_Cash_Flow': '净现金流',
    })
    display_holdings = display_holdings.sort_values('总市值', ascending=False)

    st.dataframe(
        display_holdings,
        use_container_width=True,
        hide_index=True,
        column_config={
            '总市值': st.column_config.NumberColumn(format="¥%.2f"),
            '当前价格': st.column_config.NumberColumn(format="%.4f"),
            '汇率': st.column_config.NumberColumn(format="%.4f"),
            '份额': st.column_config.NumberColumn(format="%.2f"),
            '净现金流': st.column_config.NumberColumn(format="¥%.2f"),
        },
    )

    # Footer
    st.caption(f"共 {len(holdings)} 条持仓 | 筛选市值合计: ¥{holdings['Total_Value'].sum():,.2f} | 数据日期: {latest_date_all}")

    # ─── Section 3b: Risk Concentration ───

    st.header("风险集中度")

    latest_all = raw_df[raw_df['Date'] == latest_date_all].copy()
    grand_total = latest_all['Total_Value'].sum()

    # 类别集中度
    CLASS_THRESHOLD = 0.40  # 单类别 > 40% 警示
    HOLDING_THRESHOLD = 0.20  # 单持仓 > 20% 警示

    class_conc = latest_all.groupby('Asset_Class')['Total_Value'].sum().reset_index()
    class_conc['Pct'] = class_conc['Total_Value'] / grand_total
    class_conc['Display'] = class_conc['Asset_Class'].map(display_map)

    holding_conc = latest_all.groupby('Name')['Total_Value'].sum().reset_index()
    holding_conc['Pct'] = holding_conc['Total_Value'] / grand_total

    warnings_conc = []
    for _, row in class_conc.iterrows():
        if row['Pct'] > CLASS_THRESHOLD:
            warnings_conc.append(f"**{row['Display']}** 占比 {row['Pct']*100:.1f}%，超过类别阈值 {CLASS_THRESHOLD*100:.0f}%")
    for _, row in holding_conc.iterrows():
        if row['Pct'] > HOLDING_THRESHOLD:
            warnings_conc.append(f"**{row['Name']}** 占比 {row['Pct']*100:.1f}%，超过单持仓阈值 {HOLDING_THRESHOLD*100:.0f}%")

    if warnings_conc:
        for w in warnings_conc:
            st.warning(w)
    else:
        st.success("集中度正常：无类别超过 40%，无单持仓超过 20%")

    # 类别集中度 bar
    class_conc_sorted = class_conc.sort_values('Pct', ascending=True)
    fig_conc = px.bar(
        class_conc_sorted,
        x='Pct', y='Display',
        orientation='h',
        labels={'Pct': '占比', 'Display': '资产类别'},
        title='各类别集中度',
        text=class_conc_sorted['Pct'].apply(lambda x: f'{x*100:.1f}%'),
    )
    fig_conc.add_vline(x=CLASS_THRESHOLD, line_dash='dash', line_color='orange',
                       annotation_text=f'警示线 {CLASS_THRESHOLD*100:.0f}%', annotation_position='top right')
    fig_conc.update_traces(marker_color='#2196F3', textposition='outside')
    fig_conc.update_layout(xaxis_tickformat=',.0%', height=300, margin=dict(l=0, r=40, t=40, b=0))
    st.plotly_chart(fig_conc, use_container_width=True)

    # ─── Section 3c: Currency Exposure ───

    st.header("货币敞口")

    # 按原始币种汇总（不换算），同时换算 CNY 市值
    latest_all['CNY_Value'] = latest_all['Total_Value']  # Total_Value 已是 CNY
    currency_exp = latest_all.groupby('Currency').agg(
        CNY_Value=('CNY_Value', 'sum'),
    ).reset_index()
    currency_exp['Pct'] = currency_exp['CNY_Value'] / grand_total
    currency_exp = currency_exp.sort_values('CNY_Value', ascending=False)

    # Metrics row
    ccy_cols = st.columns(len(currency_exp))
    for i, (_, row) in enumerate(currency_exp.iterrows()):
        with ccy_cols[i]:
            st.metric(
                row['Currency'],
                f"¥{row['CNY_Value']:,.0f}",
                f"{row['Pct']*100:.1f}%",
            )

    # Pie chart
    fig_ccy = px.pie(
        currency_exp,
        names='Currency',
        values='CNY_Value',
        title='货币敞口分布（按 CNY 折算市值）',
        color_discrete_sequence=['#2196F3', '#4CAF50', '#FF9800', '#9C27B0'],
        hole=0.4,
    )
    fig_ccy.update_traces(texttemplate='%{label}<br>%{percent:.1%}', textposition='outside')
    fig_ccy.update_layout(height=350, margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_ccy, use_container_width=True)

    # ─── Section 4: P/L Analysis ───

    st.header("盈亏分析")

    if cost_basis_df is not None and len(cost_basis_df) > 0:
        # Cash 作为调仓中转池不计入盈亏；Market_Value=0 为已清仓持仓也排除
        pl_df = cost_basis_df[
            (cost_basis_df['Asset_Class'] != 'Cash') &
            (cost_basis_df['Market_Value'] > 0)
        ].copy()

        # Summary KPIs
        total_cost = pl_df['Cost_Basis'].sum()
        total_market = pl_df['Market_Value'].sum()
        total_pl = total_market - total_cost
        total_pl_rate = (total_pl / total_cost * 100) if total_cost > 0 else 0

        pl_col1, pl_col2, pl_col3, pl_col4 = st.columns(4)
        with pl_col1:
            st.metric("总成本", f"¥{total_cost:,.0f}")
        with pl_col2:
            st.metric("总市值", f"¥{total_market:,.0f}")
        with pl_col3:
            st.metric("总盈亏", f"¥{total_pl:+,.0f}")
        with pl_col4:
            st.metric("总收益率", f"{total_pl_rate:+.2f}%")

        # P/L bar chart
        chart_data = pl_df.copy()
        chart_data['Display_Name'] = chart_data['Name']
        chart_data['Color'] = chart_data['Profit_Loss'].apply(lambda x: '盈利' if x >= 0 else '亏损')

        fig_pl = px.bar(
            chart_data, x='Profit_Loss', y='Display_Name',
            orientation='h', color='Color',
            color_discrete_map={'盈利': '#2e7d32', '亏损': '#d32f2f'},
            title='持仓盈亏',
            labels={'Profit_Loss': '盈亏 (CNY)', 'Display_Name': ''},
        )
        fig_pl.update_layout(height=max(300, len(chart_data) * 30 + 100), showlegend=False)
        st.plotly_chart(fig_pl, use_container_width=True)

        # P/L detail table
        pl_display = pl_df.copy()
        pl_display['Asset_Class'] = pl_display['Asset_Class'].map(display_map)
        pl_display = pl_display.rename(columns={
            'Asset_Class': '资产类别',
            'Platform': '平台',
            'Name': '名称',
            'Cost_Basis': '成本',
            'Market_Value': '市值',
            'Profit_Loss': '盈亏',
            'Profit_Loss_Rate': '收益率(%)',
        })
        st.dataframe(
            pl_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                '成本': st.column_config.NumberColumn(format="¥%.2f"),
                '市值': st.column_config.NumberColumn(format="¥%.2f"),
                '盈亏': st.column_config.NumberColumn(format="¥%.2f"),
                '收益率(%)': st.column_config.NumberColumn(format="%.2f%%"),
            },
        )

    # ─── Section 5: 收益归因 ───
    st.divider()
    st.subheader("收益归因")
    st.caption("基于 TWR 口径：各类别 NAV 涨跌幅 × 期初权重，与基金总览净值口径一致。各类别贡献率之和 ≈ 基金总 NAV 变化幅度。")

    from datetime import timedelta

    RANGE_OPTIONS = {
        '本周':  8,
        '近1月': 35,
        '近3月': 100,
        '近6月': 190,
        '近1年': 370,
        '近2年': 740,
        '全部':  None,
    }

    attr_range = st.selectbox("时间范围", list(RANGE_OPTIONS.keys()), index=6, key='attr_range')

    all_dates = sorted(fund_nav_df['Date'].unique())
    end_date  = all_dates[-1]

    if RANGE_OPTIONS[attr_range] is None:
        start_date = all_dates[0]
    else:
        cutoff = (pd.Timestamp(end_date) - timedelta(days=RANGE_OPTIONS[attr_range])).strftime('%Y-%m-%d')
        candidates = [d for d in all_dates if d >= cutoff]
        start_date = candidates[0] if candidates else all_dates[0]

    n_snapshots = len([d for d in all_dates if start_date <= d <= end_date])
    st.caption(f"统计区间：{start_date} → {end_date}，共 {n_snapshots} 个快照")

    if n_snapshots < 2:
        st.info("当前时间范围内快照不足 2 个，无法计算归因。请选择更长的时间范围。")
    else:
        attr_rows = compute_attribution(raw_df, fund_nav_df, class_nav_dict, start_date, end_date)

        if not attr_rows:
            st.warning("归因计算失败，请检查数据。")
        else:
            # 瀑布图数据（排除 Cash）
            wf_rows = [r for r in attr_rows if r['contribution_pct'] is not None]
            total_contribution = sum(r['contribution_pct'] for r in wf_rows)

            wf_data = []
            for r in wf_rows:
                wf_data.append({
                    'label': r['display_name'],
                    'value': r['contribution_pct'],
                    'type': 'relative',
                })
            wf_data.append({'label': '合计', 'value': total_contribution, 'type': 'absolute'})

            attr_left, attr_right = st.columns([6, 4])

            with attr_left:
                fig_attr = go.Figure(go.Waterfall(
                    orientation='v',
                    measure=[d['type'] for d in wf_data],
                    x=[d['label'] for d in wf_data],
                    y=[d['value'] for d in wf_data],
                    connector={'line': {'color': '#888'}},
                    increasing={'marker': {'color': '#2e7d32'}},
                    decreasing={'marker': {'color': '#d32f2f'}},
                    totals={'marker': {'color': '#1565c0'}},
                    texttemplate='%{y:+.2f}%',
                    textposition='outside',
                ))
                fig_attr.update_layout(
                    height=400,
                    margin=dict(t=40, b=60),
                    yaxis_title='贡献率 (%)',
                    yaxis_tickformat='.2f',
                )
                st.plotly_chart(fig_attr, use_container_width=True)

            with attr_right:
                # 归因明细表格
                table_rows = []
                for r in wf_rows:
                    table_rows.append({
                        '资产类别': r['display_name'],
                        '期初NAV': f"{r['nav_start']:.4f}" if r['nav_start'] else '—',
                        '期末NAV': f"{r['nav_end']:.4f}" if r['nav_end'] else '—',
                        'NAV涨跌幅': f"{r['nav_return_pct']:+.2f}%" if r['nav_return_pct'] is not None else '—',
                        '期初权重': f"{r['weight_start']:.1f}%",
                        '贡献率': f"{r['contribution_pct']:+.2f}%" if r['contribution_pct'] is not None else '—',
                    })
                # 合计行
                table_rows.append({
                    '资产类别': '**合计**',
                    '期初NAV': '—',
                    '期末NAV': '—',
                    'NAV涨跌幅': '—',
                    '期初权重': '—',
                    '贡献率': f"{total_contribution:+.2f}%",
                })
                # Cash 行
                cash_row = next((r for r in attr_rows if r['asset_class'] == 'Cash'), None)
                if cash_row:
                    table_rows.append({
                        '资产类别': f"{cash_row['display_name']}（不参与归因）",
                        '期初NAV': '—',
                        '期末NAV': '—',
                        'NAV涨跌幅': '—',
                        '期初权重': f"{cash_row['weight_start']:.1f}%",
                        '贡献率': '—',
                    })
                attr_df = pd.DataFrame(table_rows)
                st.dataframe(attr_df, use_container_width=True, hide_index=True)

    # ─── Section 6: 再平衡建议 ───
    st.divider()
    st.subheader("再平衡建议")
    st.caption("基于目标配置比例，计算各类别当前偏差，叠加市场温度计信号。仅供参考，不自动操作。")

    # 编辑目标配置比例
    with st.expander("编辑目标配置比例", expanded=False):
        st.caption("修改后点击保存，Dashboard 立即更新。各类别合计须等于 100%。")
        ta_cols = st.columns(4)
        new_alloc = {}
        for i, (cls, display) in enumerate(CLASS_DISPLAY_NAMES.items()):
            with ta_cols[i % 4]:
                new_alloc[cls] = st.number_input(
                    display,
                    min_value=0, max_value=100,
                    value=int(round(target_alloc.get(cls, 0.0) * 100)),
                    step=1, key=f'ta_{cls}',
                ) / 100.0
        ta_total = sum(new_alloc.values())
        ta_color = 'red' if abs(ta_total - 1.0) > 0.005 else 'green'
        st.markdown(
            f"合计：<span style='color:{ta_color}; font-weight:bold'>{ta_total*100:.1f}%</span>",
            unsafe_allow_html=True,
        )
        if st.button("💾 保存目标比例", disabled=(abs(ta_total - 1.0) > 0.005), key='save_ta'):
            save_target_allocation(os.path.dirname(csv_path), new_alloc)
            target_alloc = new_alloc
            st.success("已保存")
            st.rerun()

    # 计算再平衡数据
    latest_date_rb = raw_df['Date'].max()
    fund_tv_rb = raw_df[raw_df['Date'] == latest_date_rb]['Total_Value'].sum()

    # 温度计信号（在 Tab1 内重新计算，market_data 已全局加载）
    _rb_market = get_market_data()
    _rb_vix   = (_rb_market.get('vix') or {}).get('price')
    _rb_vxn   = (_rb_market.get('vxn') or {}).get('price')
    _rb_qvix  = (_rb_market.get('qvix') or {}).get('price')
    _rb_pe_sp  = ((_rb_market.get('pe_sp500') or {}).get('manual_override')
                  or (_rb_market.get('pe_sp500') or {}).get('value'))
    _rb_pe_ndx = ((_rb_market.get('pe_ndx100') or {}).get('manual_override')
                  or (_rb_market.get('pe_ndx100') or {}).get('value'))
    _rb_pe_csi300 = (_rb_market.get('pe_csi300') or {}).get('value')
    _rb_gold_entry = _rb_market.get('gold')
    _rb_gold_bias = None
    if _rb_gold_entry and _rb_gold_entry.get('ma200'):
        _rb_gold_bias = (_rb_gold_entry['price'] - _rb_gold_entry['ma200']) / _rb_gold_entry['ma200'] * 100

    _CLASS_SIGNAL = {
        'US_Blend_Fund':  lookup_multiplier(_rb_pe_sp,  _rb_vix, 'sp500'),
        'US_Growth_Fund': lookup_multiplier(_rb_pe_ndx, _rb_vxn, 'ndx100'),
        'CN_Index_Fund':  lookup_a_share_multiplier(_rb_pe_csi300, _rb_qvix, 'csi300'),
        'ETF_Stock':      lookup_a_share_multiplier(_rb_pe_csi300, _rb_qvix, 'csi300'),
        'Gold':           lookup_gold_multiplier(_rb_gold_bias, _rb_vix),
        'Fixed_Income':   '—',
        'Company_Stock':  '—',
        'Cash':           '—',
    }

    def _signal_color(sig):
        if sig in ('—', None): return '#9E9E9E'
        if sig == '暂停':       return '#9E9E9E'
        if sig == '顶格':       return '#FF6F00'
        val = float(sig.replace('x', '')) if sig.endswith('x') else 1.0
        if val >= 2.0: return '#1B5E20'
        if val >= 1.0: return '#388E3C'
        return '#81C784'

    # 构建再平衡行数据
    rb_rows = []
    for cls, display in CLASS_DISPLAY_NAMES.items():
        cur_alloc_row = allocation_df[allocation_df['Asset_Class'] == cls]
        cur_pct = float(cur_alloc_row.iloc[0]['Allocation_Percent']) if not cur_alloc_row.empty else 0.0
        cur_tv  = float(cur_alloc_row.iloc[0]['Total_Value'])        if not cur_alloc_row.empty else 0.0
        tgt_pct = target_alloc.get(cls, 0.0)
        dev_pct = cur_pct - tgt_pct
        suggest_amount = (tgt_pct - cur_pct) * fund_tv_rb  # 正=买入，负=卖出
        signal = _CLASS_SIGNAL.get(cls, '—')
        rb_rows.append({
            'cls': cls,
            'display': display,
            'cur_tv': cur_tv,
            'cur_pct': cur_pct * 100,
            'tgt_pct': tgt_pct * 100,
            'dev_pct': dev_pct * 100,
            'suggest_amount': suggest_amount,
            'signal': signal,
        })

    # 左列：偏差柱状图，右列：明细表格
    rb_left, rb_right = st.columns([55, 45])

    with rb_left:
        non_cash = [r for r in rb_rows if r['cls'] != 'Cash']
        bar_colors = ['#2e7d32' if r['dev_pct'] < 0 else '#d32f2f' for r in non_cash]
        fig_rb = go.Figure()
        fig_rb.add_trace(go.Bar(
            x=[r['display'] for r in non_cash],
            y=[r['dev_pct'] for r in non_cash],
            marker_color=bar_colors,
            text=[f"{r['cur_pct']:.1f}% / {r['tgt_pct']:.1f}%" for r in non_cash],
            textposition='outside',
            customdata=[[r['signal']] for r in non_cash],
            hovertemplate='%{x}<br>当前: %{text}<br>偏差: %{y:+.1f}%<br>信号: %{customdata[0]}<extra></extra>',
        ))
        fig_rb.add_hline(y=0, line_dash='dash', line_color='#888', line_width=1)
        fig_rb.update_layout(
            height=400,
            margin=dict(t=40, b=80),
            yaxis_title='偏差（百分点）',
            yaxis_ticksuffix='%',
            showlegend=False,
        )
        # 在 x 轴下方标注温度计信号
        for i, r in enumerate(non_cash):
            sig = r['signal']
            fig_rb.add_annotation(
                x=r['display'], y=-max(abs(r['dev_pct']) for r in non_cash) * 0.15 - 1.5,
                text=sig, showarrow=False,
                font=dict(size=11, color=_signal_color(sig)),
                yref='y',
            )
        st.plotly_chart(fig_rb, use_container_width=True)

    with rb_right:
        table_data = []
        for r in rb_rows:
            if r['cls'] == 'Cash':
                op = '参考'
            elif abs(r['suggest_amount']) < 100:
                op = '持平'
            elif r['suggest_amount'] > 0:
                op = f"买入 ¥{r['suggest_amount']:,.0f}"
            else:
                op = f"卖出 ¥{abs(r['suggest_amount']):,.0f}"
            table_data.append({
                '资产类别':   r['display'],
                '当前市值':   f"¥{r['cur_tv']:,.0f}",
                '当前%':      f"{r['cur_pct']:.1f}%",
                '目标%':      f"{r['tgt_pct']:.1f}%",
                '偏差':       f"{r['dev_pct']:+.1f}%",
                '建议操作':   op,
                '温度计':     r['signal'],
            })
        rb_df = pd.DataFrame(table_data)
        st.dataframe(rb_df, use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════
# Tab 2: Weekly Update
# ═══════════════════════════════════════════════════════════

with tab_update:

    st.header("周报更新")

    # ─── Info bar ───
    last_snapshot_date = raw_df['Date'].max()
    last_snapshot_count = len(raw_df[raw_df['Date'] == last_snapshot_date])
    st.markdown(f"**上次快照**: `{last_snapshot_date}` | **持仓数**: {last_snapshot_count}")

    st.divider()

    # ─── New snapshot date ───
    last_date_obj = pd.to_datetime(last_snapshot_date).date()
    default_new_date = last_date_obj + timedelta(days=7)
    new_date = st.date_input(
        "新快照日期",
        value=default_new_date,
        min_value=last_date_obj + timedelta(days=1),
        help="必须晚于上次快照日期",
    )
    new_date_str = new_date.strftime('%Y-%m-%d')

    # ─── Template initialization ───
    if 'update_template' not in st.session_state:
        latest_rows = raw_df[raw_df['Date'] == last_snapshot_date].copy()
        template = latest_rows.drop(columns=['Date']).reset_index(drop=True)
        template['Net_Cash_Flow'] = 0.0
        st.session_state['update_template'] = template

    # ─── Batch CSV Import ───

    def _parse_csv_input(text, prev_template):
        """Parse CSV/TSV text into a DataFrame matching portfolio schema."""
        from io import StringIO

        text = text.strip()
        if not text:
            return None, "输入为空"

        # Auto-detect separator: tab vs comma
        first_line = text.split('\n')[0]
        sep = '\t' if '\t' in first_line else ','

        # Check if first line is a header
        expected_cols = {'Asset_Class', 'Platform', 'Name', 'Code', 'Currency',
                         'Exchange_Rate', 'Shares', 'Current_Price', 'Total_Value', 'Net_Cash_Flow'}
        first_fields = {f.strip() for f in first_line.split(sep)}
        has_header = len(first_fields & expected_cols) >= 3

        try:
            if has_header:
                df = pd.read_csv(StringIO(text), sep=sep)
            else:
                df = pd.read_csv(StringIO(text), sep=sep, header=None,
                                 names=list(prev_template.columns))
        except Exception as e:
            return None, f"解析失败: {e}"

        # Ensure all expected columns exist
        for col in expected_cols:
            if col not in df.columns:
                df[col] = None

        # Fill missing fields from previous template by matching on Code (primary) or Name (fallback)
        prev_by_code = prev_template.set_index('Code')
        prev_by_name = prev_template.set_index('Name')
        for i, row in df.iterrows():
            code = row.get('Code')
            name = row.get('Name')
            prev_row = None
            if code and str(code).strip() and str(code).strip() in prev_by_code.index:
                prev_row = prev_by_code.loc[str(code).strip()]
            elif name and name in prev_by_name.index:
                prev_row = prev_by_name.loc[name]
            if prev_row is not None:
                for col in ['Asset_Class', 'Platform', 'Code', 'Currency', 'Exchange_Rate']:
                    if pd.isna(row.get(col)) or str(row.get(col, '')).strip() == '':
                        df.at[i, col] = prev_row[col]

        # Defaults
        df['Net_Cash_Flow'] = df['Net_Cash_Flow'].fillna(0.0)
        df['Code'] = df['Code'].fillna('')
        df['Exchange_Rate'] = pd.to_numeric(df['Exchange_Rate'], errors='coerce').fillna(1.0)

        # Ensure numeric columns
        for col in ['Shares', 'Current_Price', 'Total_Value', 'Net_Cash_Flow', 'Exchange_Rate']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

        # Reorder to match template columns
        df = df[list(prev_template.columns)]
        return df, None

    with st.expander("批量导入 (Paste CSV)", expanded=False):
        st.caption(
            "粘贴 CSV 或 Tab 分隔文本。表头可选。"
            "至少需要: Name, Shares, Current_Price, Total_Value。"
            "缺失字段将从上周模板补全。"
        )
        csv_input = st.text_area(
            "粘贴数据",
            height=200,
            placeholder="Asset_Class\tPlatform\tName\tCode\tCurrency\tExchange_Rate\tShares\tCurrent_Price\tTotal_Value\tNet_Cash_Flow\n"
                        "US_Index_Fund\t标普场外\t摩根标普500\tQDII A\tCNY\t1.0\t13108.21\t1.62\t21235.30\t0",
            key="csv_batch_input",
        )
        if st.button("解析", key="parse_csv_batch"):
            parsed_df, err = _parse_csv_input(csv_input, st.session_state['update_template'])
            if err:
                st.error(err)
            else:
                st.session_state['update_template'] = parsed_df
                st.success(f"已解析 {len(parsed_df)} 行")
                st.rerun()

    # ─── Reset button + FX refresh ───
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("重置为上周模板", type="secondary"):
            latest_rows = raw_df[raw_df['Date'] == last_snapshot_date].copy()
            template = latest_rows.drop(columns=['Date']).reset_index(drop=True)
            template['Net_Cash_Flow'] = 0.0
            st.session_state['update_template'] = template
            st.rerun()
    with btn_col2:
        if st.button("刷新汇率", type="secondary", help="从 frankfurter.app 获取最新汇率"):
            updated = 0
            errors_fx = []
            template = st.session_state['update_template']
            for i, row in template.iterrows():
                currency = row.get('Currency', 'CNY')
                if currency and currency != 'CNY':
                    try:
                        rate = get_exchange_rate(currency, 'CNY')
                        template.at[i, 'Exchange_Rate'] = round(rate, 4)
                        updated += 1
                    except Exception as e:
                        errors_fx.append(f"{row.get('Name', '?')} ({currency}): {e}")
            st.session_state['update_template'] = template
            if updated > 0:
                st.success(f"已更新 {updated} 个汇率")
            if errors_fx:
                for err in errors_fx:
                    st.warning(f"汇率获取失败: {err}")
            if updated > 0:
                st.rerun()

    # ─── 刷新净值 ───
    _rf_col1, _rf_col2, _rf_col3 = st.columns([1, 1, 4])
    with _rf_col1:
        if st.button("🔄 刷新净值", key="refresh_prices", help="自动拉取最新净值填入编辑区，固定收益需手动确认"):
            st.session_state['_refresh_prices'] = True
            st.rerun()
    with _rf_col2:
        if st.button("⚙ 价格来源", key="price_source_btn", help="配置哪些标的走 yfinance 而非天天基金"):
            st.session_state['_show_price_source'] = not st.session_state.get('_show_price_source', False)

    if st.session_state.get('_show_price_source'):
        with st.container(border=True):
            st.caption("**价格来源配置**：添加映射后，对应标的刷新净值时走 yfinance；未添加的6位数字基金走天天基金。「展示基本面」勾选后在 Market Tab 个股基本面面板展示。")
            from fundamentals import load_yf_symbols, add_yf_symbol, remove_yf_symbol, get_yf_symbol, get_show_fundamentals, update_show_fundamentals
            _ps_map = load_yf_symbols(os.path.dirname(csv_path))
            _ps_entries = {k: v for k, v in _ps_map.items() if not k.startswith('_')}
            if _ps_entries:
                for _ps_code, _ps_entry in _ps_entries.items():
                    _ps_sym  = get_yf_symbol(_ps_map, _ps_code)
                    _ps_show = get_show_fundamentals(_ps_map, _ps_code)
                    _pc1, _pc2, _pc3, _pc4 = st.columns([2, 2, 2, 1])
                    with _pc1: st.code(_ps_code)
                    with _pc2: st.code(_ps_sym)
                    with _pc3:
                        _ns = st.checkbox('展示基本面', value=_ps_show, key=f'ps_show_{_ps_code}')
                        if _ns != _ps_show:
                            update_show_fundamentals(os.path.dirname(csv_path), _ps_code, _ns)
                            st.rerun()
                    with _pc4:
                        if st.button('🗑️', key=f'ps_del_{_ps_code}'):
                            remove_yf_symbol(os.path.dirname(csv_path), _ps_code)
                            st.rerun()
            _pa1, _pa2, _pa3, _pa4 = st.columns([2, 2, 2, 1])
            with _pa1: _ps_new_code = st.text_input('Code', key='ps_new_code', placeholder='如 512890')
            with _pa2: _ps_new_sym  = st.text_input('YF Symbol', key='ps_new_sym', placeholder='如 512890.SS')
            with _pa3: _ps_new_show = st.checkbox('展示基本面', value=False, key='ps_new_show')
            with _pa4:
                st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
                if st.button('➕', key='ps_add'):
                    if _ps_new_code.strip() and _ps_new_sym.strip():
                        add_yf_symbol(os.path.dirname(csv_path), _ps_new_code.strip(),
                                      _ps_new_sym.strip(), show_fundamentals=_ps_new_show)
                        st.rerun()

    if st.session_state.pop('_refresh_prices', False):
        from price_fetcher import fetch_latest_prices
        with st.spinner("拉取最新净值..."):
            _price_results = fetch_latest_prices(raw_df, os.path.dirname(csv_path))
        # 写入 session_state 的编辑模板
        _template = st.session_state['update_template'].copy()
        _ok, _manual, _err = 0, 0, 0
        for i, row in _template.iterrows():
            code = str(row.get('Code', ''))
            res = _price_results.get(code)
            if res is None:
                continue
            if res['status'] == 'ok' and res['price'] is not None:
                _template.at[i, 'Current_Price'] = res['price']
                _ok += 1
            elif res['status'] == 'manual':
                _manual += 1
            else:
                _err += 1
        st.session_state['update_template'] = _template
        st.session_state['_refresh_summary'] = {'ok': _ok, 'manual': _manual, 'err': _err, 'results': _price_results}
        st.rerun()

    if '_refresh_summary' in st.session_state:
        _s = st.session_state.pop('_refresh_summary')
        _ok, _manual, _err = _s['ok'], _s['manual'], _s['err']
        st.success(f"已刷新 {_ok} 个标的  |  {_manual} 个需手动确认  |  {_err} 个失败")
        # 展示详情
        with st.expander("刷新详情", expanded=False):
            for code, res in _s['results'].items():
                if res['status'] == 'ok':
                    st.markdown(f"✅ `{code}` — {res['price']:.4f}（{res['msg']}，{res.get('date','')}）")
                elif res['status'] == 'manual':
                    st.markdown(f"⚠️ `{code}` — {res['msg']}")
                else:
                    st.markdown(f"❌ `{code}` — {res['msg']}")

    # ─── Editable table ───
    st.markdown("**编辑持仓** (可增删行，修改价格/份额/市值。买卖 NCF 通过调仓辅助器自动填写，外部入金/出金记在 Cash 行的 Net_Cash_Flow)")

    edited_df = st.data_editor(
        st.session_state['update_template'],
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "Asset_Class": st.column_config.SelectboxColumn(
                "Asset_Class",
                options=sorted(VALID_ASSET_CLASSES),
                required=True,
            ),
            "Platform": st.column_config.TextColumn("Platform", required=True),
            "Name": st.column_config.TextColumn("Name", required=True),
            "Code": st.column_config.TextColumn("Code", default=""),
            "Currency": st.column_config.SelectboxColumn(
                "Currency",
                options=["CNY", "HKD", "USD", "EUR"],
                required=True,
            ),
            "Exchange_Rate": st.column_config.NumberColumn(
                "Exchange_Rate", format="%.4f", min_value=0.0001, default=1.0,
                help="Currency to CNY (e.g. EUR→CNY ≈ 7.92)",
            ),
            "Shares": st.column_config.NumberColumn("Shares", format="%.2f", min_value=0.0),
            "Current_Price": st.column_config.NumberColumn("Current_Price", format="%.4f", min_value=0.0),
            "Total_Value": st.column_config.NumberColumn("Total_Value", format="%.2f", min_value=0.0),
            "Net_Cash_Flow": st.column_config.NumberColumn("Net_Cash_Flow", format="%.2f", default=0.0),
        },
        column_order=[
            "Asset_Class", "Platform", "Name", "Code", "Currency", "Exchange_Rate",
            "Shares", "Current_Price", "Total_Value", "Net_Cash_Flow",
        ],
        key="weekly_editor",
    )

    # ─── 重算市值（在 data_editor 之后，读 edited_df）───
    if st.button("🔄 重算市值 (Shares × Price × Rate)", type="secondary",
                 help="对所有 Shares > 0 且 Current_Price > 0 的非 Cash 行，自动计算 Total_Value = Shares × Current_Price × Exchange_Rate。手动填写的 Total_Value 仍可在表格中直接覆盖。"):
        recalc = edited_df.copy()
        mask = (
            (recalc['Asset_Class'] != 'Cash') &
            (pd.to_numeric(recalc['Shares'], errors='coerce').fillna(0) > 0) &
            (pd.to_numeric(recalc['Current_Price'], errors='coerce').fillna(0) > 0)
        )
        recalc.loc[mask, 'Total_Value'] = (
            pd.to_numeric(recalc.loc[mask, 'Shares'], errors='coerce') *
            pd.to_numeric(recalc.loc[mask, 'Current_Price'], errors='coerce') *
            pd.to_numeric(recalc.loc[mask, 'Exchange_Rate'], errors='coerce').fillna(1.0)
        ).round(2)
        updated_count = mask.sum()
        st.session_state['update_template'] = recalc
        st.success(f"已重算 {updated_count} 行市值")
        st.rerun()

    # ─── 调仓辅助器（在编辑持仓表之后，确保 edited_df 已赋值）───
    with st.expander("⚖️ 调仓辅助器（设置每行 NCF）", expanded=False):
        st.caption(
            "登记本期所有买卖及外部资金操作。点击「应用」后：\n"
            "① 更新 Cash 行 Total_Value；"
            "② 在相关资产行写入 Net_Cash_Flow（买入为正，卖出为负）；"
            "③ 新增标的自动追加到持仓表底部（请之后手动填写份额/价格/市值）。"
        )

        if 'rebalance_entries' not in st.session_state:
            st.session_state['rebalance_entries'] = []

        col_add1, col_add2, col_add3, col_add4 = st.columns(4)
        with col_add1:
            if st.button("＋ 买入", key="rb_add_buy"):
                st.session_state['rebalance_entries'].append(
                    {'type': '买入', 'asset_name': '', 'amount': 0.0, 'price': 0.0, 'fee': 0.0, 'is_new': False, 'new_asset': {}})
                st.rerun()
        with col_add2:
            if st.button("＋ 卖出", key="rb_add_sell"):
                st.session_state['rebalance_entries'].append(
                    {'type': '卖出', 'asset_name': '', 'amount': 0.0, 'price': 0.0, 'fee': 0.0, 'is_new': False, 'new_asset': {}})
                st.rerun()
        with col_add3:
            if st.button("＋ 外部入金", key="rb_add_ext_in"):
                st.session_state['rebalance_entries'].append(
                    {'type': '外部入金', 'asset_name': '', 'amount': 0.0, 'price': 0.0, 'fee': 0.0, 'is_new': False, 'new_asset': {}})
                st.rerun()
        with col_add4:
            if st.button("＋ 外部取出", key="rb_add_ext_out"):
                st.session_state['rebalance_entries'].append(
                    {'type': '外部取出', 'asset_name': '', 'amount': 0.0, 'price': 0.0, 'fee': 0.0, 'is_new': False, 'new_asset': {}})
                st.rerun()

        # Build asset options: "Name (Code)" for clarity, excluding Cash
        asset_rows = edited_df[edited_df['Asset_Class'] != 'Cash'].dropna(subset=['Name'])
        def _asset_label(row):
            code = str(row.get('Code', '')).strip()
            name = str(row.get('Name', '')).strip()
            return f"{name} ({code})" if code else name
        asset_options_labels = [_asset_label(r) for _, r in asset_rows.iterrows()] + ['新增标的']
        # Keep a mapping from label back to Name (for NCF writing)
        asset_label_to_name = {_asset_label(r): r['Name'] for _, r in asset_rows.iterrows()}
        asset_label_to_name['新增标的'] = '新增标的'

        entries = st.session_state['rebalance_entries']
        to_remove = []
        type_labels = {'买入': '🟢 买入', '卖出': '🔴 卖出', '外部入金': '🔵 外部入金', '外部取出': '🟠 外部取出'}

        for idx, entry in enumerate(entries):
            c1, c2, c3, c4 = st.columns([1.0, 2.5, 1.8, 0.5])
            with c1:
                st.markdown("　")  # label 占位，与其他列对齐
                st.markdown(f"**{type_labels.get(entry['type'], entry['type'])}**")
            with c2:
                if entry['type'] in ('买入', '卖出'):
                    current_label = entry.get('asset_label', '')
                    selected_label = st.selectbox(
                        "资产标的",
                        options=[''] + asset_options_labels,
                        index=([''] + asset_options_labels).index(current_label)
                              if current_label in ([''] + asset_options_labels) else 0,
                        key=f"rb_asset_{idx}",
                        placeholder="选择资产（名称 + 代码）...",
                    )
                    entry['asset_label'] = selected_label
                    entry['asset_name'] = asset_label_to_name.get(selected_label, selected_label)
                    entry['is_new'] = (selected_label == '新增标的')
                else:
                    st.markdown("*外部资金*")
                    entry['asset_name'] = ''
                    entry['is_new'] = False
            with c3:
                entry['amount'] = st.number_input(
                    "买入/卖出总金额 (CNY)", value=float(entry['amount']), min_value=0.0, step=100.0,
                    format="%.2f", key=f"rb_amt_{idx}",
                    help="本次操作的总金额（人民币），买入填支付金额，卖出填到账金额",
                )
            with c4:
                st.markdown("　")  # label 占位
                if st.button("✕", key=f"rb_del_{idx}"):
                    to_remove.append(idx)

            # 买入/卖出：展示成交价和手续费（可选，用于 transaction.csv 记录）
            if entry['type'] in ('买入', '卖出'):
                p_col, f_col = st.columns(2)
                with p_col:
                    entry['price'] = st.number_input(
                        "申购/赎回确认净值（单价，可选）",
                        value=float(entry.get('price', 0.0)), min_value=0.0, format="%.4f",
                        key=f"rb_price_{idx}",
                        help="基金净值或黄金单价（原始货币），从基金平台交易记录查询。不填则用快照价格估算。",
                    )
                with f_col:
                    entry['fee'] = st.number_input(
                        "手续费 CNY（可选，默认0）",
                        value=float(entry.get('fee', 0.0)), min_value=0.0, format="%.2f",
                        key=f"rb_fee_{idx}",
                        help="已被扣除的手续费，不含在 Amount_CNY 中",
                    )

            # 新增标的子表单
            if entry.get('is_new'):
                st.info("新标的信息 — 应用后将追加一行到持仓表，请之后手动填写份额/价格/市值")
                na = entry.get('new_asset', {})
                nc1, nc2, nc3, nc4, nc5 = st.columns([1.5, 1.5, 2.0, 1.5, 1.2])
                with nc1:
                    na['Asset_Class'] = st.selectbox("类别", options=sorted(VALID_ASSET_CLASSES),
                        index=sorted(VALID_ASSET_CLASSES).index(na['Asset_Class']) if na.get('Asset_Class') in VALID_ASSET_CLASSES else 0,
                        key=f"rb_new_ac_{idx}")
                with nc2:
                    na['Platform'] = st.text_input("平台", value=na.get('Platform', ''), key=f"rb_new_plat_{idx}")
                with nc3:
                    na['Name'] = st.text_input("名称", value=na.get('Name', ''), key=f"rb_new_name_{idx}")
                with nc4:
                    na['Code'] = st.text_input("代码", value=na.get('Code', ''), key=f"rb_new_code_{idx}")
                with nc5:
                    na['Currency'] = st.selectbox("货币", options=["CNY", "HKD", "USD", "EUR"],
                        index=["CNY", "HKD", "USD", "EUR"].index(na['Currency']) if na.get('Currency') in ["CNY", "HKD", "USD", "EUR"] else 0,
                        key=f"rb_new_ccy_{idx}")
                entry['new_asset'] = na

        for idx in reversed(to_remove):
            st.session_state['rebalance_entries'].pop(idx)
        if to_remove:
            st.rerun()

        if entries:
            st.divider()

            total_buy  = sum(e['amount'] for e in entries if e['type'] == '买入')
            total_sell = sum(e['amount'] for e in entries if e['type'] == '卖出')
            ext_in     = sum(e['amount'] for e in entries if e['type'] == '外部入金')
            ext_out    = sum(e['amount'] for e in entries if e['type'] == '外部取出')
            cash_delta   = total_sell - total_buy + ext_in - ext_out
            external_ncf = ext_in - ext_out

            prev_cash_rows = edited_df[edited_df['Asset_Class'] == 'Cash']
            prev_cash_tv   = prev_cash_rows['Total_Value'].sum() if len(prev_cash_rows) > 0 else 0.0
            new_cash_tv    = prev_cash_tv + cash_delta

            res_col1, res_col2, res_col3, res_col4, res_col5 = st.columns(5)
            with res_col1:
                st.metric("当前 Cash", f"¥{prev_cash_tv:,.0f}")
            with res_col2:
                st.metric("操作后 Cash", f"¥{new_cash_tv:,.0f}", delta=f"{cash_delta:+,.0f}")
            with res_col3:
                st.metric("Cash NCF（外部）", f"¥{external_ncf:+,.0f}", help="只有外部入金/取出计入 NCF")
            with res_col4:
                st.metric("买入合计", f"¥{total_buy:,.0f}")
            with res_col5:
                st.metric("卖出合计", f"¥{total_sell:,.0f}")

            if st.button("✅ 应用到持仓表", type="primary", key="rb_apply"):
                template = edited_df.copy()

                # 预检警告（不阻断）
                if new_cash_tv < 0:
                    st.warning(f"应用后 Cash 将为负 (¥{new_cash_tv:,.0f})，请检查买入金额")
                if total_buy > prev_cash_tv and prev_cash_tv > 0:
                    st.warning(f"买入合计 ¥{total_buy:,.0f} 超过当前 Cash ¥{prev_cash_tv:,.0f}")
                for e in entries:
                    if e['type'] == '卖出' and e['asset_name'] and not e['is_new']:
                        mask = template['Name'] == e['asset_name']
                        if mask.any():
                            asset_tv = template.loc[mask, 'Total_Value'].iloc[0]
                            if e['amount'] > asset_tv:
                                st.warning(f"卖出「{e['asset_name']}」¥{e['amount']:,.0f} 超过当前市值 ¥{asset_tv:,.0f}")

                # 更新 Cash 行
                cash_mask = template['Asset_Class'] == 'Cash'
                if cash_mask.any():
                    if cash_mask.sum() == 1:
                        ci = template[cash_mask].index[0]
                        template.at[ci, 'Total_Value']   = round(new_cash_tv, 2)
                        template.at[ci, 'Shares']        = round(new_cash_tv, 2)
                        template.at[ci, 'Net_Cash_Flow'] = round(external_ncf, 2)
                    else:
                        st.warning("检测到多条 Cash 行，请手动更新 Cash 的 Total_Value 和 NCF")
                else:
                    st.warning("未找到 Cash 行，请手动更新")

                # 写入各资产 NCF（已有持仓）
                for e in entries:
                    if e['type'] not in ('买入', '卖出') or not e['asset_name'] or e['is_new']:
                        continue
                    mask = template['Name'] == e['asset_name']
                    if not mask.any():
                        st.warning(f"找不到资产「{e['asset_name']}」，跳过 NCF 写入")
                        continue
                    if mask.sum() > 1:
                        st.warning(f"「{e['asset_name']}」有 {mask.sum()} 条匹配行，NCF 写入到第一条，请手动核查其余行")
                    ncf_sign = +1 if e['type'] == '买入' else -1
                    idx_row = template[mask].index[0]
                    template.at[idx_row, 'Net_Cash_Flow'] = round(
                        (template.at[idx_row, 'Net_Cash_Flow'] or 0) + ncf_sign * e['amount'], 2
                    )

                # 追加新标的行
                new_rows = []
                for e in entries:
                    if e['type'] not in ('买入', '卖出') or not e.get('is_new'):
                        continue
                    na = e.get('new_asset', {})
                    if not na.get('Name'):
                        st.warning("新增标的缺少 Name 字段，跳过追加")
                        continue
                    if na['Name'] in template['Name'].values:
                        st.warning(f"「{na['Name']}」已存在于持仓表，建议改用「买入」选择现有资产")
                    ncf_sign = +1 if e['type'] == '买入' else -1
                    new_rows.append({
                        'Asset_Class':   na.get('Asset_Class', ''),
                        'Platform':      na.get('Platform', ''),
                        'Name':          na.get('Name', ''),
                        'Code':          na.get('Code', ''),
                        'Currency':      na.get('Currency', 'CNY'),
                        'Exchange_Rate': 1.0,
                        'Shares':        0.0,
                        'Current_Price': 0.0,
                        'Total_Value':   0.0,
                        'Net_Cash_Flow': round(ncf_sign * e['amount'], 2),
                    })
                if new_rows:
                    template = pd.concat([template, pd.DataFrame(new_rows)], ignore_index=True)

                # ─── 写入 transaction.csv ───
                tx_rows = []
                for e in entries:
                    if e['type'] not in ('买入', '卖出') or not e['asset_name'] or e['is_new']:
                        continue
                    # 查找资产的 Asset_Class、Platform、Code（用于完整记录）
                    asset_mask = template['Name'] == e['asset_name']
                    asset_row = template[asset_mask].iloc[0] if asset_mask.any() else None
                    # 成交价：用户填了则用，否则从快照 Current_Price 估算
                    price_val = e.get('price', 0.0)
                    if not price_val or price_val <= 0:
                        price_val = float(asset_row['Current_Price']) if asset_row is not None else 0.0
                    tx_rows.append({
                        'Date':        new_date_str,
                        'Asset_Class': asset_row['Asset_Class'] if asset_row is not None else '',
                        'Platform':    asset_row['Platform']    if asset_row is not None else '',
                        'Name':        e['asset_name'],
                        'Code':        asset_row['Code']        if asset_row is not None else '',
                        'Type':        e['type'],
                        'Amount_CNY':  round(e['amount'], 2),
                        'Price':       round(price_val, 4),
                        'Fee_CNY':     round(e.get('fee', 0.0), 2),
                    })
                if tx_rows:
                    tx_df = pd.DataFrame(tx_rows)
                    tx_path = csv_path.replace('portfolio.csv', 'transaction.csv')
                    if os.path.exists(tx_path):
                        existing = pd.read_csv(tx_path)
                        tx_df = pd.concat([existing, tx_df], ignore_index=True)
                    tx_df.to_csv(tx_path, index=False)

                st.session_state['update_template'] = template
                st.session_state['rebalance_entries'] = []
                st.rerun()

        if entries and st.button("清空所有条目", key="rb_clear", type="secondary"):
            st.session_state['rebalance_entries'] = []
            st.rerun()

        # ── 从短信导入 ──────────────────────────────────────
        st.divider()
        with st.expander("📱 从短信导入", expanded=False):
            st.caption("粘贴基金确认短信（多条用空行分隔），自动提取信息填入调仓辅助器。")
            sms_text = st.text_area("短信内容", height=150, key="sms_input",
                                    placeholder="【博时基金】尊敬的...确认成功，份额为...净值为...\n\n【南方基金】...")
            if st.button("解析短信", key="sms_parse", type="primary"):
                if sms_text.strip():
                    from sms_parser import parse_sms
                    _holdings = [
                        {'code': str(row['Code']), 'name': str(row['Name'])}
                        for _, row in edited_df[edited_df['Asset_Class'] != 'Cash'].iterrows()
                        if pd.notna(row['Name']) and pd.notna(row['Code'])
                    ]
                    _parsed = parse_sms(sms_text, _holdings)
                    st.session_state['sms_parsed'] = _parsed
                    st.rerun()
                else:
                    st.warning("请先粘贴短信内容")

            # 展示解析结果并允许填入调仓辅助器
            if 'sms_parsed' in st.session_state:
                _parsed = st.session_state['sms_parsed']
                st.markdown("**解析结果：**")
                _any_error = False
                for i, r in enumerate(_parsed):
                    if r.get('parse_error'):
                        st.error(f"第{i+1}条短信无法解析")
                        _any_error = True
                        continue
                    _match_str = f"{r['matched_code']} ({r['matched_name']})" if r['matched_code'] else "❓ 未匹配"
                    _gold_str  = f"（{r['shares']}克）" if r['is_gold'] else ""
                    st.markdown(
                        f"**{i+1}.** {r['confirm_date']} · {r['action']} · "
                        f"{r['fund_name']} → {_match_str}  "
                        f"金额 ¥{r['amount']:,.2f}{_gold_str} · 净值 {r['nav']:.4f} · 份额 {r['shares']}"
                    )
                    # 未匹配时提供下拉选择
                    if not r['matched_code']:
                        _options = [''] + [
                            f"{row['Name']} ({row['Code']})"
                            for _, row in edited_df[edited_df['Asset_Class'] != 'Cash'].iterrows()
                            if pd.notna(row['Name'])
                        ]
                        _sel = st.selectbox(f"手动选择持仓（第{i+1}条）", _options, key=f"sms_match_{i}")
                        if _sel:
                            _name, _code = _sel.rsplit(' (', 1)
                            _code = _code.rstrip(')')
                            _parsed[i]['matched_code'] = _code
                            _parsed[i]['matched_name'] = _name
                            st.session_state['sms_parsed'] = _parsed

                if not _any_error:
                    if st.button("✅ 填入调仓辅助器", key="sms_apply", type="primary"):
                        for r in _parsed:
                            if r.get('parse_error') or not r['matched_code']:
                                continue
                            st.session_state['rebalance_entries'].append({
                                'type':       r['action'],
                                'asset_name': r['matched_name'],
                                'asset_label': f"{r['matched_name']} ({r['matched_code']})",
                                'amount':     r['amount'],
                                'price':      r['nav'],
                                'fee':        0.0,
                                'is_new':     False,
                                'new_asset':  {},
                            })
                        del st.session_state['sms_parsed']
                        st.success(f"已填入 {len([r for r in _parsed if not r.get('parse_error')])} 条记录")
                        st.rerun()

                if st.button("清除解析结果", key="sms_clear"):
                    del st.session_state['sms_parsed']
                    st.rerun()

    # ─── Validation ───

    def validate_snapshot(df, prev_df, new_date_str, last_date_str):
        """Validate the new weekly snapshot. Returns (errors, warnings)."""
        errors = []
        warnings = []

        # Date check
        if new_date_str <= last_date_str:
            errors.append(f"新日期 ({new_date_str}) 必须晚于上次快照 ({last_date_str})")

        # Empty check
        if len(df) == 0:
            errors.append("持仓表为空，至少需要一条记录")
            return errors, warnings

        # Required fields
        for i, row in df.iterrows():
            if pd.isna(row.get('Asset_Class')) or str(row.get('Asset_Class', '')).strip() == '':
                errors.append(f"第 {i+1} 行: Asset_Class 不能为空")
            elif row['Asset_Class'] not in VALID_ASSET_CLASSES:
                errors.append(f"第 {i+1} 行: 无效的 Asset_Class '{row['Asset_Class']}'")
            if pd.isna(row.get('Name')) or str(row.get('Name', '')).strip() == '':
                errors.append(f"第 {i+1} 行: Name 不能为空")
            if pd.isna(row.get('Platform')) or str(row.get('Platform', '')).strip() == '':
                errors.append(f"第 {i+1} 行: Platform 不能为空")

        # Numeric checks
        for i, row in df.iterrows():
            tv = row.get('Total_Value', 0)
            if pd.isna(tv) or tv < 0:
                errors.append(f"第 {i+1} 行 ({row.get('Name', '?')}): Total_Value 不能为负或空")
            shares = row.get('Shares', 0)
            if pd.isna(shares) or shares < 0:
                errors.append(f"第 {i+1} 行 ({row.get('Name', '?')}): Shares 不能为负或空")
            price = row.get('Current_Price', 0)
            if pd.isna(price) or price < 0:
                errors.append(f"第 {i+1} 行 ({row.get('Name', '?')}): Current_Price 不能为负或空")

        # Holdings count change
        prev_count = len(prev_df[prev_df['Date'] == last_date_str])
        if len(df) != prev_count:
            warnings.append(f"持仓数变化: 上周 {prev_count} 条 -> 本周 {len(df)} 条，请确认是否有新增/删除")

        # NCF 异常检查：只在 NCF 远超市值时警告（新语义下每行都可以有 NCF）
        for i, row in df.iterrows():
            ncf = abs(float(row.get('Net_Cash_Flow', 0) or 0))
            tv  = float(row.get('Total_Value', 0) or 0)
            if ncf > 0 and tv > 0 and ncf > tv * 2:
                warnings.append(
                    f"{row.get('Name','?')}: Net_Cash_Flow ¥{ncf:+,.0f} 远超市值 ¥{tv:,.0f}，请核实"
                )

        # 本期现金流汇总（只统计 Cash 行，其他资产行 NCF 是内部调仓，不是外部入金）
        cash_ncf = df[df['Asset_Class'] == 'Cash']['Net_Cash_Flow'].fillna(0).sum()
        if cash_ncf != 0:
            warnings.append(f"本期外部净现金流（Cash 行）: ¥{cash_ncf:+,.2f}")

        # Large price swings
        prev_latest = prev_df[prev_df['Date'] == last_date_str]
        for i, row in df.iterrows():
            name     = row.get('Name', '')
            platform = row.get('Platform', '')
            code     = row.get('Code', '')
            # 用 (Platform, Name, Code) 精确匹配，避免同名不同标的误报
            mask = (
                (prev_latest['Name'] == name) &
                (prev_latest['Platform'] == platform) &
                (prev_latest['Code'].fillna('') == (code or ''))
            )
            prev_row = prev_latest[mask]
            if len(prev_row) > 0:
                old_tv = prev_row.iloc[0]['Total_Value']
                new_tv = row.get('Total_Value', 0)
                if old_tv > 0 and not pd.isna(new_tv) and new_tv > 0:
                    pct_change = abs(new_tv - old_tv) / old_tv
                    if pct_change > 0.2:
                        warnings.append(f"{name}: 市值变化 {pct_change*100:.1f}%，请核实")

        # Total_Value vs Shares * Current_Price * Exchange_Rate mismatch
        for i, row in df.iterrows():
            shares = row.get('Shares', 0) or 0
            price = row.get('Current_Price', 0) or 0
            exchange_rate = row.get('Exchange_Rate', 1.0) or 1.0
            tv = row.get('Total_Value', 0) or 0
            expected = shares * price * exchange_rate
            if expected > 0 and tv > 0 and abs(tv - expected) / expected > 0.05:
                warnings.append(f"{row.get('Name', '?')}: Total_Value({tv:.2f}) != Shares*Price*ExRate({expected:.2f})")

        return errors, warnings

    st.divider()

    # Run validation
    errors, warnings = validate_snapshot(edited_df, raw_df, new_date_str, last_snapshot_date)

    # Display validation results
    if errors:
        for e in errors:
            st.error(e)
    if warnings:
        for w in warnings:
            st.warning(w)
    if not errors and not warnings:
        st.success("数据校验通过")
    elif not errors:
        st.info("有警告信息，但不影响保存")

    # ─── Preview ───
    with st.expander("预览将追加的数据", expanded=False):
        preview_df = edited_df.copy()
        preview_df.insert(0, 'Date', new_date_str)
        st.dataframe(preview_df, use_container_width=True, hide_index=True)
        st.caption(f"共 {len(preview_df)} 行 | 总市值: ¥{preview_df['Total_Value'].sum():,.2f}")

    # ─── Save ───
    st.divider()

    if errors:
        st.button("保存到 CSV", disabled=True, help="请先修正错误")
    else:
        confirm = st.checkbox("我已确认数据无误")
        if st.button("保存到 CSV", type="primary", disabled=not confirm):
            # Build the final rows
            save_df = edited_df.copy()
            save_df.insert(0, 'Date', new_date_str)
            save_df['Code'] = save_df['Code'].fillna('')
            save_df['Exchange_Rate'] = save_df['Exchange_Rate'].fillna(1.0)
            save_df['Net_Cash_Flow'] = save_df['Net_Cash_Flow'].fillna(0.0)

            # Read fresh CSV and append
            existing_df = pd.read_csv(csv_path)
            combined_df = pd.concat([existing_df, save_df], ignore_index=True)

            # Atomic write with file lock
            _atomic_write_csv(combined_df, csv_path)

            st.success(f"已保存 {len(save_df)} 条记录 (日期: {new_date_str}) 到 {os.path.basename(csv_path)}")
            st.balloons()

            # Clear cache and refresh
            st.cache_data.clear()
            del st.session_state['update_template']
            st.rerun()


    st.divider()
    st.header("历史快照管理")

    st.header("历史快照管理")

    all_dates = sorted(raw_df['Date'].unique(), reverse=True)

    if len(all_dates) == 0:
        st.info("暂无历史数据")
    else:
        selected_date = st.selectbox("选择快照日期", options=all_dates, key="history_date")

        snapshot_rows = raw_df[raw_df['Date'] == selected_date].copy()
        st.markdown(f"**{selected_date}** — {len(snapshot_rows)} 条持仓 | 总市值: ¥{snapshot_rows['Total_Value'].sum():,.2f}")

        st.divider()

        # ─── Edit snapshot ───
        st.subheader("编辑快照")

        edit_key = f"history_editor_{selected_date}"
        edit_template = snapshot_rows.drop(columns=['Date']).reset_index(drop=True)

        edited_history = st.data_editor(
            edit_template,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "Asset_Class": st.column_config.SelectboxColumn(
                    "Asset_Class",
                    options=sorted(VALID_ASSET_CLASSES),
                    required=True,
                ),
                "Platform": st.column_config.TextColumn("Platform", required=True),
                "Name": st.column_config.TextColumn("Name", required=True),
                "Code": st.column_config.TextColumn("Code", default=""),
                "Currency": st.column_config.SelectboxColumn(
                    "Currency",
                    options=["CNY", "HKD", "USD", "EUR"],
                    required=True,
                ),
                "Exchange_Rate": st.column_config.NumberColumn(
                    "Exchange_Rate", format="%.4f", min_value=0.0001, default=1.0,
                ),
                "Shares": st.column_config.NumberColumn("Shares", format="%.2f", min_value=0.0),
                "Current_Price": st.column_config.NumberColumn("Current_Price", format="%.4f", min_value=0.0),
                "Total_Value": st.column_config.NumberColumn("Total_Value", format="%.2f", min_value=0.0),
                "Net_Cash_Flow": st.column_config.NumberColumn("Net_Cash_Flow", format="%.2f"),
            },
            column_order=[
                "Asset_Class", "Platform", "Name", "Code", "Currency", "Exchange_Rate",
                "Shares", "Current_Price", "Total_Value", "Net_Cash_Flow",
            ],
            key=edit_key,
        )

        save_col, delete_col = st.columns(2)

        with save_col:
            if st.button("保存修改", type="primary", key="save_history"):
                update_snapshot(csv_path, selected_date, edited_history)
                st.success(f"已更新 {selected_date} 的快照")
                st.cache_data.clear()
                if 'update_template' in st.session_state:
                    del st.session_state['update_template']
                st.rerun()

        with delete_col:
            if len(all_dates) <= 1:
                st.button("删除此快照", disabled=True, help="不能删除唯一的快照", key="delete_history")
            else:
                confirm_delete = st.checkbox("确认删除", key="confirm_delete_history")
                if st.button("删除此快照", type="secondary", disabled=not confirm_delete, key="delete_history"):
                    delete_snapshot(csv_path, selected_date)
                    st.success(f"已删除 {selected_date} 的快照")
                    st.cache_data.clear()
                    if 'update_template' in st.session_state:
                        del st.session_state['update_template']
                    st.rerun()

# Tab 3: SAP
# ═══════════════════════════════════════════════════════════

with tab_sap:

    st.header("SAP 公司股票")

    own_df = load_own_sap(OWN_SAP_CSV) if os.path.exists(OWN_SAP_CSV) else None
    move_df = load_move_sap(MOVE_SAP_CSV) if os.path.exists(MOVE_SAP_CSV) else None

    if own_df is None and move_df is None:
        st.info("未找到 SAP 股票数据。请先运行 `python src/import_sap_xlsx.py CurrentAsset.xlsx data/` 导入数据。")
    else:

        # ─── Section 1: Summary KPIs ───

        st.subheader("持仓概览")

        # Handle refresh: try Yahoo Finance for price, frankfurter for FX
        if st.session_state.get('_sap_do_refresh'):
            st.session_state['_sap_do_refresh'] = False
            refresh_errors = []
            try:
                price = get_stock_price("SAP.DE")
                if price:
                    st.session_state['sap_current_price'] = round(price, 2)
            except Exception:
                refresh_errors.append("stock price (VPN required)")
            try:
                rate = get_exchange_rate('EUR', 'CNY')
                if rate:
                    st.session_state['sap_fx_rate'] = round(rate, 4)
            except Exception:
                refresh_errors.append("FX rate")
            save_sap_price_cache(
                st.session_state.get('sap_current_price', 170.0),
                st.session_state.get('sap_fx_rate', 8.0),
            )
            if refresh_errors:
                st.session_state['_sap_refresh_errors'] = refresh_errors

        # Show refresh errors from previous cycle
        errs = st.session_state.pop('_sap_refresh_errors', None)
        if errs:
            st.warning(f"Failed to fetch: {', '.join(errs)}. Using cached/manual values.")

        # User-input price & FX
        price_col, fx_col, refresh_col = st.columns([2, 2, 1])
        with price_col:
            sap_price_eur = st.number_input(
                "SAP Price (EUR)",
                value=float(st.session_state.get('sap_current_price', 170.0)),
                min_value=0.01,
                format="%.2f",
                key="sap_current_price")
        with fx_col:
            sap_fx_rate = st.number_input(
                "EUR/CNY Rate",
                value=float(st.session_state.get('sap_fx_rate', 8.0)),
                min_value=0.01,
                format="%.4f",
                key="sap_fx_rate")
        with refresh_col:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Refresh", key="sap_refresh"):
                st.session_state['_sap_do_refresh'] = True
                st.rerun()

        # Persist any change (manual or refreshed) to iCloud-synced cache
        save_sap_price_cache(sap_price_eur, sap_fx_rate)

        cache = load_sap_price_cache()
        if cache and cache.get('updated'):
            st.caption(f"Last updated: {cache['updated']}")

        kpi_cols = st.columns(3)

        if own_df is not None:
            own_sum = own_sap_summary(own_df, fx_rate=sap_fx_rate)
            own_value = own_sum['total_shares'] * sap_price_eur * sap_fx_rate
            own_pl = own_value - own_sum['total_cost']
            own_pl_pct = own_pl / own_sum['total_cost'] * 100 if own_sum['total_cost'] > 0 else None
            with kpi_cols[0]:
                st.markdown("**Own SAP (ESPP)**")
                st.metric("持股", f"{own_sum['total_shares']:.2f} 股")
                st.metric("成本", f"¥{own_sum['total_cost']:,.2f}")
                st.metric("市值", f"¥{own_value:,.2f}")
                st.metric("盈亏", f"¥{own_pl:+,.2f}")
                if own_pl_pct is not None:
                    st.metric("盈亏%", f"{own_pl_pct:+.2f}%")
                if own_sum['break_even_eur']:
                    st.caption(f"盈亏平衡价: {own_sum['break_even_eur']:.2f} EUR")

        if move_df is not None:
            move_sum = move_sap_summary(move_df, fx_rate=sap_fx_rate)
            move_value = move_sum['total_shares'] * sap_price_eur * sap_fx_rate
            move_pl = move_value - move_sum['total_cost']
            move_pl_pct = move_pl / move_sum['total_cost'] * 100 if move_sum['total_cost'] > 0 else None
            with kpi_cols[1]:
                st.markdown("**Move SAP (RSU)**")
                st.metric("持股", f"{move_sum['total_shares']:.2f} 股")
                st.metric("成本", f"¥{move_sum['total_cost']:,.2f}")
                st.metric("市值", f"¥{move_value:,.2f}")
                st.metric("盈亏", f"¥{move_pl:+,.2f}")
                if move_pl_pct is not None:
                    st.metric("盈亏%", f"{move_pl_pct:+.2f}%")
                if move_sum['break_even_eur']:
                    st.caption(f"盈亏平衡价: {move_sum['break_even_eur']:.2f} EUR")

        with kpi_cols[2]:
            combined_shares = (own_sum['total_shares'] if own_df is not None else 0) + \
                              (move_sum['total_shares'] if move_df is not None else 0)
            combined_cost = (own_sum['total_cost'] if own_df is not None else 0) + \
                            (move_sum['total_cost'] if move_df is not None else 0)
            combined_value = combined_shares * sap_price_eur * sap_fx_rate
            combined_pl = combined_value - combined_cost
            combined_pl_pct = combined_pl / combined_cost * 100 if combined_cost > 0 else None
            st.markdown("**Combined**")
            st.metric("总持股", f"{combined_shares:.2f} 股")
            st.metric("总成本", f"¥{combined_cost:,.2f}")
            st.metric("总市值", f"¥{combined_value:,.2f}")
            st.metric("总盈亏", f"¥{combined_pl:+,.2f}")
            if combined_pl_pct is not None:
                st.metric("总盈亏%", f"{combined_pl_pct:+.2f}%")
            st.caption(f"当前股价: {sap_price_eur:.2f} EUR")

        st.divider()

        # ─── Section 2: Transaction History ───

        st.subheader("交易记录")

        history_program = st.selectbox("选择计划", ["Own SAP", "Move SAP"], key="sap_history_select")

        if history_program == "Own SAP" and own_df is not None:
            st.dataframe(
                own_df.sort_values('Date', ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Price_EUR": st.column_config.NumberColumn(format="%.4f"),
                    "Quantity": st.column_config.NumberColumn(format="%.4f"),
                    "Discount_Ratio": st.column_config.NumberColumn(format="%.2f"),
                    "CNY": st.column_config.NumberColumn(format="¥%.2f"),
                    "Cost_CNY": st.column_config.NumberColumn(format="¥%.2f"),
                },
            )
            st.caption(f"共 {len(own_df)} 条记录")
        elif history_program == "Move SAP" and move_df is not None:
            st.dataframe(
                move_df.sort_values('Date', ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Price_EUR": st.column_config.NumberColumn(format="%.4f"),
                    "Quantity": st.column_config.NumberColumn(format="%.4f"),
                    "FX_Rate": st.column_config.NumberColumn(format="%.4f"),
                    "CNY": st.column_config.NumberColumn(format="¥%.2f"),
                },
            )
            st.caption(f"共 {len(move_df)} 条记录")
        else:
            st.info("无数据")

        st.divider()

        # ─── Section 3: Add New Transaction ───

        st.subheader("新增交易")

        add_program = st.selectbox("选择计划", ["Own SAP", "Move SAP"], key="sap_add_select")

        # ── Own SAP Form ──
        if add_program == "Own SAP":

            own_col1, own_col2, own_col3 = st.columns(3)
            with own_col1:
                own_date = st.date_input("交易日期", value=date.today(), key="own_sap_date")
            with own_col2:
                own_price = st.number_input("Price (EUR)", value=170.0, min_value=0.01, format="%.5f", key="own_sap_price")
            with own_col3:
                own_tax_rate = st.number_input("Tax Rate", value=0.25, min_value=0.0, max_value=1.0,
                                               step=0.05, format="%.2f", key="own_sap_tax")

            # Dynamic rows
            if 'own_sap_rows' not in st.session_state:
                st.session_state['own_sap_rows'] = [
                    {'type': 'Match', 'cny': 0.0, 'qty': 0.0},
                    {'type': 'Purchase', 'cny': 0.0, 'qty': 0.0},
                ]

            rows_to_display = []
            for i, row_data in enumerate(st.session_state['own_sap_rows']):
                c1, c2, c3, c4, c5 = st.columns([1.5, 1.5, 1.5, 1, 1.5])
                with c1:
                    rtype = st.selectbox("Type", ["Match", "Purchase", "Dividend", "Sell"],
                                         index=["Match", "Purchase", "Dividend", "Sell"].index(row_data['type']),
                                         key=f"own_type_{i}")
                with c2:
                    cny = st.number_input("CNY", value=row_data['cny'], format="%.2f", key=f"own_cny_{i}")
                with c3:
                    qty = st.number_input("Quantity", value=row_data['qty'], format="%.6f", key=f"own_qty_{i}")

                # Derive discount and cost
                if rtype == 'Match':
                    discount = own_tax_rate
                elif rtype == 'Purchase':
                    discount = 1.0 - own_tax_rate
                else:
                    discount = 1.0
                cost = round(cny * discount, 2)

                with c4:
                    st.text_input("Discount", value=f"{discount:.2f}", disabled=True, key=f"own_disc_{i}")
                with c5:
                    st.text_input("Cost CNY", value=f"{cost:.2f}", disabled=True, key=f"own_cost_{i}")

                rows_to_display.append({
                    'type': rtype, 'cny': cny, 'qty': qty,
                    'discount': discount, 'cost': cost,
                })

            # Update session state with current values
            st.session_state['own_sap_rows'] = [
                {'type': r['type'], 'cny': r['cny'], 'qty': r['qty']} for r in rows_to_display
            ]

            btn_own_col1, btn_own_col2 = st.columns(2)
            with btn_own_col1:
                if st.button("+ Add Row", key="own_add_row"):
                    st.session_state['own_sap_rows'].append({'type': 'Purchase', 'cny': 0.0, 'qty': 0.0})
                    st.rerun()
            with btn_own_col2:
                if len(st.session_state['own_sap_rows']) > 1:
                    if st.button("- Remove Last", key="own_remove_row"):
                        st.session_state['own_sap_rows'].pop()
                        st.rerun()

            # Summary
            total_qty = sum(r['qty'] for r in rows_to_display)
            total_cost = sum(r['cost'] for r in rows_to_display)
            st.markdown(f"**Total: {total_qty:.4f} shares, Cost: ¥{total_cost:,.2f}**")

            # Save
            if st.button("Save to own_sap.csv", type="primary", key="own_sap_save"):
                if total_qty == 0 and total_cost == 0:
                    st.error("No data to save")
                else:
                    new_rows = []
                    date_str = own_date.strftime('%Y-%m-%d')
                    for r in rows_to_display:
                        if r['qty'] != 0 or r['cny'] != 0:
                            new_rows.append({
                                'Date': date_str,
                                'Activity': r['type'],
                                'Price_EUR': round(own_price, 6),
                                'Quantity': round(r['qty'], 6),
                                'Discount_Ratio': round(r['discount'], 4),
                                'CNY': round(r['cny'], 2),
                                'Cost_CNY': round(r['cost'], 2),
                            })
                    new_df = pd.DataFrame(new_rows)
                    if os.path.exists(OWN_SAP_CSV):
                        existing = pd.read_csv(OWN_SAP_CSV)
                        combined = pd.concat([existing, new_df], ignore_index=True)
                    else:
                        combined = new_df
                    _atomic_write_csv(combined, OWN_SAP_CSV)
                    st.success(f"Saved {len(new_rows)} rows to own_sap.csv")
                    st.cache_data.clear()
                    st.session_state.pop('own_sap_rows', None)
                    st.rerun()

        # ── Move SAP Form ──
        else:

            # Handle FX refresh: must set session_state BEFORE widget renders
            if st.session_state.get('_move_fx_do_refresh'):
                st.session_state['_move_fx_do_refresh'] = False
                try:
                    rate = get_exchange_rate('EUR', 'CNY')
                    st.session_state['move_sap_fx'] = round(rate, 4)
                except Exception as e:
                    st.error(f"FX fetch failed: {e}")

            move_col1, move_col2, move_col3 = st.columns(3)
            with move_col1:
                move_date = st.date_input("交易日期", value=date.today(), key="move_sap_date")
            with move_col2:
                move_price = st.number_input("Price (EUR)", value=170.0, min_value=0.01, format="%.2f", key="move_sap_price")
            with move_col3:
                move_fx_col1, move_fx_col2 = st.columns([3, 1])
                with move_fx_col1:
                    move_fx = st.number_input("EUR/CNY Rate", value=8.0, min_value=0.01,
                                              format="%.4f", key="move_sap_fx")
                with move_fx_col2:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("Refresh", key="move_fx_refresh"):
                        st.session_state['_move_fx_do_refresh'] = True
                        st.rerun()

            # Dynamic rows
            if 'move_sap_rows' not in st.session_state:
                st.session_state['move_sap_rows'] = [{'qty': 0.0}]

            move_rows = []
            for i, row_data in enumerate(st.session_state['move_sap_rows']):
                mc1, mc2 = st.columns([2, 2])
                with mc1:
                    mqty = st.number_input(f"Tranche {i+1} Qty", value=row_data['qty'],
                                           format="%.4f", key=f"move_qty_{i}")
                cny_val = round(move_price * mqty * move_fx, 2)
                with mc2:
                    st.text_input(f"CNY", value=f"¥{cny_val:,.2f}", disabled=True, key=f"move_cny_{i}")
                move_rows.append({'qty': mqty, 'cny': cny_val})

            st.session_state['move_sap_rows'] = [{'qty': r['qty']} for r in move_rows]

            btn_move_col1, btn_move_col2 = st.columns(2)
            with btn_move_col1:
                if st.button("+ Add Row", key="move_add_row"):
                    st.session_state['move_sap_rows'].append({'qty': 0.0})
                    st.rerun()
            with btn_move_col2:
                if len(st.session_state['move_sap_rows']) > 1:
                    if st.button("- Remove Last", key="move_remove_row"):
                        st.session_state['move_sap_rows'].pop()
                        st.rerun()

            # Summary
            move_total_qty = sum(r['qty'] for r in move_rows)
            move_total_cny = sum(r['cny'] for r in move_rows)
            st.markdown(f"**Total: {move_total_qty:.4f} shares, CNY: ¥{move_total_cny:,.2f}**")

            # Save
            if st.button("Save to move_sap.csv", type="primary", key="move_sap_save"):
                if move_total_qty == 0:
                    st.error("No data to save")
                else:
                    new_rows = []
                    date_str = move_date.strftime('%Y-%m-%d')
                    for r in move_rows:
                        if r['qty'] != 0:
                            new_rows.append({
                                'Date': date_str,
                                'Activity': 'Award',
                                'Price_EUR': round(move_price, 6),
                                'Quantity': round(r['qty'], 6),
                                'FX_Rate': round(move_fx, 4),
                                'CNY': round(r['cny'], 2),
                            })
                    new_df = pd.DataFrame(new_rows)
                    if os.path.exists(MOVE_SAP_CSV):
                        existing = pd.read_csv(MOVE_SAP_CSV)
                        combined = pd.concat([existing, new_df], ignore_index=True)
                    else:
                        combined = new_df
                    _atomic_write_csv(combined, MOVE_SAP_CSV)
                    st.success(f"Saved {len(new_rows)} rows to move_sap.csv")
                    st.cache_data.clear()
                    st.session_state.pop('move_sap_rows', None)
                    st.rerun()

# ═══════════════════════════════════════════════════════════
# Tab 4: Market
# ═══════════════════════════════════════════════════════════

with tab_market:

    st.header("市场温度计")

    # ─── 顶部栏：最后更新 + 刷新按钮 ───

    top_col1, top_col2 = st.columns([5, 1])
    with top_col2:
        if st.button("🔄 刷新数据", key="market_refresh"):
            st.session_state['_market_force_refresh'] = True
            st.rerun()

    force = st.session_state.pop('_market_force_refresh', False)

    with st.spinner("加载市场数据..."):
        market_data = get_market_data(force_refresh=force)

    meta = market_data.get('meta', {})

    # 显示最后更新时间（取各标的最新时间）
    update_times = [v for k, v in meta.items() if k.endswith('_updated') and v != '未知']
    last_update  = max(update_times) if update_times else '未知'
    with top_col1:
        st.caption(f"数据更新时间: {last_update}　｜　数据来自公开市场，存在延迟")

    st.divider()

    # ─── Section 1: 乖离率监测 ───

    st.subheader("乖离率监测")
    st.caption("乖离率 = (当前价 − MAn) / MAn × 100%　｜　主要信号列显示主要参考均线信号")

    # 颜色图例
    st.markdown(
        "🔵 深度超卖 (≤−10%)　　"
        "🟢 超卖 (−10~−5%)　　"
        "⚪ 正常 (−5~+8%)　　"
        "🟡 偏高 (+8~+15%)　　"
        "🔴 超买 (>+15%)"
    )

    bias_rows = []
    for key, cfg in TARGETS.items():
        entry = market_data.get(key)
        updated = meta.get(f'{key}_updated', '未知')
        is_stale = (updated != last_update and updated != '未知')

        if entry is None:
            bias_rows.append({
                '标的':        cfg['name'] + (' ⚠️陈旧' if is_stale else ''),
                '当前价':      '—',
                'MA60':        '—',
                'MA60乖离':    '—',
                'MA200':       '—',
                'MA200乖离':   '—',
                '主要信号':    '无数据',
            })
            continue

        bias = compute_bias(entry)
        primary_ma = cfg['primary_ma']

        def _fmt_bias(b, signal, emoji, is_primary):
            if b is None:
                return '—'
            sign = '+' if b >= 0 else ''
            return f"{emoji} {sign}{b:.2f}%"

        bias60_str  = _fmt_bias(bias['bias60'],  bias['signal60'],  bias['emoji60'],  primary_ma == 60)
        bias200_str = _fmt_bias(bias['bias200'], bias['signal200'], bias['emoji200'], primary_ma == 200)

        main_signal = bias[f"signal{primary_ma}"]
        main_emoji  = bias[f"emoji{primary_ma}"]

        price = entry.get('price', 0)
        ma60  = entry.get('ma60')
        ma200 = entry.get('ma200')

        bias_rows.append({
            '标的':      cfg['name'] + (' ⚠️' if is_stale else ''),
            '当前价':    f"{price:,.2f}",
            'MA60':      f"{ma60:,.2f}"  if ma60  else '—',
            'MA60乖离':  bias60_str,
            'MA200':     f"{ma200:,.2f}" if ma200 else '—',
            'MA200乖离': bias200_str,
            '主要信号':  f"{main_emoji} {main_signal}",
        })

    if bias_rows:
        bias_df = pd.DataFrame(bias_rows)
        st.dataframe(
            bias_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                '标的':      st.column_config.TextColumn(width='medium'),
                'MA60乖离':  st.column_config.TextColumn('MA60乖离 ★A股', width='medium'),
                'MA200乖离': st.column_config.TextColumn('MA200乖离 ★美股/金', width='medium'),
                '主要信号':  st.column_config.TextColumn(width='small'),
            },
        )

    st.divider()

    # ─── Section 2: 恐慌与估值 ───

    st.subheader("恐慌与估值")

    vix_entry       = market_data.get('vix')
    vxn_entry       = market_data.get('vxn')
    qvix_entry      = market_data.get('qvix')
    pe_sp_entry     = market_data.get('pe_sp500')
    pe_ndx_entry    = market_data.get('pe_ndx100')
    treasury_entry    = market_data.get('treasury_10y')
    cn_treasury_entry = market_data.get('cn_treasury_10y')

    vix_val       = vix_entry.get('price')      if vix_entry      else None
    vxn_val       = vxn_entry.get('price')      if vxn_entry      else None
    vix_manual    = bool((vix_entry or {}).get('manual_override'))
    vxn_manual    = bool((vxn_entry or {}).get('manual_override'))
    qvix_val      = qvix_entry.get('price')     if qvix_entry     else None
    pe_sp         = (pe_sp_entry.get('manual_override') or pe_sp_entry.get('value'))   if pe_sp_entry  else None
    pe_ndx        = (pe_ndx_entry.get('manual_override') or pe_ndx_entry.get('value')) if pe_ndx_entry else None
    treasury_val    = treasury_entry.get('price')    if treasury_entry    else None
    cn_treasury_val = cn_treasury_entry.get('price') if cn_treasury_entry else None
    sp_src        = ('手动' if (pe_sp_entry or {}).get('manual_override') else 'VOO auto') if pe_sp_entry else '—'
    ndx_src       = ('手动' if (pe_ndx_entry or {}).get('manual_override') else 'QQQ auto') if pe_ndx_entry else '—'

    vix_label,   vix_emoji   = compute_vix_signal(vix_val)
    vxn_label,   vxn_emoji   = compute_vxn_signal(vxn_val)
    qvix_label,  qvix_emoji  = compute_qvix_signal(qvix_val)
    sp_pe_label,  sp_pe_emoji  = compute_pe_signal(pe_sp,  'sp500')
    ndx_pe_label, ndx_pe_emoji = compute_pe_signal(pe_ndx, 'ndx100')

    # 中国10Y国债信号
    if cn_treasury_val is None:
        cn_treasury_label, cn_treasury_emoji = '数据不可用', '❓'
    elif cn_treasury_val >= 3.0:
        cn_treasury_label, cn_treasury_emoji = '偏高', '🔴'
    elif cn_treasury_val >= 2.5:
        cn_treasury_label, cn_treasury_emoji = '中性', '🟡'
    else:
        cn_treasury_label, cn_treasury_emoji = '偏低（宽松）', '🟢'

    # 红利利差（沪深300 PE倒数×40% 近似股息率 - 中国10Y国债）
    _pe_csi300_for_div = (market_data.get('pe_csi300') or {}).get('value')
    _div_yield_proxy = (1 / _pe_csi300_for_div * 100 * 0.4) if (_pe_csi300_for_div and _pe_csi300_for_div > 0) else None
    _div_spread = round(_div_yield_proxy - cn_treasury_val, 2) if (_div_yield_proxy and cn_treasury_val) else None

    # 美债收益率信号（仅展示，不参与矩阵）
    if treasury_val is None:
        treasury_label, treasury_emoji = '数据不可用', '❓'
    elif treasury_val >= 4.5:
        treasury_label, treasury_emoji = '偏高（压估值）', '🔴'
    elif treasury_val >= 3.5:
        treasury_label, treasury_emoji = '中性', '🟡'
    else:
        treasury_label, treasury_emoji = '偏低（宽松）', '🟢'

    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6, kpi7, kpi8 = st.columns(8)
    with kpi1:
        st.metric("VIX 标普波动率", f"{vix_val:.1f}" if vix_val else "—")
        st.markdown(f"{vix_emoji} **{vix_label}**")
        _vix_src = '手动' if vix_manual else f"更新: {meta.get('vix_updated', '未知')}"
        st.caption(f"标普500期权隐含波动率　{_vix_src}")
    with kpi2:
        st.metric("VXN 纳指波动率", f"{vxn_val:.1f}" if vxn_val else "—")
        st.markdown(f"{vxn_emoji} **{vxn_label}**")
        _vxn_src = '手动' if vxn_manual else f"更新: {meta.get('vxn_updated', '未知')}"
        st.caption(f"纳指100期权隐含波动率　{_vxn_src}")
    with kpi3:
        st.metric("QVIX A股波动率", f"{qvix_val:.1f}" if qvix_val else "—")
        st.markdown(f"{qvix_emoji} **{qvix_label}**")
        from market_monitor import get_qvix_percentile
        _qvix_pct = get_qvix_percentile(os.path.dirname(csv_path), qvix_val)
        if _qvix_pct:
            _qp = _qvix_pct['percentile']
            _qp_color = '#d32f2f' if _qp >= 80 else ('#2e7d32' if _qp <= 20 else '#888')
            st.caption(
                f"300ETF期权隐含波动率　更新: {meta.get('qvix_updated', '未知')}"
                f"\n近{_qvix_pct['days']}天分位: "
                f"<span style='color:{_qp_color};font-weight:bold'>{_qp:.1f}%</span>"
                f"　区间 {_qvix_pct['min']:.1f}–{_qvix_pct['max']:.1f}",
                unsafe_allow_html=True,
            )
        else:
            st.caption(f"300ETF期权隐含波动率　更新: {meta.get('qvix_updated', '未知')}")
    with kpi4:
        st.metric("标普500 PE", f"{pe_sp:.1f}" if pe_sp else "—")
        st.markdown(f"{sp_pe_emoji} **{sp_pe_label}**")
        st.caption(f"来源: {sp_src}　更新: {meta.get('pe_sp500_updated', '未知')}")
    with kpi5:
        st.metric("纳指100 PE", f"{pe_ndx:.1f}" if pe_ndx else "—")
        st.markdown(f"{ndx_pe_emoji} **{ndx_pe_label}**")
        st.caption(f"来源: {ndx_src}　更新: {meta.get('pe_ndx100_updated', '未知')}")
    with kpi6:
        st.metric("美债10Y收益率", f"{treasury_val:.2f}%" if treasury_val else "—")
        st.markdown(f"{treasury_emoji} **{treasury_label}**")
        st.caption(f"仅供参考　更新: {meta.get('treasury_10y_updated', '未知')}")
    with kpi7:
        st.metric("中国10Y国债", f"{cn_treasury_val:.2f}%" if cn_treasury_val else "—")
        st.markdown(f"{cn_treasury_emoji} **{cn_treasury_label}**")
        st.caption(f"更新: {meta.get('cn_treasury_10y_updated', '未知')}")
    with kpi8:
        if _div_spread is not None:
            _ds_color = '#2e7d32' if _div_spread >= 2 else ('#888' if _div_spread >= 1 else '#d32f2f')
            st.metric("红利-国债利差", f"{_div_spread:+.2f}pp")
            st.markdown(f"<span style='color:{_ds_color};font-weight:bold'>"
                       f"{'宽松✓' if _div_spread>=2 else ('中性' if _div_spread>=1 else '收窄✗')}</span>",
                       unsafe_allow_html=True)
            st.caption(f"沪深300近似股息率 - 中国10Y　参考第十八节结论")
        else:
            st.metric("红利-国债利差", "—")
            st.caption("数据不完整")

    # 手动覆盖（折叠）
    with st.expander("手动覆盖（网络不可达时使用）", expanded=False):
        st.caption("填入后点击应用；留空或填0表示清除手动值，恢复自动获取。")
        ov_col1, ov_col2, ov_col3, ov_col4, ov_col5 = st.columns([2, 2, 2, 2, 1])
        with ov_col1:
            sp_ov = st.number_input(
                "标普PE (VOO)", value=float(pe_sp) if pe_sp else 0.0,
                min_value=0.0, step=0.1, format="%.1f", key="pe_sp_override",
            )
        with ov_col2:
            ndx_ov = st.number_input(
                "纳指PE (QQQ)", value=float(pe_ndx) if pe_ndx else 0.0,
                min_value=0.0, step=0.1, format="%.1f", key="pe_ndx_override",
            )
        with ov_col3:
            vix_ov = st.number_input(
                "VIX", value=float(vix_val) if vix_val else 0.0,
                min_value=0.0, step=0.1, format="%.1f", key="vix_override",
            )
        with ov_col4:
            vxn_ov = st.number_input(
                "VXN", value=float(vxn_val) if vxn_val else 0.0,
                min_value=0.0, step=0.1, format="%.1f", key="vxn_override",
            )
        with ov_col5:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("应用", key="override_apply"):
                set_pe_override('sp500',  sp_ov  if sp_ov  > 0 else None)
                set_pe_override('ndx100', ndx_ov if ndx_ov > 0 else None)
                set_vol_override('vix',   vix_ov if vix_ov > 0 else None)
                set_vol_override('vxn',   vxn_ov if vxn_ov > 0 else None)
                st.success("已保存手动值")
                st.rerun()
            if st.button("清除全部", key="override_clear"):
                set_pe_override('sp500',  None)
                set_pe_override('ndx100', None)
                set_vol_override('vix',   None)
                set_vol_override('vxn',   None)
                st.success("已清除，将恢复自动获取")
                st.rerun()

    st.divider()

    # ─── Section 3: 定投倍数建议（标普 + 纳指） ───

    st.subheader("定投倍数建议")
    st.caption("标普500使用 PE×VIX 矩阵；纳指100使用 PE×VXN 矩阵（VXN为纳指专属波动率，更精准）。A股请参考下方A股矩阵，黄金请参考黄金矩阵。")

    mult_sp  = lookup_multiplier(pe_sp,  vix_val, 'sp500')
    mult_ndx = lookup_multiplier(pe_ndx, vxn_val, 'ndx100')

    def _mult_color(m):
        if m in ('暂停',):
            return '#d32f2f'
        if m == '顶格':
            return '#1565c0'
        return '#2e7d32'

    def _render_matrix(matrix, row_labels, col_labels, current_row, current_col):
        """将矩阵渲染为带高亮的 DataFrame，current_row/col 为当前所在格子索引。"""
        import pandas as pd

        df = pd.DataFrame(matrix, index=row_labels, columns=col_labels)

        def style_cell(val):
            return ''

        def highlight(df):
            styles = pd.DataFrame('', index=df.index, columns=df.columns)
            for i in range(len(df)):
                for j in range(len(df.columns)):
                    cell_val = df.iloc[i, j]
                    if i == current_row and j == current_col:
                        styles.iloc[i, j] = 'background-color: #fff9c4; font-weight: bold; border: 2px solid #f9a825;'
                    elif cell_val == '暂停':
                        styles.iloc[i, j] = 'color: #d32f2f; opacity: 0.5;'
                    elif cell_val == '顶格':
                        styles.iloc[i, j] = 'color: #1565c0;'
                    else:
                        styles.iloc[i, j] = 'color: #2e7d32;'
            return styles

        return df.style.apply(highlight, axis=None)

    mult_col1, mult_col2 = st.columns(2)

    with mult_col1:
        color = _mult_color(mult_sp)
        if pe_sp and vix_val:
            st.markdown(
                f"<div style='border:1px solid #ddd; border-radius:8px; padding:16px; text-align:center;'>"
                f"<div style='font-size:14px; color:#666; margin-bottom:8px;'>标普500</div>"
                f"<div style='font-size:13px; color:#999;'>PE {pe_sp:.1f} × VIX {vix_val:.1f}</div>"
                f"<div style='font-size:40px; font-weight:bold; color:{color}; margin:12px 0;'>{mult_sp}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.caption("ℹ️ 回测验证：标普500矩阵策略在20年（2005起）视角下有效，XIRR超额+0.2%，绝对多赚约150万。核心价值在于危机时顶格加仓（2008/2020），而非日常减仓择时。")
        else:
            st.info("标普500: 数据不完整，无法计算")

    with mult_col2:
        color = _mult_color(mult_ndx)
        if pe_ndx and vxn_val:
            st.markdown(
                f"<div style='border:1px solid #ddd; border-radius:8px; padding:16px; text-align:center;'>"
                f"<div style='font-size:14px; color:#666; margin-bottom:8px;'>纳指100</div>"
                f"<div style='font-size:13px; color:#999;'>PE {pe_ndx:.1f} × VXN {vxn_val:.1f}</div>"
                f"<div style='font-size:40px; font-weight:bold; color:{color}; margin:12px 0;'>{mult_ndx}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("纳指100: 数据不完整，无法计算")

    st.markdown(
        "**倍数说明**: 暂停 = 不建议定投（原『观望』已合并）；顶格 = 全力加仓。"
        "倍数以您自身基准定投金额为基础执行。"
    )

    # 美股矩阵表格（当前位置高亮）
    sp_col_labels  = ['VIX<18', 'VIX 18-25', 'VIX 25-35', 'VIX>35']
    ndx_col_labels = ['VXN<20', 'VXN 20-27', 'VXN 27-35', 'VXN>35']
    sp_row_labels  = ['>32', '29-32', '26-29', '23-26', '20-23', '17-20', '14-17', '<14']
    ndx_row_labels = ['>37', '35-37', '32-35', '28-32', '24-28', '20-24', '16-20', '<16']

    def _find_row(val, bands):
        """找当前值所在行索引。"""
        if val is None:
            return None
        for i, b in enumerate(bands):
            if val > b:
                return i
        return len(bands)

    def _find_col(val, bands):
        """找当前值所在列索引。"""
        if val is None:
            return None
        for j, b in enumerate(bands):
            if val < b:
                return j
        return len(bands)

    mat_col1, mat_col2 = st.columns(2)
    with mat_col1:
        st.caption("标普500 完整矩阵（🟨 当前位置）")
        sp_row = _find_row(pe_sp, SP500_PE_BANDS) if pe_sp else None
        sp_col = _find_col(vix_val, SP500_VIX_BANDS) if vix_val else None
        if sp_row is not None and sp_col is not None:
            st.dataframe(_render_matrix(SP500_MATRIX, sp_row_labels, sp_col_labels, sp_row, sp_col),
                         use_container_width=True)
        else:
            st.dataframe(pd.DataFrame(SP500_MATRIX, index=sp_row_labels, columns=sp_col_labels),
                         use_container_width=True)

    with mat_col2:
        st.caption("纳指100 完整矩阵（🟨 当前位置）")
        ndx_row = _find_row(pe_ndx, NDX100_PE_BANDS) if pe_ndx else None
        ndx_col = _find_col(vxn_val, NDX100_VIX_BANDS) if vxn_val else None
        if ndx_row is not None and ndx_col is not None:
            st.dataframe(_render_matrix(NDX100_MATRIX, ndx_row_labels, ndx_col_labels, ndx_row, ndx_col),
                         use_container_width=True)
        else:
            st.dataframe(pd.DataFrame(NDX100_MATRIX, index=ndx_row_labels, columns=ndx_col_labels),
                         use_container_width=True)

    st.caption("⚠️ 仅供参考，不构成投资建议。数据来自公开市场，存在延迟。")

    st.divider()

    # ─── Section 4: A股定投倍数建议（CSI300 + 中证A500） ───

    st.subheader("A股定投倍数建议")
    st.caption(
        "基于 PE百分位 × QVIX百分位 矩阵。A股PE历史区间波动极大（8x–51x），"
        "百分位法比绝对值更稳健。QVIX为300ETF期权隐含波动率（A股波动率基准）。"
    )

    pe_csi300_entry  = market_data.get('pe_csi300')
    pe_csi_a500_entry = market_data.get('pe_csi_a500')

    pe_csi300   = pe_csi300_entry.get('value')   if pe_csi300_entry  else None
    pe_csi_a500 = pe_csi_a500_entry.get('value') if pe_csi_a500_entry else None

    mult_csi300  = lookup_a_share_multiplier(pe_csi300,   qvix_val, 'csi300')
    mult_csi_a500 = lookup_a_share_multiplier(pe_csi_a500, qvix_val, 'csi_a500')

    a_col1, a_col2 = st.columns(2)

    with a_col1:
        color = _mult_color(mult_csi300)
        if pe_csi300 and qvix_val:
            st.markdown(
                f"<div style='border:1px solid #ddd; border-radius:8px; padding:16px; text-align:center;'>"
                f"<div style='font-size:14px; color:#666; margin-bottom:8px;'>CSI 300 沪深300</div>"
                f"<div style='font-size:13px; color:#999;'>PE {pe_csi300:.1f} × QVIX {qvix_val:.1f}</div>"
                f"<div style='font-size:40px; font-weight:bold; color:{color}; margin:12px 0;'>{mult_csi300}</div>"
                f"<div style='font-size:11px; color:#aaa;'>PE来源: akshare 沪深300 滚动PE</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("CSI300: 数据不完整，无法计算")

    with a_col2:
        color = _mult_color(mult_csi_a500)
        if pe_csi_a500 and qvix_val:
            pe_src_note = (pe_csi_a500_entry or {}).get('source', '')
            st.markdown(
                f"<div style='border:1px solid #ddd; border-radius:8px; padding:16px; text-align:center;'>"
                f"<div style='font-size:14px; color:#666; margin-bottom:8px;'>中证A500</div>"
                f"<div style='font-size:13px; color:#999;'>PE {pe_csi_a500:.1f} × QVIX {qvix_val:.1f}</div>"
                f"<div style='font-size:40px; font-weight:bold; color:{color}; margin:12px 0;'>{mult_csi_a500}</div>"
                f"<div style='font-size:11px; color:#e65100;'>⚠️ PE代理: 中证500（A500于2023-11-17发布，历史数据不足，待积累3年后切换）</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("中证A500: 数据不完整，无法计算")

    st.markdown(
        "**倍数说明**: 暂停 = 不建议定投（原『观望』已合并）；顶格 = 全力加仓。"
        "倍数以您自身基准定投金额为基础执行。"
    )

    # A股矩阵表格
    a_row_labels  = ['PE>80th 高估', 'PE 60-80th 偏贵', 'PE 30-60th 合理', 'PE<30th 低估']
    a_col_labels  = ['QVIX<30th', 'QVIX 30-60th', 'QVIX 60-80th', 'QVIX>80th']

    def _find_a_row(pe, bands):
        if pe is None:
            return None
        for i, b in enumerate(bands):
            if pe > b:
                return i
        return len(bands)

    amat_col1, amat_col2 = st.columns(2)
    with amat_col1:
        st.caption("CSI300 完整矩阵（🟨 当前位置）")
        csi300_row = _find_a_row(pe_csi300, CSI300_PE_BANDS) if pe_csi300 else None
        csi300_col = _find_col(qvix_val, CSI300_QVIX_BANDS) if qvix_val else None
        if csi300_row is not None and csi300_col is not None:
            st.dataframe(_render_matrix(CSI300_MATRIX, a_row_labels, a_col_labels, csi300_row, csi300_col),
                         use_container_width=True)
        else:
            st.dataframe(pd.DataFrame(CSI300_MATRIX, index=a_row_labels, columns=a_col_labels),
                         use_container_width=True)

    with amat_col2:
        st.caption("中证A500 完整矩阵（🟨 当前位置）")
        a500_row = _find_a_row(pe_csi_a500, CSI_A500_PE_BANDS) if pe_csi_a500 else None
        a500_col = _find_col(qvix_val, CSI_A500_QVIX_BANDS) if qvix_val else None
        if a500_row is not None and a500_col is not None:
            st.dataframe(_render_matrix(CSI_A500_MATRIX, a_row_labels, a_col_labels, a500_row, a500_col),
                         use_container_width=True)
        else:
            st.dataframe(pd.DataFrame(CSI_A500_MATRIX, index=a_row_labels, columns=a_col_labels),
                         use_container_width=True)

    st.caption("⚠️ 仅供参考，不构成投资建议。数据来自公开市场，存在延迟。")

    st.divider()

    # ─── Section 5: 黄金定投倍数建议 ───

    st.subheader("黄金定投倍数建议")
    st.caption(
        "基于 MA200乖离率 × VIX 矩阵。黄金无PE，以MA200乖离率作为估值锚。"
        "黄金定位为对冲/压舱石仓位，顶格=5x，整体倍数低于权益类。"
    )
    st.warning("⚠️ 回测验证：黄金两套矩阵（原始+对冲）在2024年结构性牛市中均失效，固定定投反而最优。矩阵策略对黄金的择时价值有限，建议黄金采用固定定投，不依赖矩阵信号。", icon=None)

    gold_entry = market_data.get('gold')
    gold_bias200 = None
    gold_bias_emoji = None
    if gold_entry:
        gold_bias_data = compute_bias(gold_entry)
        gold_bias200 = gold_bias_data.get('bias200')
        gold_bias_emoji = gold_bias_data.get('emoji200')

    mult_gold = lookup_gold_multiplier(gold_bias200, vix_val)
    color = _mult_color(mult_gold)

    if gold_bias200 is not None and vix_val:
        sign = '+' if gold_bias200 >= 0 else ''
        st.markdown(
            f"<div style='border:1px solid #ddd; border-radius:8px; padding:16px; text-align:center; max-width:300px;'>"
            f"<div style='font-size:14px; color:#666; margin-bottom:8px;'>黄金 (USD/oz)</div>"
            f"<div style='font-size:13px; color:#999;'>MA200乖离 {sign}{gold_bias200:.1f}% {gold_bias_emoji or ''} × VIX {vix_val:.1f}</div>"
            f"<div style='font-size:40px; font-weight:bold; color:{color}; margin:12px 0;'>{mult_gold}</div>"
            f"<div style='font-size:11px; color:#aaa;'>顶格 = 5x（对冲仓，低于权益类顶格10x）</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.info("黄金: 数据不完整，无法计算")

    # 黄金矩阵表格
    gold_row_labels = ['乖离>+20%', '乖离+10~+20%', '乖离-5~+10%', '乖离-10~-5%', '乖离<-10%']
    gold_col_labels = ['VIX<18', 'VIX 18-25', 'VIX 25-35', 'VIX>35']

    st.caption("黄金完整矩阵（🟨 当前位置）")
    gold_row = _find_a_row(gold_bias200, GOLD_BIAS_BANDS) if gold_bias200 is not None else None
    gold_col = _find_col(vix_val, GOLD_VIX_BANDS) if vix_val else None
    if gold_row is not None and gold_col is not None:
        st.dataframe(_render_matrix(GOLD_MATRIX, gold_row_labels, gold_col_labels, gold_row, gold_col),
                     use_container_width=True)
    else:
        st.dataframe(pd.DataFrame(GOLD_MATRIX, index=gold_row_labels, columns=gold_col_labels),
                     use_container_width=True)
    st.caption("⚠️ 基于黄金自身乖离率，适合趋势跟踪逻辑。")

    # ── 黄金对冲矩阵（标普PE×VIX，反向对冲逻辑）──────────────
    st.divider()
    st.subheader("黄金对冲矩阵（标普PE × VIX）")
    st.caption(
        "**对冲逻辑**：股市高估+恐慌时加仓黄金（对冲价值高），股市低估+平静时减仓（不需要对冲）。"
        "与上方乖离率矩阵方向相反，适合将黄金定位为压舱石/对冲工具的投资者。"
    )

    from market_monitor import GOLD_HEDGE_PE_BANDS, GOLD_HEDGE_VIX_BANDS, GOLD_HEDGE_MATRIX

    mult_gold_hedge = lookup_gold_hedge_multiplier(pe_sp, vix_val)
    color_hedge = _mult_color(mult_gold_hedge)

    if pe_sp and vix_val:
        st.markdown(
            f"<div style='border:1px solid #ddd; border-radius:8px; padding:16px; text-align:center; max-width:300px;'>"
            f"<div style='font-size:14px; color:#666; margin-bottom:8px;'>黄金对冲建议</div>"
            f"<div style='font-size:13px; color:#999;'>标普PE {pe_sp:.1f} × VIX {vix_val:.1f}</div>"
            f"<div style='font-size:40px; font-weight:bold; color:{color_hedge}; margin:12px 0;'>{mult_gold_hedge}</div>"
            f"<div style='font-size:11px; color:#aaa;'>顶格 = 5x</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.info("标普PE或VIX数据不完整，无法计算对冲建议")

    hedge_row_labels = ['PE>32（高估）', 'PE 29-32', 'PE 26-29', 'PE 23-26', 'PE 20-23', 'PE 17-20', 'PE<17（低估）']
    hedge_col_labels = ['VIX<18(平静)', 'VIX 18-25(警觉)', 'VIX 25-35(恐慌)', 'VIX>35(极恐)']

    st.caption("黄金对冲完整矩阵（🟨 当前位置）")
    hedge_row = _find_row(pe_sp, GOLD_HEDGE_PE_BANDS) if pe_sp else None
    hedge_col = _find_col(vix_val, GOLD_HEDGE_VIX_BANDS) if vix_val else None
    if hedge_row is not None and hedge_col is not None:
        st.dataframe(_render_matrix(GOLD_HEDGE_MATRIX, hedge_row_labels, hedge_col_labels, hedge_row, hedge_col),
                     use_container_width=True)
    else:
        st.dataframe(pd.DataFrame(GOLD_HEDGE_MATRIX, index=hedge_row_labels, columns=hedge_col_labels),
                     use_container_width=True)
    st.caption("⚠️ 仅供参考，不构成投资建议。数据来自公开市场，存在延迟。")

    # ─── Section: 个股基本面 ───

    st.divider()
    _data_dir = os.path.dirname(csv_path)
    with st.expander("📊 个股基本面", expanded=False):

        from fundamentals import get_yf_symbol, get_show_fundamentals, update_show_fundamentals
        _yf_map   = load_yf_symbols(_data_dir)
        # show_fundamentals=True 的才展示基本面
        _sym_map  = {
            k: get_yf_symbol(_yf_map, k)
            for k in _yf_map
            if not k.startswith('_') and get_show_fundamentals(_yf_map, k)
        }

        _latest_holdings = raw_df[raw_df['Date'] == raw_df['Date'].max()]
        _stock_codes = list(dict.fromkeys(
            row['Code'] for _, row in _latest_holdings.iterrows()
            if row['Code'] in _sym_map
        ))

        _force_refresh    = st.session_state.pop('_fund_refresh_all', False)
        _fund_refresh_code = st.session_state.pop('_fund_refresh_code', None)

        with st.spinner("加载基本面数据...") if _force_refresh else contextlib.nullcontext():
            _fundamentals = {}
            for code in _stock_codes:
                from fundamentals import get_fundamentals
                _fr = _force_refresh or (_fund_refresh_code == code)
                _f = get_fundamentals(_data_dir, code, force_refresh=_fr)
                if _f:
                    _fundamentals[code] = _f

        def _fmt(v, fmt='x', scale=1.0):
            if v is None: return '—'
            try:
                n = float(v) * scale
                if fmt == 'x':          return f'{n:.2f}x'
                if fmt == '%':          return f'{n*100:.2f}%'
                if fmt == '+%':         return f'{n*100:+.1f}%'
                if fmt == 'pct_direct': return f'{n:.2f}%'
                if fmt == 'cny':        return f'¥{n:.2f}'
                if fmt == 'usd':        return f'${n:.2f}'
                return f'{n:.2f}'
            except Exception:
                return '—'

        if _stock_codes:
            for code in _stock_codes:
                name_row = _latest_holdings[_latest_holdings['Code'] == code].iloc[0]
                name     = name_row['Name']
                yf_sym   = _sym_map[code]
                f        = _fundamentals.get(code, {})
                ccy      = name_row.get('Currency', 'CNY')
                eps_fmt  = 'cny' if ccy == 'CNY' else 'usd'

                with st.expander(f"**{name}** ({code} · {yf_sym})", expanded=True):
                    _, btn_col = st.columns([8, 1])
                    with btn_col:
                        if st.button('🔄', key=f'rf_{code}', help='强制刷新数据'):
                            st.session_state['_fund_refresh_code'] = code
                            st.rerun()

                    r1c1, r1c2, r1c3, r1c4, r1c5 = st.columns(5)
                    _price    = f.get('currentPrice')
                    _currency = f.get('currency', '')
                    _price_str = f'{_price:,.2f} {_currency}' if _price else '—'
                    with r1c1: st.metric('当前股价',     _price_str)
                    with r1c2: st.metric('PE (TTM)',    _fmt(f.get('trailingPE'), 'x'))
                    with r1c3: st.metric('Forward PE',  _fmt(f.get('forwardPE'),  'x'))
                    with r1c4: st.metric('PB',          _fmt(f.get('priceToBook'),'x'))
                    with r1c5: st.metric('ROE',         _fmt(f.get('returnOnEquity'), '%'))

                    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
                    with r2c1: st.metric('EPS (TTM)',   _fmt(f.get('trailingEps'), eps_fmt))
                    with r2c2: st.metric('Forward EPS', _fmt(f.get('forwardEps'),  eps_fmt))
                    with r2c3: st.metric('股息率',       _fmt(f.get('dividendYield'), 'pct_direct'))
                    with r2c4: st.metric('营收增长 YoY', _fmt(f.get('revenueGrowth'), '+%'))

                    # PE 历史分位
                    from fundamentals import get_pe_percentile_from_snapshot
                    _pe_val = f.get('trailingPE')
                    _pe_pct = None
                    if any(yf_sym.endswith(s) for s in ['.SS', '.SZ']) and _pe_val:
                        # A股：akshare 实时拉取历史 PE
                        try:
                            import akshare as _ak
                            _ak_code = code if code.isdigit() else code[2:].zfill(5)
                            _pe_df = _ak.stock_zh_valuation_baidu(symbol=_ak_code, indicator='市盈率(TTM)')
                            if _pe_df is not None and len(_pe_df) >= 10:
                                _vals = _pe_df['value'].dropna().tolist()
                                _pct_val = sum(1 for v in _vals if v <= _pe_val) / len(_vals) * 100
                                _pe_pct = {
                                    'percentile': round(_pct_val, 1),
                                    'pe_min':     round(min(_vals), 2),
                                    'pe_max':     round(max(_vals), 2),
                                    'pe_median':  round(sorted(_vals)[len(_vals)//2], 2),
                                    'days':       len(_vals),
                                }
                        except Exception:
                            pass
                    else:
                        # 港股(.HK) / 美股/ADR：从 pe_history_us.json 快照读取
                        _pe_pct = get_pe_percentile_from_snapshot(_data_dir, yf_sym, _pe_val)
                    if _pe_pct:
                        _pct = _pe_pct['percentile']
                        _pct_color = '#d32f2f' if _pct >= 80 else ('#2e7d32' if _pct <= 20 else '#888')
                        st.markdown(
                            f"<div style='font-size:12px; color:#888; margin-top:4px;'>"
                            f"PE历史分位（近{_pe_pct['days']}天）："
                            f"<span style='color:{_pct_color}; font-weight:bold;'>{_pct:.1f}%</span>"
                            f"　区间 {_pe_pct['pe_min']:.1f}x – {_pe_pct['pe_max']:.1f}x"
                            f"　中位 {_pe_pct['pe_median']:.1f}x"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    if not f:
                        st.caption('⚠️ 数据暂不可用，请检查 YF Symbol 或网络连接')
        else:
            st.info('暂无持仓个股匹配 YF Symbol 映射。请在下方添加映射后，确认持仓中有对应 Code。')

        # YF Symbol 管理
        st.divider()
        with st.expander("管理 YF Symbol 映射", expanded=False):
            st.caption("""**Symbol 填写规则：**
- A股上海（6开头）：`601838` → `601838.SS`
- A股深圳（0/3开头）：`000001` → `000001.SZ`
- 港股（去掉前导零）：`HK0700` → `0700.HK`
- 美股：直接用 ticker（`NVDA`、`AAPL`）
- 欧股 ADR：`SAP.DE` → `SAP`

**展示基本面**：勾选后该标的出现在个股基本面面板；不勾选则仅用于价格刷新路由。""")

            # 全量映射（含 show_fundamentals=false 的）
            _all_sym_map = {
                k: _yf_map[k] for k in _yf_map if not k.startswith('_')
            }
            if _all_sym_map:
                st.markdown("**当前映射：**")
                for code, entry in _all_sym_map.items():
                    sym  = get_yf_symbol(_yf_map, code)
                    show = get_show_fundamentals(_yf_map, code)
                    mc1, mc2, mc3, mc4 = st.columns([2, 2, 2, 1])
                    with mc1: st.code(code)
                    with mc2: st.code(sym)
                    with mc3:
                        new_show = st.checkbox('展示基本面', value=show, key=f'show_{code}')
                        if new_show != show:
                            update_show_fundamentals(_data_dir, code, new_show)
                            st.rerun()
                    with mc4:
                        if st.button('🗑️', key=f'del_{code}', help=f'删除 {code}'):
                            remove_yf_symbol(_data_dir, code)
                            st.rerun()
            else:
                st.info('暂无映射，请添加。')

            st.markdown("**新增映射：**")
            add_c1, add_c2, add_c3, add_c4 = st.columns([2, 2, 2, 1])
            with add_c1:
                new_code = st.text_input('portfolio.csv Code', key='new_yf_code',
                                         placeholder='如 601838')
            with add_c2:
                new_sym = st.text_input('YF Symbol', key='new_yf_sym',
                                        placeholder='如 601838.SS')
            with add_c3:
                new_show_fund = st.checkbox('展示基本面', value=True, key='new_yf_show')
            with add_c4:
                st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
                if st.button('➕', key='add_yf'):
                    if new_code.strip() and new_sym.strip():
                        add_yf_symbol(_data_dir, new_code.strip(), new_sym.strip(),
                                      show_fundamentals=new_show_fund)
                        st.success(f'已添加 {new_code} → {new_sym}')
                        st.rerun()
                    else:
                        st.warning('Code 和 Symbol 均不能为空')

    # ═══════════════════════════════════════════════════════════
    # AH 溢价监测
    # ═══════════════════════════════════════════════════════════

    st.divider()
    with st.expander("🔀 AH 股溢价监测", expanded=False):
        from ah_monitor import (
            load_ah_config, get_ah_data,
            add_ah_stock, remove_ah_stock,
        )

        _ah_force = st.session_state.pop('_ah_force_refresh', False)
        with st.spinner("拉取 AH 股价格...") if _ah_force else contextlib.nullcontext():
            _ah_results = get_ah_data(_data_dir, force_refresh=_ah_force)

        if st.button('🔄 刷新', key='ah_refresh'):
            st.session_state['_ah_force_refresh'] = True
            st.rerun()

        # ── 溢价率汇总表 ──
        if _ah_results:
            _ah_rows = []
            for r in _ah_results:
                _prem = r['premium']
                if _prem is None:
                    _prem_str = '—'
                elif _prem >= 120:
                    _prem_str = f'🟢 {_prem:.1f}'
                elif _prem >= 90:
                    _prem_str = f'🟡 {_prem:.1f}'
                else:
                    _prem_str = f'🔴 {_prem:.1f}'

                _ah_rows.append({
                    '名称':       r['name'],
                    'A股价格':    f"¥{r['a_price']:.2f}" if r['a_price'] else '—',
                    'H股价格':    f"HK${r['h_price']:.2f}" if r['h_price'] else '—',
                    'H股(CNY)':  f"¥{r['h_price_cny']:.3f}" if r['h_price_cny'] else '—',
                    '溢价率':     _prem_str,
                    '1年分位':    f"{r['pct_1y']:.0f}%" if r['pct_1y'] is not None else '积累中',
                    '信号':       r['signal'],
                })
            import pandas as _pd_ah
            st.dataframe(_pd_ah.DataFrame(_ah_rows), use_container_width=True, hide_index=True)
            st.caption(
                "溢价率 = A股价格 ÷ (H股价格 × HKD/CNY) × 100。"
                "🟢 >120 港股便宜  🟡 90-120 接近平价  🔴 <90 港股贵。"
                f"当前 HKD/CNY = {_ah_results[0]['hkd_cny']:.4f}"
            )
        else:
            st.info("暂无关注标的，请在下方添加。")

        # ── 管理 UI ──
        with st.expander("⚙ 管理关注标的", expanded=False):
            _ah_config = load_ah_config(_data_dir)
            for _s in _ah_config.get('stocks', []):
                _sc1, _sc2, _sc3, _sc4 = st.columns([2, 2, 2, 1])
                with _sc1: st.text(_s['name'])
                with _sc2: st.text(_s['a_code'])
                with _sc3: st.text(_s['h_code'])
                with _sc4:
                    if st.button('🗑', key=f'ah_del_{_s["a_code"]}'):
                        remove_ah_stock(_data_dir, _s['a_code'])
                        st.rerun()

            st.markdown("**新增标的：**")
            _an1, _an2, _an3, _an4 = st.columns([2, 2, 2, 1])
            with _an1: _add_ah_name  = st.text_input('名称',   key='ah_add_name',   placeholder='如 中海油')
            with _an2: _add_ah_acode = st.text_input('A股代码', key='ah_add_acode', placeholder='如 600938.SS')
            with _an3: _add_ah_hcode = st.text_input('H股代码', key='ah_add_hcode', placeholder='如 0883.HK')
            with _an4:
                st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
                if st.button('➕', key='ah_add_btn'):
                    if _add_ah_name.strip() and _add_ah_acode.strip() and _add_ah_hcode.strip():
                        add_ah_stock(_data_dir, _add_ah_name, _add_ah_acode, _add_ah_hcode)
                        st.rerun()
                    else:
                        st.warning('三个字段均不能为空')

    # ═══════════════════════════════════════════════════════════
    # DCA Plan 定投管理
    # ═══════════════════════════════════════════════════════════

    st.divider()
    st.subheader("📅 定投计划管理")
    st.caption("本周建议 = 基础金额 × 市场温度计倍数，取整到10元。执行后正常维护 Weekly Update，无额外步骤。")

    from dca_manager import (
        load_dca_config, save_dca_config, add_plan, update_plan, remove_plan,
        compute_all_suggestions, _ASSET_CLASS_LABELS, _FREQUENCIES, _FREQ_LABELS,
    )

    _dca_config = load_dca_config(_data_dir)
    _dca_plans  = _dca_config.get('plans', [])

    # ── 展示区 ──────────────────────────────────────────────
    _suggestions = compute_all_suggestions(_dca_plans, market_data)

    if _suggestions:
        _rows = []
        _total_suggested = 0
        for _plan, _sug in _suggestions:
            _mult = _sug['multiplier_str']
            _arrow = _sug['arrow']
            _ac = _plan.get('asset_class', '')
            if _sug['unit'] == 'gram':
                _base_str = f"{_plan.get('base_amount_unit', '—')}g"
                _sug_str  = f"{_sug['suggested_unit']:.0f}g"
                if _sug['suggested_cny']:
                    _sug_str += f"（≈¥{_sug['suggested_cny']:,}）"
            else:
                _base_str = f"¥{_plan.get('base_amount_cny', 0):,}"
                _sug_str  = f"¥{_sug['suggested_cny']:,}"
            # 标普/黄金加警示标注
            _signal_str = f"{_mult} {_arrow}"
            if _ac == 'US_Blend_Fund':
                _signal_str += ' ⚠️'
            elif _ac == 'Gold':
                _signal_str += ' ⚠️'
            _rows.append({
                '标的':      _plan.get('name', '—'),
                '类型':      _ASSET_CLASS_LABELS.get(_ac, _ac or '—'),
                '平台':      _plan.get('platform', '—'),
                '频率':      _FREQ_LABELS.get(_plan.get('frequency', 'weekly'), _plan.get('frequency', '—')),
                '基础金额':  _base_str,
                '温度计信号': _signal_str,
                '本周建议':  _sug_str,
            })
            if _sug['suggested_cny']:
                _total_suggested += _sug['suggested_cny']
        import pandas as _pd_dca
        _dca_df = _pd_dca.DataFrame(_rows)
        st.dataframe(_dca_df, use_container_width=True, hide_index=True)
        st.markdown(f"**本周建议总投入：¥{_total_suggested:,}**")
        st.caption("⚠️ 回测验证：黄金两套矩阵在结构性行情中均失效，建议黄金采用固定定投。标普500矩阵策略经20年回测验证有效（核心在危机加仓），可正常执行。")
    else:
        st.info("暂无启用的定投计划，点击下方「管理定投计划」添加。")

    # ═══════════════════════════════════════════════════════════
    # 弹药池与现金流压力测试
    # ═══════════════════════════════════════════════════════════
    st.divider()
    st.subheader("💰 弹药健康度")

    # ── 数据准备 ──────────────────────────────────────────────
    from fi_engine import load_fi_config as _load_fi_cfg_ammo
    _ammo_fi_cfg = _load_fi_cfg_ammo(_data_dir)
    _emergency_reserve = float(_ammo_fi_cfg.get('emergency_reserve_cny', 200000))
    _top_mult_equity   = float(_ammo_fi_cfg.get('top_multiplier_equity', 10.0))
    _top_mult_gold     = float(_ammo_fi_cfg.get('top_multiplier_gold', 5.0))
    _monthly_savings   = (float(_ammo_fi_cfg.get('monthly_income_cny', 0))
                          * float(_ammo_fi_cfg.get('monthly_savings_target_pct', 0)))

    _ammo_latest_date = raw_df['Date'].max()
    _ammo_latest      = raw_df[raw_df['Date'] == _ammo_latest_date]
    _cash_val  = float(_ammo_latest[_ammo_latest['Asset_Class'] == 'Cash']['Total_Value'].sum())
    _fi_val    = float(_ammo_latest[_ammo_latest['Asset_Class'] == 'Fixed_Income']['Total_Value'].sum())
    _ammo_pool = _cash_val + _fi_val - _emergency_reserve

    # 月消耗（按当前信号，按频率折算）
    _monthly_cost = 0.0
    if _suggestions:
        for _ap, _as in _suggestions:
            _acny = _as.get('suggested_cny') or 0
            _freq = _ap.get('frequency', 'weekly')
            if _freq == 'weekly':
                _monthly_cost += _acny * 4.33
            elif _freq == 'biweekly':
                _monthly_cost += _acny * 2.17
            else:
                _monthly_cost += _acny
    _weekly_cost = _total_suggested if _suggestions else 0.0

    # 全部顶格每周消耗
    _gold_price_cny = (market_data.get('gold', {}).get('price') or 0)
    _max_weekly = 0.0
    for _ap in _dca_plans:
        if not _ap.get('enabled', True):
            continue
        if _ap.get('asset_class') == 'Gold':
            _base_cny = float(_ap.get('base_amount_unit', 0)) * _gold_price_cny
            _max_weekly += _base_cny * _top_mult_gold
        else:
            _base_cny = float(_ap.get('base_amount_cny', 0))
            _max_weekly += _base_cny * _top_mult_equity

    _weeks_current = (_ammo_pool / _weekly_cost) if _weekly_cost > 0 else float('inf')
    _weeks_extreme = (_ammo_pool / _max_weekly)  if _max_weekly > 0 else float('inf')

    # 颜色标签
    def _ammo_status(weeks):
        if weeks == float('inf'):
            return '🟢', '无限'
        if weeks > 8:
            return '🟢', f'{weeks:.1f} 周'
        if weeks >= 4:
            return '🟡', f'⚠️ {weeks:.1f} 周'
        return '🔴', f'🔴 {weeks:.1f} 周'

    _ic, _iv = _ammo_status(_weeks_current)
    _ec, _ev = _ammo_status(_weeks_extreme)

    # ── KPI 展示 ──────────────────────────────────────────────
    _ac1, _ac2, _ac3 = st.columns(3)
    with _ac1:
        st.metric("弹药池余额", f"¥{_ammo_pool:,.0f}")
    with _ac2:
        st.metric("可支撑（当前信号）", _iv)
    with _ac3:
        st.metric("可支撑（全部顶格）", _ev)

    st.caption(
        f"弹药池 = Cash ¥{_cash_val:,.0f} + 固收 ¥{_fi_val:,.0f} - 备用金 ¥{_emergency_reserve:,.0f}　｜　"
        f"月消耗速率 ¥{_monthly_cost:,.0f}（当前信号持续）　｜　月新增储蓄 ¥{_monthly_savings:,.0f}"
    )

    if _weeks_extreme < 4:
        st.error(f"🔴 警告：全部顶格仅能支撑 {_weeks_extreme:.1f} 周，建议补充现金或降低基础金额。")
    elif _weeks_extreme < 8:
        st.warning(f"⚠️ 关注：全部顶格可支撑 {_weeks_extreme:.1f} 周（健康基准：≥8周）。")

    # ── 压力测试（折叠）─────────────────────────────────────
    with st.expander("⚡ 压力测试", expanded=False):
        _sc1, _sc2, _sc3, _sc4 = st.columns([2, 2, 2, 1])
        with _sc1:
            _stress_weeks = st.number_input("极端持续周数", min_value=1, max_value=52,
                                            value=8, step=1, key='ammo_stress_weeks')
        with _sc2:
            _stress_mode = st.selectbox("信号强度", ["当前信号", "全部顶格"],
                                        key='ammo_stress_mode')
        with _sc3:
            _stress_savings = st.number_input("月新增储蓄（¥）", min_value=0,
                                              value=int(_monthly_savings), step=1000,
                                              key='ammo_stress_savings')
        with _sc4:
            st.write("")
            st.write("")
            _run_stress = st.button("运行压测", key='ammo_run_stress')

        if _stress_mode == "当前信号":
            _stress_weekly = _weekly_cost
        else:
            _stress_weekly = _max_weekly

        # 压测摘要（按钮触发或直接展示）
        if _stress_weekly > 0:
            _total_consume = _stress_weekly * _stress_weeks
            _months_in_period = _stress_weeks / 4.33
            _savings_inject   = _stress_savings * _months_in_period
            _end_balance      = _ammo_pool - _total_consume + _savings_inject

            # 临界周数（逐步模拟，含月储蓄补充）
            _b = _ammo_pool
            _critical_week = None
            for _w in range(1, _stress_weeks + 1):
                _b -= _stress_weekly
                if (_w % 4) == 0:
                    _b += _stress_savings
                if _b < 0 and _critical_week is None:
                    _critical_week = _w

            st.markdown(f"""
| 项目 | 数值 |
|------|------|
| 每周消耗 | ¥{_stress_weekly:,.0f} |
| {_stress_weeks}周总消耗 | ¥{_total_consume:,.0f} |
| 弹药池起始 | ¥{_ammo_pool:,.0f} |
| 月储蓄补充（{_months_in_period:.1f}个月）| +¥{_savings_inject:,.0f} |
| {_stress_weeks}周后余额 | ¥{_end_balance:,.0f} {'✅' if _end_balance >= 0 else '⚠️ 不足'} |
| 临界周数 | {'**第 ' + str(_critical_week) + ' 周耗尽**' if _critical_week else f'≥{_stress_weeks}周，不会耗尽'} |
""")

            if _critical_week:
                _half_base  = int(_ammo_pool / (_critical_week / 0.5) / 1000) * 1000 if _stress_weekly else 0
                st.warning(
                    f"**建议方案 A**：将所有基础金额降低 50%，每周消耗 ¥{_stress_weekly/2:,.0f}，可延长至约 {_critical_week * 2} 周\n\n"
                    f"**建议方案 B**：补充现金至弹药池达 ¥{_total_consume - _savings_inject:,.0f}，保证全程不中断"
                )

            # 折线图
            import plotly.graph_objects as _go_ammo
            _bal_list = []
            _b2 = _ammo_pool
            for _w2 in range(_stress_weeks + 1):
                _bal_list.append(_b2)
                if _w2 < _stress_weeks:
                    _b2 -= _stress_weekly
                    if _w2 % 4 == 3:
                        _b2 += _stress_savings
            _fig_ammo = _go_ammo.Figure()
            _fig_ammo.add_trace(_go_ammo.Scatter(
                x=list(range(_stress_weeks + 1)), y=_bal_list,
                mode='lines+markers', name='弹药池余额',
                line=dict(color='#2196F3', width=2),
            ))
            _fig_ammo.add_hline(y=0, line_dash='dash', line_color='red',
                                annotation_text='耗尽线', annotation_position='bottom right')
            _fig_ammo.update_layout(
                xaxis_title='第N周', yaxis_title='弹药池余额（¥）',
                height=320, margin=dict(l=40, r=20, t=20, b=40),
                yaxis=dict(tickformat=',.0f'),
            )
            st.plotly_chart(_fig_ammo, use_container_width=True)
        else:
            st.info("暂无定投计划，无法计算消耗速率。")

    # ── 配置区（可折叠）────────────────────────────────────
    with st.expander("⚙ 管理定投计划", expanded=False):

        # 初始化 session_state
        if 'dca_editing' not in st.session_state:
            st.session_state['dca_editing'] = {}
        if 'dca_adding' not in st.session_state:
            st.session_state['dca_adding'] = False

        # ── 现有计划 ──
        for _plan in _dca_plans:
            _pid = _plan['id']
            with st.container(border=True):
                _hc1, _hc2, _hc3 = st.columns([5, 1, 1])
                with _hc1:
                    _enabled = st.toggle(
                        _plan.get('name', '未命名'),
                        value=_plan.get('enabled', True),
                        key=f'dca_enabled_{_pid}',
                    )
                    if _enabled != _plan.get('enabled', True):
                        update_plan(_data_dir, _pid, {'enabled': _enabled})
                        st.rerun()
                with _hc2:
                    if st.button('✏ 编辑', key=f'dca_edit_{_pid}'):
                        st.session_state['dca_editing'][_pid] = True
                with _hc3:
                    if st.button('🗑 删除', key=f'dca_del_{_pid}', type='secondary'):
                        remove_plan(_data_dir, _pid)
                        st.rerun()

                if st.session_state['dca_editing'].get(_pid):
                    _ec1, _ec2, _ec3 = st.columns(3)
                    with _ec1:
                        _new_name = st.text_input('标的名称', value=_plan.get('name', ''), key=f'dca_name_{_pid}')
                        _new_code = st.text_input('基金/股票代码', value=_plan.get('code', ''), key=f'dca_code_{_pid}')
                    with _ec3:
                        _ac_idx = list(_ASSET_CLASS_LABELS.keys()).index(_plan.get('asset_class', 'CN_Index_Fund')) \
                                  if _plan.get('asset_class') in _ASSET_CLASS_LABELS else 0
                        _new_ac = st.selectbox('资产类别', options=list(_ASSET_CLASS_LABELS.keys()),
                                               format_func=lambda x: _ASSET_CLASS_LABELS[x],
                                               index=_ac_idx, key=f'dca_ac_{_pid}')
                        _freq_idx = _FREQUENCIES.index(_plan.get('frequency', 'weekly')) \
                                    if _plan.get('frequency') in _FREQUENCIES else 0
                        _new_freq = st.selectbox('频率', options=_FREQUENCIES,
                                                 format_func=lambda x: _FREQ_LABELS[x],
                                                 index=_freq_idx, key=f'dca_freq_{_pid}')
                    with _ec2:
                        _new_plat = st.text_input('执行平台', value=_plan.get('platform', ''), key=f'dca_plat_{_pid}')
                        _is_gold_edit = (_new_ac == 'Gold')
                        if _is_gold_edit:
                            _new_base_unit = st.number_input(
                                '基础买入克数（g）',
                                value=int(_plan.get('base_amount_unit', 2)),
                                min_value=1, step=1, key=f'dca_base_{_pid}',
                            )
                        else:
                            _new_base_cny = st.number_input('基础金额（CNY）', value=int(_plan.get('base_amount_cny', 500)), min_value=0, step=100, key=f'dca_base_{_pid}')
                    _new_note = st.text_input('备注', value=_plan.get('note', ''), key=f'dca_note_{_pid}')
                    _sv1, _sv2 = st.columns([1, 5])
                    with _sv1:
                        if st.button('💾 保存', key=f'dca_save_{_pid}'):
                            if _is_gold_edit:
                                _save_fields = {
                                    'name': _new_name, 'code': _new_code,
                                    'platform': _new_plat,
                                    'asset_class': _new_ac, 'frequency': _new_freq,
                                    'note': _new_note,
                                    'unit': 'gram',
                                    'base_amount_unit': _new_base_unit,
                                    'min_unit': 1,
                                }
                            else:
                                _save_fields = {
                                    'name': _new_name, 'code': _new_code,
                                    'platform': _new_plat, 'base_amount_cny': _new_base_cny,
                                    'asset_class': _new_ac, 'frequency': _new_freq,
                                    'note': _new_note,
                                    'unit': 'cny',
                                }
                            update_plan(_data_dir, _pid, _save_fields)
                            st.session_state['dca_editing'].pop(_pid, None)
                            st.rerun()
                    with _sv2:
                        if st.button('取消', key=f'dca_cancel_{_pid}'):
                            st.session_state['dca_editing'].pop(_pid, None)
                            st.rerun()

        # ── 新增计划 ──
        if st.button('＋ 新增定投计划', key='dca_add_btn'):
            st.session_state['dca_adding'] = True

        if st.session_state.get('dca_adding'):
            st.markdown("**新增定投计划**")
            _na1, _na2, _na3 = st.columns(3)
            with _na1:
                _add_name = st.text_input('标的名称', key='dca_add_name')
                _add_code = st.text_input('基金/股票代码', key='dca_add_code')
            with _na3:
                _add_ac = st.selectbox('资产类别', options=list(_ASSET_CLASS_LABELS.keys()),
                                       format_func=lambda x: _ASSET_CLASS_LABELS[x], key='dca_add_ac')
                _add_freq = st.selectbox('频率', options=_FREQUENCIES,
                                         format_func=lambda x: _FREQ_LABELS[x], key='dca_add_freq')
            with _na2:
                _add_plat = st.text_input('执行平台', key='dca_add_plat')
                _is_gold_add = (_add_ac == 'Gold')
                if _is_gold_add:
                    _add_base_unit = st.number_input('基础买入克数（g）', value=2, min_value=1, step=1, key='dca_add_base')
                else:
                    _add_base_cny  = st.number_input('基础金额（CNY）', value=500, min_value=0, step=100, key='dca_add_base')
            _add_note = st.text_input('备注（可选）', key='dca_add_note')
            _ab1, _ab2 = st.columns([1, 5])
            with _ab1:
                if st.button('✅ 添加', key='dca_add_confirm'):
                    if _add_name.strip():
                        if _is_gold_add:
                            _new_plan = {
                                'name': _add_name.strip(),
                                'code': _add_code.strip(),
                                'platform': _add_plat.strip(),
                                'asset_class': _add_ac,
                                'frequency': _add_freq,
                                'enabled': True,
                                'note': _add_note.strip(),
                                'unit': 'gram',
                                'base_amount_unit': _add_base_unit,
                                'min_unit': 1,
                            }
                        else:
                            _new_plan = {
                                'name': _add_name.strip(),
                                'code': _add_code.strip(),
                                'platform': _add_plat.strip(),
                                'base_amount_cny': _add_base_cny,
                                'asset_class': _add_ac,
                                'frequency': _add_freq,
                                'enabled': True,
                                'note': _add_note.strip(),
                                'unit': 'cny',
                            }
                        add_plan(_data_dir, _new_plan)
                        st.session_state['dca_adding'] = False
                        st.rerun()
                    else:
                        st.warning('标的名称不能为空')
            with _ab2:
                if st.button('取消', key='dca_add_cancel'):
                    st.session_state['dca_adding'] = False
                    st.rerun()

# ═══════════════════════════════════════════════════════════
# Tab 5: Backtest
# ═══════════════════════════════════════════════════════════

with tab_backtest:
    st.header("定投策略回测")
    st.caption(
        "对比「固定金额定投」与「矩阵策略定投」的历史表现。"
        "相同起始日期、相同基准金额，验证市场温度计矩阵是否真正有效。"
        "权益类使用 PE×VIX/QVIX 矩阵；黄金使用 MA200乖离率×VIX 矩阵。"
    )

    from datetime import date as _date

    _TARGET_NAMES = {
        'csi300':   'CSI 300 沪深300',
        'csi_a500': '中证A500',
        'sp500':    '标普500 (^GSPC)',
        'ndx100':   '纳指100 (^NDX)',
        'gold':     '黄金 (GC=F)',
    }
    _DEFAULT_STARTS = {
        'csi300':   _date(2015, 3, 1), 'csi_a500': _date(2015, 3, 1),
        'sp500':    _date(2000, 1, 1), 'ndx100':   _date(2010, 1, 1),
        'gold':     _date(2000, 1, 1),
    }
    _MIN_DATES = {
        'csi300':   _date(2015, 3, 1), 'csi_a500': _date(2015, 3, 1),
        'sp500':    _date(1990, 1, 1), 'ndx100':   _date(2009, 10, 1),
        'gold':     _date(1990, 1, 1),
    }
    _CURRENCY = {
        'csi300': '¥', 'csi_a500': '¥',
        'sp500': '$', 'ndx100': '$', 'gold': '$',
    }

    def _make_effectiveness_scatter(points: list, currency: str = '¥'):
        """四象限散点图：X=XIRR超额(%)，Y=绝对盈亏超额(CNY)。"""
        import plotly.graph_objects as _go

        xs = [p['xirr_excess'] for p in points]
        ys = [p['pl_excess']    for p in points]
        labels = [p['label']   for p in points]

        # 坐标轴范围：覆盖所有点，两侧留 30% padding，且至少有基本空间
        max_abs_x = max((abs(x) for x in xs), default=1)
        max_abs_y = max((abs(y) for y in ys), default=1000)
        x_pad = max_abs_x * 1.4
        y_pad = max_abs_y * 1.4

        fig = _go.Figure()

        # 四象限背景色
        for x0, x1, y0, y1, color, label in [
            (0, x_pad,  0, y_pad,  'rgba(46,125,50,0.07)',  ''),   # I  右上
            (-x_pad, 0, 0, y_pad,  'rgba(255,193,7,0.07)',  ''),   # II 左上
            (-x_pad, 0, -y_pad, 0, 'rgba(211,47,47,0.07)',  ''),   # III左下
            (0, x_pad,  -y_pad, 0, 'rgba(255,152,0,0.07)',  ''),   # IV 右下
        ]:
            fig.add_shape(type='rect', x0=x0, x1=x1, y0=y0, y1=y1,
                          fillcolor=color, line_width=0, layer='below')

        # 象限标注（用 paper 坐标系，与数据范围无关，永远在角落）
        for px, py, txt, xanc, yanc in [
            (0.97, 0.97, '多投多赚 ✓', 'right', 'top'),
            (0.03, 0.97, '少投高效',   'left',  'top'),
            (0.03, 0.03, '两者皆输 ✗', 'left',  'bottom'),
            (0.97, 0.03, '多投无超额', 'right', 'bottom'),
        ]:
            fig.add_annotation(
                x=px, y=py, text=txt, showarrow=False,
                xref='paper', yref='paper',
                font=dict(size=11, color='#aaa'),
                xanchor=xanc, yanchor=yanc,
            )

        # 原点十字线
        fig.add_shape(type='line', x0=-x_pad, x1=x_pad, y0=0, y1=0,
                      line=dict(color='#ccc', width=1))
        fig.add_shape(type='line', x0=0, x1=0, y0=-y_pad, y1=y_pad,
                      line=dict(color='#ccc', width=1))

        # 散点
        colors = ['#1565c0','#c62828','#2e7d32','#f57f17','#6a1b9a']
        for i, (p, x, y, lbl) in enumerate(zip(points, xs, ys, labels)):
            fig.add_trace(_go.Scatter(
                x=[x], y=[y], mode='markers+text',
                text=[lbl], textposition='top center',
                marker=dict(size=14, color=colors[i % len(colors)]),
                name=lbl,
                hovertemplate=(
                    f"<b>{lbl}</b><br>"
                    f"XIRR超额: {x:+.2f}%<br>"
                    f"盈亏超额: {currency}{y:+,.0f}<extra></extra>"
                ),
            ))

        fig.update_layout(
            height=420,
            xaxis_title='XIRR 超额（矩阵 - 固定，%）',
            yaxis_title=f'绝对盈亏超额（矩阵 - 固定，{currency}）',
            showlegend=False,
            xaxis=dict(range=[-x_pad, x_pad], zeroline=False),
            yaxis=dict(range=[-y_pad, y_pad], zeroline=False),
            margin=dict(t=30, b=50),
        )
        return fig

    # ─── Controls ───────────────────────────────────────────
    bt_col1, bt_col2, bt_col3, bt_col4 = st.columns(4)
    with bt_col1:
        bt_target = st.selectbox(
            "回测标的", options=list(_TARGET_NAMES.keys()),
            format_func=lambda t: _TARGET_NAMES[t], key='bt_target',
        )
    with bt_col2:
        bt_start = st.date_input(
            "起始日期",
            value=_DEFAULT_STARTS.get(bt_target, _date(2015, 1, 1)),
            min_value=_MIN_DATES.get(bt_target, _date(2000, 1, 1)),
            max_value=_date.today() - timedelta(days=365),
            key='bt_start_date',
        )
    with bt_col3:
        bt_end = st.date_input(
            "截止日期",
            value=_date.today(),
            min_value=_MIN_DATES.get(bt_target, _date(2000, 1, 1)),
            max_value=_date.today(),
            key='bt_end_date',
            help="可截短回测区间，用于分析特定市场周期（如排除单边行情）",
        )
    with bt_col4:
        bt_freq = st.radio(
            "定投频率", options=['月频', '周频'], horizontal=True, key='bt_freq',
            help="周频使用每周一作为定投日；PE 数据为月频，同月内各周使用同一 PE 值",
        )

    bt_col4, bt_col5, bt_col6, bt_col7 = st.columns(4)
    with bt_col4:
        bt_base_amount = st.number_input(
            "每期基准金额", min_value=100.0, max_value=1_000_000.0,
            value=1000.0, step=100.0, key='bt_base_amount',
        )
    with bt_col5:
        bt_top_equity = st.number_input(
            "顶格上限（权益类）", min_value=1.0, max_value=20.0,
            value=10.0, step=0.5,
            help="矩阵'顶格'对应的实际倍数（权益类标的）",
            key='bt_top_equity',
        )
    with bt_col6:
        bt_top_gold = st.number_input(
            "顶格上限（黄金）", min_value=1.0, max_value=10.0,
            value=5.0, step=0.5,
            help="矩阵'顶格'对应的实际倍数（黄金）",
            key='bt_top_gold',
        )
    with bt_col7:
        st.markdown("<br>", unsafe_allow_html=True)
        bt_run = st.button("▶ 运行回测", type="primary", key='bt_run', use_container_width=True)
        bt_run_all = st.button("🔭 全标的对比", key='bt_run_all', use_container_width=True,
                               help="批量跑5个标的，在散点图上对比策略有效性（约需30-60秒）")
        bt_gen_report = st.button("📄 生成回测报告", key='bt_gen_report', use_container_width=True,
                                  help="生成全标的 HTML 报告，保存到 data/reports/，可用浏览器打印为PDF")

    if bt_target == 'ndx100':
        st.info(
            "**纳指100 回测说明**：纳指历史 PE 无免费数据源，回测使用标普500 PE（Shiller）作为代理信号。"
            "纳指实际 PE 历史上高于标普（科技溢价），代理信号在科技泡沫期（2000年、2021年）偏乐观。"
            "当前实时展示仍使用 QQQ 真实 PE，本说明仅适用于回测历史数据。"
            "波动率信号使用 **VXN**（纳指专属，来源 CBOE），与实时 Market Tab 保持一致。"
        )

    if bt_target == 'gold':
        bt_gold_hedge = st.radio(
            "黄金矩阵模式",
            options=['原始矩阵（MA200乖离率×VIX）', '对冲矩阵（标普PE×VIX）'],
            index=0,
            horizontal=True,
            key='bt_gold_hedge',
            help="原始：基于黄金自身估值；对冲：基于股市估值，高PE+高VIX时多买黄金",
        )
    else:
        bt_gold_hedge = '原始矩阵（MA200乖离率×VIX）'

    st.divider()

    # ─── 日期校验 ────────────────────────────────────────────
    if bt_end <= bt_start:
        st.warning("截止日期必须晚于起始日期")

    # ─── Run ────────────────────────────────────────────────
    if bt_run:
        top_mult = bt_top_gold if bt_target == 'gold' else bt_top_equity
        _gold_hedge = (bt_target == 'gold' and '对冲矩阵' in bt_gold_hedge)
        with st.spinner(f"正在拉取 {_TARGET_NAMES[bt_target]} 历史数据并运行回测..."):
            try:
                result = run_backtest(
                    target=bt_target,
                    start_date=bt_start.strftime('%Y-%m-%d'),
                    end_date=bt_end.strftime('%Y-%m-%d'),
                    base_amount=bt_base_amount,
                    freq='W' if bt_freq == '周频' else 'M',
                    top_multiplier=top_mult,
                    gold_hedge_mode=_gold_hedge,
                )
                st.session_state['bt_result'] = result
            except Exception as e:
                st.error(f"回测运行失败: {e}")

    # ─── 全标的对比 ──────────────────────────────────────────
    if bt_run_all:
        with st.spinner("正在批量跑5个标的（约需30-60秒）..."):
            try:
                _all_results = run_all_targets(
                    user_start_date=bt_start.strftime('%Y-%m-%d'),
                    base_amount=bt_base_amount,
                    freq='W' if bt_freq == '周频' else 'M',
                    top_multiplier_equity=bt_top_equity,
                    top_multiplier_gold=bt_top_gold,
                    end_date=bt_end.strftime('%Y-%m-%d'),
                )
                st.session_state['bt_all_results'] = _all_results
            except Exception as e:
                st.error(f"全标的对比失败: {e}")

    # ─── 生成回测报告 ────────────────────────────────────
    if bt_gen_report:
        with st.spinner("正在跑全标的回测并生成报告（约需60-90秒）..."):
            try:
                from backtest_report import generate_backtest_report
                report_path = generate_backtest_report(
                    data_dir=os.path.dirname(csv_path),
                    start_date=bt_start.strftime('%Y-%m-%d'),
                    end_date=bt_end.strftime('%Y-%m-%d'),
                    base_amount=bt_base_amount,
                    freq='W' if bt_freq == '周频' else 'M',
                    top_multiplier_equity=bt_top_equity,
                    top_multiplier_gold=bt_top_gold,
                )
                st.success(f"报告已生成：`{os.path.basename(report_path)}`")
                st.caption(f"保存路径：{report_path}　用浏览器打开后可打印为 PDF")
                # 提供下载
                with open(report_path, 'rb') as f:
                    st.download_button(
                        label="⬇ 下载 HTML 报告",
                        data=f.read(),
                        file_name=os.path.basename(report_path),
                        mime='text/html',
                    )
            except Exception as e:
                st.error(f"报告生成失败: {e}")

    result = st.session_state.get('bt_result')

    if result is None:
        st.info("配置回测参数后，点击「▶ 运行回测」开始")
    else:
        fixed      = result['fixed']
        matrix     = result['matrix']
        history_df = result['history']
        cur        = _CURRENCY.get(result['target'], '¥')

        # ─── Section 1: 对比指标卡 ──────────────────────────
        st.subheader("策略对比")

        def _fmt_money(v, currency=cur):
            return f'{currency}{v:,.0f}' if v is not None else '—'

        def _fmt_pct(v):
            return f'{v:+.2f}%' if v is not None else '—'

        def _card(label, f_val, m_val, fmt_fn, higher_is_better=True):
            if f_val is not None and m_val is not None:
                delta = m_val - f_val
                if higher_is_better:
                    dc = '#2e7d32' if delta > 0 else ('#d32f2f' if delta < 0 else '#888')
                else:
                    dc = '#2e7d32' if delta < 0 else ('#d32f2f' if delta > 0 else '#888')
                delta_str = fmt_fn(delta) if callable(fmt_fn) else str(delta)
            else:
                dc, delta_str = '#888', '—'
            return (
                f"<div style='border:1px solid #e0e0e0;border-radius:8px;padding:14px;'>"
                f"<div style='font-size:12px;color:#888;'>{label}</div>"
                f"<div style='font-size:13px;color:#444;margin-top:4px;'>固定: {fmt_fn(f_val)}</div>"
                f"<div style='font-size:13px;color:#444;'>矩阵: {fmt_fn(m_val)}</div>"
                f"<div style='font-size:15px;font-weight:bold;color:{dc};margin-top:6px;'>"
                f"超额: {delta_str}</div></div>"
            )

        c1, c2, c3 = st.columns(3)
        c1.markdown(_card('总投入成本',   fixed['total_cost'],   matrix['total_cost'],   _fmt_money, False), unsafe_allow_html=True)
        c2.markdown(_card('最终市值',     fixed['final_value'],  matrix['final_value'],  _fmt_money, True),  unsafe_allow_html=True)
        c3.markdown(_card('绝对盈亏',     fixed['profit_loss'],  matrix['profit_loss'],  _fmt_money, True),  unsafe_allow_html=True)

        c4, c5, c6 = st.columns(3)
        c4.markdown(_card('XIRR（年化）', fixed['xirr'],         matrix['xirr'],         _fmt_pct,   True),  unsafe_allow_html=True)
        c5.markdown(_card('最大回撤',     fixed['max_drawdown'], matrix['max_drawdown'], _fmt_pct,   False), unsafe_allow_html=True)
        c6.markdown(_card('每元成本市值', fixed['value_per_cost'], matrix['value_per_cost'],
                          lambda v: f'{v:.4f}' if v else '—', True), unsafe_allow_html=True)

        # 定投次数
        st.caption(f"定投次数：固定 {fixed['periods']} 期 / 矩阵 {matrix['periods']} 期（暂停 {fixed['periods']-matrix['periods']} 期）")

        st.divider()

        # ─── Section 2: 累计市值对比 ────────────────────────
        st.subheader("累计市值走势")

        fig_val = go.Figure()
        fig_val.add_trace(go.Scatter(x=history_df['date'], y=history_df['fixed_cum_value'],
                                     name='固定策略 市值', line=dict(color='#4ECDC4', width=2)))
        fig_val.add_trace(go.Scatter(x=history_df['date'], y=history_df['matrix_cum_value'],
                                     name='矩阵策略 市值', line=dict(color='#FF6B6B', width=2)))
        fig_val.add_trace(go.Scatter(x=history_df['date'], y=history_df['fixed_cum_cost'],
                                     name='固定策略 成本', line=dict(color='#4ECDC4', width=1, dash='dash'), opacity=0.5))
        fig_val.add_trace(go.Scatter(x=history_df['date'], y=history_df['matrix_cum_cost'],
                                     name='矩阵策略 成本', line=dict(color='#FF6B6B', width=1, dash='dash'), opacity=0.5))
        fig_val.update_layout(height=400, hovermode='x unified',
                              legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                              yaxis_title=f'金额 ({cur})', margin=dict(t=40, b=40))
        st.plotly_chart(fig_val, use_container_width=True)

        # ─── Section 3: 每期倍数柱状图 ──────────────────────
        st.subheader("每期矩阵倍数 & 指数价格")

        bar_colors = [
            '#d32f2f' if m == 0
            else ('#1565c0' if m >= result['top_multiplier'] * 0.9 else '#2e7d32')
            for m in history_df['multiplier']
        ]
        fig_mult = go.Figure()
        fig_mult.add_trace(go.Bar(x=history_df['date'], y=history_df['multiplier'],
                                  name='矩阵倍数', marker_color=bar_colors, yaxis='y1', opacity=0.8))
        fig_mult.add_trace(go.Scatter(x=history_df['date'], y=history_df['price'],
                                      name='价格', line=dict(color='#FFA726', width=2), yaxis='y2'))
        fig_mult.update_layout(
            height=350,
            yaxis=dict(title='定投倍数', side='left'),
            yaxis2=dict(title=f'价格 ({cur})', side='right', overlaying='y'),
            hovermode='x unified',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            margin=dict(t=40, b=40),
        )
        st.plotly_chart(fig_mult, use_container_width=True)

        # ─── Section 4: 暂停占比饼图 + 明细表 ──────────────
        pie_col, tbl_col = st.columns([1, 2])
        with pie_col:
            st.subheader("资金利用效率")
            pause_n  = int((history_df['multiplier'] == 0).sum())
            invest_n = int((history_df['multiplier'] > 0).sum())
            fig_pie = go.Figure(go.Pie(
                labels=['定投期', '暂停期'],
                values=[invest_n, pause_n],
                marker_colors=['#2e7d32', '#d32f2f'],
                hole=0.4, textinfo='label+percent',
            ))
            fig_pie.update_layout(height=260, margin=dict(t=20, b=20))
            st.plotly_chart(fig_pie, use_container_width=True)

        with tbl_col:
            st.subheader("逐期明细")
            _vol_label = {
                'csi300': 'QVIX', 'csi_a500': 'QVIX',
                'ndx100': 'VXN',
                'sp500': 'VIX', 'gold': 'VIX',
            }.get(result['target'], 'VIX/QVIX')
            show_df = history_df[[
                'date', 'price', 'pe_or_bias', 'vol', 'raw_mult',
                'matrix_amount', 'matrix_cum_cost', 'matrix_cum_value',
            ]].rename(columns={
                'date': '日期', 'price': '价格',
                'pe_or_bias': 'PE/乖离率', 'vol': _vol_label,
                'raw_mult': '矩阵倍数',
                'matrix_amount': '矩阵投入', 'matrix_cum_cost': '矩阵累计成本',
                'matrix_cum_value': '矩阵累计市值',
            })
            st.dataframe(show_df, use_container_width=True, height=260, hide_index=True)

        pe_note = '纳指100使用标普500 PE作为历史代理信号，存在系统性低估风险。 | ' if result['target'] == 'ndx100' else ''
        st.caption(
            f"⚠️ {pe_note}仅供参考，不构成投资建议。"
            "历史回测不代表未来收益。XIRR 为资金加权年化收益率。"
        )

        # ─── 策略有效性散点图（单标的）──────────────────────────
        st.divider()
        st.subheader("策略有效性定位")
        _xe = (matrix['xirr'] - fixed['xirr']) if (matrix['xirr'] and fixed['xirr']) else None
        _ye = (matrix['profit_loss'] - fixed['profit_loss']) if (matrix['profit_loss'] is not None and fixed['profit_loss'] is not None) else None
        if _xe is not None and _ye is not None:
            _scatter_fig = _make_effectiveness_scatter(
                [{'label': _TARGET_NAMES[result['target']], 'xirr_excess': _xe, 'pl_excess': _ye}],
                cur
            )
            st.plotly_chart(_scatter_fig, use_container_width=True)
            st.caption(f"当前标的：{_TARGET_NAMES[result['target']]}，回测区间 {result['start_date']} ~ {result['end_date']}")

    # ─── 全标的对比散点图 ────────────────────────────────────
    _all_results = st.session_state.get('bt_all_results')
    if _all_results:
        st.divider()
        st.subheader("全标的策略有效性对比")
        _pts = [r for r in _all_results if r['xirr_excess'] is not None and r['pl_excess'] is not None]
        _errs = [r for r in _all_results if r.get('error')]
        if _pts:
            _cur_all = '¥'  # 全标的统一用 CNY 口径（已在回测内部换算）
            _scatter_all = _make_effectiveness_scatter(_pts, _cur_all)
            st.plotly_chart(_scatter_all, use_container_width=True)
            # Caption：各标的实际起始日期
            # 去重：黄金两条只显示一次日期
            _seen = set()
            _date_parts = []
            for r in _all_results:
                if not r.get('error'):
                    key = (r['target'], r['actual_start'])
                    if key not in _seen:
                        _seen.add(key)
                        _date_parts.append(f"{_TARGET_NAMES.get(r['target'], r['target'])} {r['actual_start']}")
            _date_notes = ' · '.join(_date_parts)
            st.caption(f"各标的实际回测起始：{_date_notes}　截止：{bt_end.strftime('%Y-%m-%d')}　基准金额：¥{bt_base_amount:,.0f}/期　黄金显示两个点（原始/对冲矩阵）")
        if _errs:
            for r in _errs:
                st.warning(f"⚠️ {r['label']} 回测失败：{r['error']}")

# ═══════════════════════════════════════════════════════════
# Tab 6: Quarterly
# ═══════════════════════════════════════════════════════════

with tab_quarterly:
    st.header("Quarterly Report")
    st.caption("家庭全量资产负债表 · QoQ 净资产对比 · 财务比率")

    try:
        from quarterly_engine import (
            load_balance_sheet, available_quarters, compute_net_worth,
            compute_qoq, generate_balance_sheet_table, generate_waterfall_data,
            CATEGORY_DISPLAY,
        )
        bs_df = load_balance_sheet()
        quarters = available_quarters(bs_df)
    except Exception as e:
        st.error(f"加载 balance_sheet.csv 失败: {e}")
        st.stop()

    if not quarters:
        st.info("balance_sheet.csv 暂无数据")
    else:
        ctrl1, ctrl2 = st.columns(2)
        with ctrl1:
            q_curr = st.selectbox("当前季度", options=quarters[::-1], key='qr_curr')
        with ctrl2:
            prev_options = [q for q in quarters[::-1] if q < q_curr]
            q_prev = st.selectbox("对比季度",
                                  options=prev_options if prev_options else ['—'],
                                  key='qr_prev')
            if q_prev == '—':
                q_prev = None

        curr = compute_net_worth(bs_df, q_curr)

        # KPI
        st.subheader("核心指标")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("家庭净资产", f"¥{curr['net_worth']:,.0f}")
        k2.metric("总资产", f"¥{curr['total_assets']:,.0f}")
        k3.metric("总负债", f"¥{curr['total_liabilities']:,.0f}")
        k4.metric("资产负债率", f"{curr['debt_ratio']:.1f}%")

        k5, k6, k7, k8 = st.columns(4)
        k5.metric("金融投资占比", f"{curr['investment_ratio']:.1f}%")
        cr = curr['current_ratio']
        k6.metric("流动比率", f"{cr:.2f}" if cr else "—", help="流动资产/流动负债")
        k7.metric("季度", q_curr)
        if q_prev:
            qoq = compute_qoq(bs_df, q_prev, q_curr)
            k8.metric("QoQ 净资产", f"¥{qoq['net_worth_delta']:+,.0f}",
                      delta=f"{qoq['net_worth_pct']:+.2f}%")

        st.divider()

        # 资产负债表
        st.subheader("资产负债表")
        asset_df, liab_df = generate_balance_sheet_table(bs_df, q_curr)
        tbl1, tbl2 = st.columns(2)
        with tbl1:
            st.markdown("**资产端**")
            st.dataframe(asset_df.drop(columns=['Notes']), use_container_width=True,
                         hide_index=True,
                         column_config={'金额(CNY)': st.column_config.NumberColumn(format="¥%.0f")})
            st.markdown(f"**总资产: ¥{curr['total_assets']:,.0f}**")
        with tbl2:
            st.markdown("**负债端**")
            st.dataframe(liab_df.drop(columns=['Notes']), use_container_width=True,
                         hide_index=True,
                         column_config={'金额(CNY)': st.column_config.NumberColumn(format="¥%.0f")})
            st.markdown(f"**总负债: ¥{curr['total_liabilities']:,.0f}**")
            st.markdown(f"**净资产: ¥{curr['net_worth']:,.0f}**")

        st.divider()

        # 瀑布图 + QoQ 对比
        if q_prev:
            st.subheader(f"净资产变化 ({q_prev} → {q_curr})")
            waterfall = generate_waterfall_data(qoq)
            fig_wf = go.Figure(go.Waterfall(
                orientation='v',
                measure=[d['type'] for d in waterfall],
                x=[d['label'] for d in waterfall],
                y=[d['value'] for d in waterfall],
                connector={'line': {'color': '#888'}},
                increasing={'marker': {'color': '#2e7d32'}},
                decreasing={'marker': {'color': '#d32f2f'}},
                totals={'marker': {'color': '#1565c0'}},
                texttemplate='¥%{y:,.0f}',
                textposition='outside',
            ))
            fig_wf.update_layout(height=420, margin=dict(t=40, b=60), yaxis_title='金额 (¥)')
            st.plotly_chart(fig_wf, use_container_width=True)

            st.subheader("资产结构对比")
            prev_nw = compute_net_worth(bs_df, q_prev)
            compare_rows = []
            all_cats = sorted(set(list(curr['asset_breakdown']) + list(prev_nw['asset_breakdown'])))
            for cat in all_cats:
                compare_rows.append({
                    '大类':  CATEGORY_DISPLAY.get(cat, cat),
                    q_prev:  prev_nw['asset_breakdown'].get(cat, 0),
                    q_curr:  curr['asset_breakdown'].get(cat, 0),
                    '变化':  curr['asset_breakdown'].get(cat, 0) - prev_nw['asset_breakdown'].get(cat, 0),
                })
            compare_df = pd.DataFrame(compare_rows)
            fig_cmp = go.Figure()
            fig_cmp.add_trace(go.Bar(name=q_prev, x=compare_df['大类'], y=compare_df[q_prev],
                                     marker_color='#90CAF9'))
            fig_cmp.add_trace(go.Bar(name=q_curr, x=compare_df['大类'], y=compare_df[q_curr],
                                     marker_color='#1565c0'))
            fig_cmp.update_layout(barmode='group', height=360,
                                   yaxis_title='金额 (¥)', margin=dict(t=40, b=40))
            st.plotly_chart(fig_cmp, use_container_width=True)

        st.caption("⚠️ 房产/车辆为估算公允价值，非精确市场价。损益表分解从 2026Q2 起可用（需 portfolio.csv 投资收益数据）。")

        # ─── PDF 导出 ────────────────────────────────────────
        st.divider()

        def _generate_quarterly_html(q_curr, curr, q_prev, qoq, asset_df, liab_df, compare_df=None):
            """季度财报 HTML 报告生成。"""
            import plotly.graph_objects as go
            import plotly.io as pio

            # ── KPI ──
            net_worth   = curr['net_worth']
            total_assets = curr['total_assets']
            total_liab   = curr['total_liabilities']
            qoq_nw = qoq['net_worth_delta'] if qoq else None
            qoq_pct = qoq['net_worth_pct'] if qoq else None

            kpi_html = f"""
            <div class="kpi-row">
              <div class="kpi"><div class="kpi-label">净资产</div>
                <div class="kpi-value">¥{net_worth:,.0f}</div>
                {'<div class="kpi-delta ' + ('pos' if qoq_nw>=0 else 'neg') + '">' + ('+' if qoq_nw>=0 else '') + f'¥{qoq_nw:,.0f} ({qoq_pct:+.1f}%)</div>' if qoq_nw is not None else ''}</div>
              <div class="kpi"><div class="kpi-label">总资产</div><div class="kpi-value">¥{total_assets:,.0f}</div></div>
              <div class="kpi"><div class="kpi-label">总负债</div><div class="kpi-value">¥{total_liab:,.0f}</div></div>
              <div class="kpi"><div class="kpi-label">资产负债率</div>
                <div class="kpi-value">{total_liab/total_assets*100:.1f}%</div></div>
            </div>"""

            # ── 资产负债表 ──
            def _df_to_html(df, title):
                rows = ''
                for _, row in df.iterrows():
                    vals = ''.join(f'<td>{v}</td>' for v in row.values)
                    cls = ' class="subtotal"' if '小计' in str(row.iloc[0]) else ''
                    rows += f'<tr{cls}>{vals}</tr>'
                cols = ''.join(f'<th>{c}</th>' for c in df.columns)
                return f'<h3>{title}</h3><table class="data-table"><thead><tr>{cols}</tr></thead><tbody>{rows}</tbody></table>'

            bs_html = _df_to_html(asset_df, '资产明细') + _df_to_html(liab_df, '负债明细')

            # ── 瀑布图（基于 qoq 数值）──
            waterfall_html = ''
            if qoq:
                nw_prev = qoq.get('net_worth_prev', 0)
                nw_curr = qoq.get('net_worth_curr', 0)
                # asset_delta / liability_delta 是按类别拆分的 dict，需要求和
                _ad = qoq.get('asset_delta', 0)
                _ld = qoq.get('liability_delta', 0)
                asset_d = sum(_ad.values()) if isinstance(_ad, dict) else _ad
                liab_d  = sum(_ld.values()) if isinstance(_ld, dict) else _ld
                fig_wf = go.Figure(go.Waterfall(
                    name='净资产变动',
                    orientation='v',
                    measure=['absolute', 'relative', 'relative', 'total'],
                    x=[q_prev, '资产变动', '负债变动', q_curr],
                    y=[nw_prev, asset_d, -liab_d, nw_curr],
                    text=[f'¥{v:,.0f}' for v in [nw_prev, asset_d, -liab_d, nw_curr]],
                    textposition='outside',
                    connector=dict(line=dict(color='#888')),
                ))
                fig_wf.update_layout(
                    title=f'净资产 QoQ 变动瀑布图（{q_prev} → {q_curr}）',
                    height=380, showlegend=False, margin=dict(t=50, b=40),
                )
                waterfall_html = f'<h2>净资产变动分析</h2>{pio.to_html(fig_wf, full_html=False, include_plotlyjs=False)}'

            # ── 资产结构对比（如果有 compare_df）──
            compare_html = ''
            if compare_df is not None and not compare_df.empty:
                try:
                    categories = compare_df.iloc[:, 0].tolist()
                    curr_vals  = compare_df.iloc[:, 1].tolist()
                    prev_vals  = compare_df.iloc[:, 2].tolist() if compare_df.shape[1] > 2 else None
                    fig_bar = go.Figure()
                    fig_bar.add_trace(go.Bar(name=q_curr, x=categories, y=curr_vals, marker_color='#1565c0'))
                    if prev_vals:
                        fig_bar.add_trace(go.Bar(name=q_prev, x=categories, y=prev_vals, marker_color='#90caf9'))
                    fig_bar.update_layout(
                        title='资产结构对比', barmode='group', height=350,
                        margin=dict(t=50, b=40),
                    )
                    compare_html = f'<h2>资产结构对比</h2>{pio.to_html(fig_bar, full_html=False, include_plotlyjs=False)}'
                except Exception:
                    pass

            # plotly JS
            import plotly
            plotly_js_path = os.path.join(os.path.dirname(plotly.__file__), 'package_data', 'plotly.min.js')
            if os.path.exists(plotly_js_path):
                with open(plotly_js_path) as f_js:
                    plotly_js = f'<script>{f_js.read()}</script>'
            else:
                plotly_js = '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>'

            html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>FamilyFund 季度财报 {q_curr}</title>
{plotly_js}
<style>
  body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
        max-width:1100px;margin:0 auto;padding:24px 40px;color:#333}}
  h1{{color:#1a237e;border-bottom:3px solid #1565c0;padding-bottom:10px}}
  h2{{color:#1565c0;margin-top:32px}}
  h3{{color:#444;margin-top:20px}}
  .kpi-row{{display:flex;gap:16px;margin:20px 0;flex-wrap:wrap}}
  .kpi{{background:#f5f5f5;border-radius:8px;padding:16px 20px;flex:1;min-width:160px}}
  .kpi-label{{font-size:12px;color:#888;margin-bottom:4px}}
  .kpi-value{{font-size:22px;font-weight:bold;color:#1a237e}}
  .kpi-delta.pos{{color:#2e7d32;font-size:13px}}
  .kpi-delta.neg{{color:#c62828;font-size:13px}}
  .data-table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 20px}}
  .data-table th{{background:#1565c0;color:white;padding:8px 10px;text-align:left}}
  .data-table td{{padding:6px 10px;border-bottom:1px solid #f0f0f0}}
  .data-table tr.subtotal td{{background:#e3f2fd;font-weight:bold}}
  .data-table tr:nth-child(even){{background:#fafafa}}
  .footer{{color:#aaa;font-size:12px;text-align:center;margin-top:40px;
            border-top:1px solid #eee;padding-top:16px}}
  @media print{{body{{padding:10px 20px}}h2{{page-break-before:always}}
    h2:first-of-type{{page-break-before:avoid}}}}
</style></head><body>
<h1>📊 FamilyFund 家庭季度财报　{q_curr}</h1>
{kpi_html}
<h2>资产负债表</h2>
{bs_html}
{waterfall_html}
{compare_html}
<div class="footer">FamilyFund　{q_curr}　仅供个人财务管理参考</div>
</body></html>"""
            return html

        col_export, _ = st.columns([1, 3])
        with col_export:
            try:
                _cd = compare_df if (q_prev and 'compare_df' in dir()) else None
                _qoq = qoq if q_prev else None
                html_bytes = _generate_quarterly_html(
                    q_curr, curr, q_prev, _qoq, asset_df, liab_df, _cd
                ).encode('utf-8')
                fname_html = f"FamilyFund_Quarterly_{q_curr}.html"
                st.download_button(
                    label="📄 下载季度报告 HTML",
                    data=html_bytes,
                    file_name=fname_html,
                    mime="text/html",
                    type="primary",
                )
                st.caption("用浏览器打开后可打印为 PDF")
            except Exception as e:
                st.error(f"报告生成失败: {e}")

# ═══════════════════════════════════════════════════════════
# Tab 7: Planning
# ═══════════════════════════════════════════════════════════

with tab_planning:
    st.header("Planning")
    st.caption("人生阶段支出规划 · 财务独立测算 · 储蓄率追踪")

    # ─── Section 7: 人生阶段规划 ───
    st.divider()
    st.subheader("人生阶段规划")

    from life_stages_engine import load_life_stages, compute_expense_curve, get_milestone_summary

    _ls_data = load_life_stages(os.path.dirname(csv_path))

    if _ls_data is None:
        st.info("未找到 life_stages.json，请在 iCloud 数据目录手动创建。")
    else:
        _SCENARIO_LABELS = {'pessimistic': '悲观', 'base': '基准', 'optimistic': '乐观'}
        _SCENARIO_COLORS = {
            'pessimistic': '#EF5350',
            'base':        '#42A5F5',
            'optimistic':  '#66BB6A',
        }
        _MS_COLORS = {
            'early_childhood':  '#FF8A65',
            'k12_education':    '#FFB300',
            'higher_education': '#AB47BC',
            'property':         '#26A69A',
            'retirement_self':  '#78909C',
        }
        _MS_NAMES = {
            'early_childhood':  '早期养育',
            'k12_education':    'K12教育',
            'higher_education': '高等教育',
            'property':         '置业',
            'retirement_self':  '退休',
        }

        # 全局情景切换
        _ls_scenario = st.radio(
            '情景', options=['base', 'pessimistic', 'optimistic'],
            format_func=lambda x: _SCENARIO_LABELS[x],
            horizontal=True, key='ls_scenario',
        )

        # 计算三种情景的曲线
        _curve    = compute_expense_curve(_ls_data, _ls_scenario)
        _curve_b  = compute_expense_curve(_ls_data, 'base')
        _curve_p  = compute_expense_curve(_ls_data, 'pessimistic')
        _curve_o  = compute_expense_curve(_ls_data, 'optimistic')

        # 堆叠柱状图（当前情景，展望40年）
        _cur_year = date.today().year
        _years_show = list(range(_cur_year, _cur_year + 41))
        _ms_ids = [ms['id'] for ms in _ls_data.get('milestones', []) if ms.get('enabled', True)]

        _fig_ls = go.Figure()
        for _ms_id in _ms_ids:
            _vals = [_curve.get(y, {}).get('components', {}).get(_ms_id, 0) / 10000 for y in _years_show]
            _fig_ls.add_trace(go.Bar(
                x=_years_show, y=_vals,
                name=_MS_NAMES.get(_ms_id, _ms_id),
                marker_color=_MS_COLORS.get(_ms_id, '#888'),
            ))
        _fig_ls.update_layout(
            barmode='stack', height=380,
            yaxis_title='年支出（万CNY）',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            margin=dict(t=40, b=40),
            hovermode='x unified',
        )
        st.plotly_chart(_fig_ls, use_container_width=True)

        # 各里程碑配置一览
        with st.expander("各里程碑配置", expanded=False):
            for ms in _ls_data.get('milestones', []):
                _mc1, _mc2, _mc3 = st.columns([2, 3, 2])
                with _mc1:
                    st.markdown(f"**{ms['name']}**")
                with _mc2:
                    st.caption(get_milestone_summary(_ls_data, _ls_scenario, ms['id']))
                with _mc3:
                    _sc_sel = ms.get('selected', 'base')
                    st.caption(f"当前档位：{_SCENARIO_LABELS.get(_sc_sel, _sc_sel)}")

        # 对 FI 的影响（三种情景对比）
        st.markdown("**对财务独立的影响**")
        from fi_engine import load_fi_config, compute_fi_target, compute_years_to_fi
        import math as _math_ls
        _fi_cfg_ls  = load_fi_config(os.path.dirname(csv_path))
        _cur_assets = float(fund_nav_df.iloc[-1]['Total_Value']) if not fund_nav_df.empty else 0.0
        _monthly_sav_ls = _fi_cfg_ls['monthly_income_cny'] * _fi_cfg_ls['monthly_savings_target_pct']

        _fi_rows = []
        for _sc, _sc_label in _SCENARIO_LABELS.items():
            _sc_curve = {'pessimistic': _curve_p, 'base': _curve_b, 'optimistic': _curve_o}[_sc]
            # 用当前年支出作为 FI 目标支出（含里程碑）
            _this_year_exp = _sc_curve.get(_cur_year, {}).get('total', 0)
            if _this_year_exp <= 0:
                _this_year_exp = _fi_cfg_ls['annual_expense_target_cny']
            _fi_tgt = compute_fi_target(_this_year_exp, _fi_cfg_ls['withdrawal_rate'])
            _yrs    = compute_years_to_fi(_cur_assets, _fi_tgt, _monthly_sav_ls, _fi_cfg_ls['expected_annual_return'])
            if _yrs == 0:
                _yr_str = '已达标'
            elif _yrs is None:
                _yr_str = '100年内不达标'
            else:
                _yr_str = f"{_cur_year + _math_ls.ceil(_yrs)}年（{_yrs:.1f}年后）"
            _fi_rows.append({
                '情景':         _sc_label,
                '当年总支出':   f"¥{_this_year_exp/10000:.1f}万",
                'FI目标资产':   f"¥{_fi_tgt/10000:.0f}万",
                '预计达标':     _yr_str,
            })
        st.dataframe(pd.DataFrame(_fi_rows), use_container_width=True, hide_index=True)
        st.caption("FI目标基于当年（含里程碑）总支出计算，随情景变化。里程碑金额已含通胀调整。")

    # ─── Section 8: 财务独立测算 + 储蓄率 ───
    st.divider()
    st.subheader("财务独立 & 储蓄率")

    from fi_engine import (
        load_fi_config, save_fi_config,
        compute_fi_target, compute_years_to_fi, fi_sensitivity,
        compute_monthly_savings, compute_savings_rate,
    )
    import math as _math

    _fi_cfg = load_fi_config(_data_dir if '_data_dir' in dir() else os.path.dirname(csv_path))
    _fi_data_dir = os.path.dirname(csv_path)

    # ── 配置区 ──
    with st.expander("⚙ 配置参数", expanded=False):
        _fc1, _fc2, _fc3 = st.columns(3)
        with _fc1:
            _fi_income   = st.number_input(
                '税后月收入（CNY）',
                value=int(_fi_cfg['monthly_income_cny']), min_value=0, step=1000, key='fi_income',
                help="广义总收入年化平摊：(固定工资×12 + 年终奖 + RSU年度归属市值) ÷ 12。用于计算实际储蓄率分母。",
            )
            _fi_expense  = st.number_input('年度生活支出目标（CNY）', value=int(_fi_cfg['annual_expense_target_cny']), min_value=0, step=10000, key='fi_expense')
        with _fc2:
            _fi_return   = st.number_input(
                '预期年化收益率（%）',
                value=float(_fi_cfg['expected_annual_return']*100),
                min_value=0.0, max_value=20.0, step=0.5, key='fi_return',
                help=(
                    "建议填**实际收益率**（名义收益率 - 你预期的长期通胀），而非名义收益率。\n\n"
                    "示例：若预期组合名义年化 7%，长期通胀 3%，则填 4%。\n\n"
                    "参考：中国 M2 长期增速约 8-10%，CPI 约 2-3%，资产收益率需高于 M2 增速才能跑赢货币稀释。"
                ),
            )
            _fi_withdraw = st.number_input(
                '安全提款率（%）',
                value=float(_fi_cfg['withdrawal_rate']*100),
                min_value=1.0, max_value=10.0, step=0.5, key='fi_withdraw',
                help=(
                    "4% 法则来自 Trinity Study（1998），基于美国股债60/40组合、30年退休期。\n\n"
                    "**对中国投资者的调整建议：**\n"
                    "- 退休期 >30 年（如40岁退休）→ 用 3% 或 3.5%\n"
                    "- M2 超发 / 人民币长期贬值风险 → 偏保守用 3%\n"
                    "- 有房租、养老金等其他收入来源 → 可适当放宽\n\n"
                    "实质含义：3% → 需33倍年支出；3.5% → 28倍；4% → 25倍。"
                ),
            )
        with _fc3:
            _fi_sav_tgt = st.number_input(
                '目标储蓄率（%）',
                value=float(_fi_cfg['monthly_savings_target_pct']*100),
                min_value=0.0, max_value=100.0, step=5.0, key='fi_sav_tgt',
                help="每月储蓄目标占月收入的比例。用于储蓄率柱状图的目标虚线，同时自动推算 FI 测算的月供（月收入 × 目标储蓄率）。",
            )
            # 自动计算，展示给用户确认
            _fi_monthly_sav_auto = int(_fi_income * _fi_sav_tgt / 100)
            st.metric('月储蓄额（自动）', f"¥{_fi_monthly_sav_auto:,}", help="= 税后月收入 × 目标储蓄率，用于 FI 测算")

        if st.button('💾 保存配置', key='fi_save'):
            save_fi_config(_fi_data_dir, {
                'monthly_income_cny':         _fi_income,
                'monthly_savings_target_pct': _fi_sav_tgt / 100,
                'annual_expense_target_cny':  _fi_expense,
                'withdrawal_rate':            _fi_withdraw / 100,
                'expected_annual_return':     _fi_return / 100,
            })
            st.success('已保存')
            st.rerun()

    # ── 财务独立测算 ──
    _fi_target      = compute_fi_target(_fi_cfg['annual_expense_target_cny'], _fi_cfg['withdrawal_rate'])
    _current_assets = float(fund_nav_df.iloc[-1]['Total_Value']) if not fund_nav_df.empty else 0.0
    _monthly_sav_fi = _fi_cfg['monthly_income_cny'] * _fi_cfg['monthly_savings_target_pct']

    # 其他资产 toggle
    _tog1, _tog2 = st.columns([1, 3])
    with _tog1:
        _show_other = st.toggle('含其他资产', value=False, key='fi_show_other',
                                help="叠加未纳入基金的资产（房产、公积金等）查看完整 FI 进度")
    with _tog2:
        _other_assets = st.number_input(
            '其他资产估值（CNY）', value=int(_fi_cfg.get('other_assets_cny', 0)),
            min_value=0, step=100000, key='fi_other_assets',
            label_visibility='visible',
        ) if _show_other else 0

    _total_assets   = _current_assets + (_other_assets if _show_other else 0)
    _fi_progress    = min(_current_assets / _fi_target, 1.0) if _fi_target > 0 else 0.0
    _fi_progress_total = min(_total_assets / _fi_target, 1.0) if _fi_target > 0 else 0.0
    _years          = compute_years_to_fi(_current_assets, _fi_target, _monthly_sav_fi, _fi_cfg['expected_annual_return'])
    _years_total    = compute_years_to_fi(_total_assets,   _fi_target, _monthly_sav_fi, _fi_cfg['expected_annual_return'])

    def _year_str(y):
        if y == 0:   return '已达标 🎉'
        if y is None: return '100年内不达标'
        return f"{date.today().year + _math.ceil(y)}年（{y:.1f}年后）"

    _kc1, _kc2, _kc3 = st.columns(3)
    with _kc1: st.metric('FI 目标资产', f"¥{_fi_target:,.0f}")
    with _kc2: st.metric('基金资产',    f"¥{_current_assets:,.0f}")
    with _kc3:
        if _show_other:
            st.metric('含其他资产合计', f"¥{_total_assets:,.0f}",
                      delta=f"+¥{_other_assets:,.0f} 其他资产")
        else:
            st.metric('完成进度', f"{_fi_progress*100:.1f}%")

    # 进度条
    st.caption("基金资产进度")
    st.progress(_fi_progress)
    if _show_other:
        st.caption(f"含其他资产进度（{_fi_progress_total*100:.1f}%）")
        st.progress(_fi_progress_total)

    _ya1, _ya2 = st.columns(2)
    with _ya1: st.metric('预计达标（仅基金）',   _year_str(_years))
    if _show_other:
        with _ya2: st.metric('预计达标（含其他）', _year_str(_years_total))

    st.caption(
        f"FI目标 = 年支出 ÷ 提款率 = ¥{_fi_cfg['annual_expense_target_cny']:,.0f} ÷ {_fi_cfg['withdrawal_rate']*100:.1f}% = ¥{_fi_target:,.0f}。"
        f" 预期收益率 {_fi_cfg['expected_annual_return']*100:.1f}% 为**实际收益率**（已扣通胀），"
        f"名义收益率请在此基础上加上你预期的长期通胀（参考：M2长期增速约8-10%，CPI约2-3%）。"
    )

    # 敏感性分析（以当前有效资产为基准）
    _sens = fi_sensitivity(_total_assets, _fi_target, _monthly_sav_fi, _fi_cfg['expected_annual_return'])
    _sens_rows = []
    for s in _sens:
        _sens_rows.append({
            '情景':     s['label'],
            '年化收益': f"{s['annual_return']*100:.1f}%",
            '月储蓄':   f"¥{s['monthly_savings']:,.0f}",
            '所需年数': f"{s['years']:.1f}年" if s['years'] is not None else '100年+',
            '达标年份': str(s['target_year']) if s['target_year'] else '—',
        })
    st.dataframe(pd.DataFrame(_sens_rows), use_container_width=True, hide_index=True)

    # ── 储蓄率追踪 ──
    st.divider()
    st.subheader("储蓄率追踪")

    _monthly_savings_map = compute_monthly_savings(raw_df)
    _savings_rate_map    = compute_savings_rate(_monthly_savings_map, _fi_cfg['monthly_income_cny'])
    _sav_target          = _fi_cfg['monthly_savings_target_pct']

    if _savings_rate_map:
        _months = sorted(_savings_rate_map.keys())[-12:]  # 最近12个月
        _rates  = [_savings_rate_map[m] * 100 for m in _months]
        _amounts = [_monthly_savings_map.get(m, 0) for m in _months]
        _avg_rate = sum(_rates) / len(_rates) if _rates else 0

        _sc1, _sc2, _sc3 = st.columns(3)
        with _sc1: st.metric('滚动平均储蓄率', f"{_avg_rate:.1f}%")
        with _sc2: st.metric('目标储蓄率',     f"{_sav_target*100:.0f}%")
        with _sc3: st.metric('达标月数',       f"{sum(1 for r in _rates if r >= _sav_target*100)}/{len(_rates)} 个月")

        # 柱状图
        _bar_colors = ['#2e7d32' if r >= _sav_target * 100 else '#d32f2f' for r in _rates]
        _fig_sav = go.Figure()
        _fig_sav.add_trace(go.Bar(
            x=_months, y=_rates,
            marker_color=_bar_colors,
            name='储蓄率',
            text=[f'¥{a:,.0f}' for a in _amounts],
            textposition='outside',
        ))
        _fig_sav.add_hline(
            y=_sav_target * 100,
            line_dash='dash', line_color='#FFA726',
            annotation_text=f'目标 {_sav_target*100:.0f}%',
        )
        _fig_sav.update_layout(
            height=320, yaxis_title='储蓄率 (%)',
            yaxis=dict(range=[0, max(max(_rates) * 1.2, _sav_target * 120)]),
            margin=dict(t=30, b=40),
        )
        st.plotly_chart(_fig_sav, use_container_width=True)
        st.caption("储蓄率 = Cash 行正 NCF / 税后月收入。绿色 = 达标，红色 = 未达标。ESPP/RSU 不计入。")
    else:
        st.info("暂无 Cash 入金记录，储蓄率将在首次外部入金后自动计算。")


# ═══════════════════════════════════════════════════════════
# Tab 8: 10th Man
# ═══════════════════════════════════════════════════════════

with tab_tenth:
    from tenth_man import run_tenth_man
    from nav_engine import CLASS_DISPLAY_NAMES, VALID_ASSET_CLASSES

    st.header("10th Man System")
    st.caption("Pre-trade decision review. Three independent agents challenge your thesis from different angles: value trap / macro stress / liquidity.")

    _tm_data_dir = os.path.dirname(csv_path)

    # ── Provider 选择（动态扫描 tenth_man_config_*.json）─────────
    import glob as _glob
    def _load_providers(data_dir: str) -> dict:
        """扫描 data_dir 下所有 tenth_man_config_*.json，返回 {显示名: 文件名}。"""
        result = {}
        for path in sorted(_glob.glob(os.path.join(data_dir, 'tenth_man_config_*.json'))):
            fname = os.path.basename(path)
            try:
                with open(path, encoding='utf-8') as _f:
                    _cfg = json.load(_f)
                provider = _cfg.get('provider', fname)
                model    = _cfg.get('model', '?')
                note = '（家用网络）' if provider == 'deepseek' else ''
                label = f"{model}  ·  {provider}{note}"
            except Exception:
                label = fname
            result[label] = fname
        return result

    _PROVIDERS = _load_providers(_tm_data_dir)

    if not _PROVIDERS:
        st.warning("No tenth_man_config_*.json found in data directory.")
    else:
        _tm_provider_label = st.selectbox(
            'LLM Provider',
            options=list(_PROVIDERS.keys()),
            key='tm_provider',
            help='自动读取 iCloud data 目录下的 tenth_man_config_*.json。新增 provider 只需放入对应 json 文件。',
        )
        _tm_config_file = _PROVIDERS[_tm_provider_label]
        _tm_config_ok = os.path.exists(os.path.join(_tm_data_dir, _tm_config_file))

    if not _PROVIDERS or not _tm_config_ok:
        st.warning(f"{_tm_config_file} not found. Please create it in your data directory.")
    else:
        # ── 决策输入区 ──
        st.subheader("Decision Input")
        st.caption("**Core Logic and Macro Assumption are required.** The agents will specifically attack what you write here — the more specific, the sharper the review.")

        # 持仓快速填入
        latest_holdings_tm = raw_df[raw_df['Date'] == raw_df['Date'].max()]
        holding_options = ['（Manual input）'] + [
            f"{row['Name']} ({row['Code']})"
            for _, row in latest_holdings_tm.iterrows()
            if row['Asset_Class'] not in ('Cash',)
        ]

        # 用 session_state 驱动自动填入
        prev_selection = st.session_state.get('tm_prev_selection', '')
        selected_holding = st.selectbox("Quick-fill from holdings (optional)", holding_options, key='tm_holding_select')

        if selected_holding != prev_selection:
            st.session_state['tm_prev_selection'] = selected_holding
            if selected_holding != '（Manual input）':
                _parts = selected_holding.rsplit('(', 1)
                _auto_name = _parts[0].strip()
                _auto_code = _parts[1].rstrip(')') if len(_parts) > 1 else ''
                _match = latest_holdings_tm[latest_holdings_tm['Name'] == _auto_name]
                _auto_class = _match.iloc[0]['Asset_Class'] if not _match.empty else ''
                st.session_state['tm_name'] = _auto_name
                st.session_state['tm_symbol'] = _auto_code
                st.session_state['tm_auto_class'] = _auto_class
            else:
                st.session_state['tm_name'] = ''
                st.session_state['tm_symbol'] = ''
                st.session_state['tm_auto_class'] = ''

        _tm_auto_class = st.session_state.get('tm_auto_class', '')

        col_a, col_b, col_c = st.columns([3, 3, 2])
        with col_a:
            tm_name = st.text_input('Asset Name', key='tm_name')
        with col_b:
            tm_symbol = st.text_input(
                'YF Symbol',
                key='tm_symbol',
                help='Shanghai A: 601838.SS  Shenzhen A: 000001.SZ  HK: 0700.HK  US: NVDA',
            )
        with col_c:
            tm_direction = st.selectbox('Direction', ['Buy', 'Sell'], key='tm_direction')

        col_d, col_e = st.columns([2, 3])
        with col_d:
            tm_amount = st.number_input('Amount (CNY)', min_value=0, step=1000, value=20000, key='tm_amount')
        with col_e:
            _class_options = ['（Not selected）'] + sorted(c for c in VALID_ASSET_CLASSES if c != 'Cash')
            _class_index = _class_options.index(_tm_auto_class) if _tm_auto_class in _class_options else 0
            tm_class = st.selectbox(
                'Asset Class (for post-trade position estimate)',
                _class_options,
                index=_class_index,
                key='tm_class',
            )

        tm_logic = st.text_area(
            'Core Logic (required)',
            placeholder='e.g. Forward PE only 5x, dividend yield 4.75%, NPL ratio declining',
            key='tm_logic', height=80,
        )
        tm_macro = st.text_area(
            'Macro Assumption (optional)',
            placeholder='e.g. Interest rates stay low, Chengdu economy continues growing',
            key='tm_macro', height=80,
        )

        st.caption("💡 Estimated cost per review: GLM-5.1 ≈ ¥0.10-0.15 | DeepSeek-Reasoner ≈ ¥0.05-0.10 | MiniMax-Text-01 ≈ ¥0.05-0.10（3 independent calls）")

        if st.button("🔍 Launch 10th Man Review", type="primary", key='tm_run'):
            if not tm_name or not tm_symbol:
                st.warning("Please fill in Asset Name and YF Symbol")
            elif not tm_logic:
                st.warning("Please fill in Core Logic — the agents need your thesis to attack it")
            else:
                decision = {
                    'asset_name':       tm_name,
                    'yf_symbol':        tm_symbol,
                    'asset_class':      tm_class if tm_class != '（Not selected）' else '',
                    'direction':       tm_direction,
                    'amount_cny':      tm_amount,
                    'core_logic':      tm_logic,
                    'macro_assumption': tm_macro or '（未填写）',
                }
                with st.spinner("第十人审查中（约 30-60 秒）..."):
                    _tm_mkt = get_market_data()
                    result = run_tenth_man(
                        decision, raw_df, fund_nav_df, allocation_df,
                        _tm_mkt, _tm_data_dir,
                        config_file=_tm_config_file,
                    )
                st.session_state['tm_result'] = result
                st.session_state['tm_decision'] = decision

        # ── 审查报告区 ──
        if 'tm_result' in st.session_state:
            result = st.session_state['tm_result']
            decision = st.session_state['tm_decision']

            if result.get('error'):
                st.error(result['error'])
            else:
                st.divider()
                st.subheader(f"审查报告：{decision['asset_name']} {decision['direction']} ¥{decision['amount_cny']:,.0f}")

                _is_buy = decision.get('direction') in ('Buy', '买入')
                _label_a = "🔍 Agent A：价值陷阱审问官" if _is_buy else "🔍 Agent A：逆向价值辩护律师"
                _label_b = "🌪 Agent B：宏观压测机" if _is_buy else "🌪 Agent B：宏观压测机（持有风险）"
                _label_c = "💧 Agent C：集中度/流动性审计员"

                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.markdown(f"### {_label_a}")
                    st.markdown(result['agent_a'])
                with col_b:
                    st.markdown(f"### {_label_b}")
                    st.markdown(result['agent_b'])
                with col_c:
                    st.markdown(f"### {_label_c}")
                    st.markdown(result['agent_c'])

                # HTML 报告导出
                st.divider()
                if st.button("📄 导出审查报告 HTML", key='tm_pdf'):
                    try:
                        import re

                        def _md_to_html(text: str) -> str:
                            """简单 Markdown → HTML 转换（不依赖外部库）。"""
                            lines = []
                            for line in text.split('\n'):
                                line = re.sub(r'^### (.+)', r'<h4>\1</h4>', line)
                                line = re.sub(r'^## (.+)',  r'<h3>\1</h3>', line)
                                line = re.sub(r'^# (.+)',   r'<h2>\1</h2>', line)
                                line = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
                                line = re.sub(r'\*(.+?)\*',     r'<i>\1</i>', line)
                                line = re.sub(r'^- (.+)',   r'<li>\1</li>', line)
                                lines.append(line)
                            html = '\n'.join(lines)
                            html = re.sub(r'(<li>.*?</li>\n?)+',
                                          lambda m: f'<ul>{m.group(0)}</ul>', html, flags=re.DOTALL)
                            html = html.replace('\n', '<br>')
                            return html

                        _a_title = "Agent A：价值陷阱审问官" if _is_buy else "Agent A：逆向价值辩护律师"
                        _b_title = "Agent B：宏观压测机" if _is_buy else "Agent B：宏观压测机（持有风险）"
                        _c_title = "Agent C：流动性与集中度审计员"
                        _direction = "买入" if _is_buy else "卖出"

                        sections = [
                            ("决策摘要 & 持仓上下文", result['context']),
                            (_a_title, result['agent_a']),
                            (_b_title, result['agent_b']),
                            (_c_title, result['agent_c']),
                        ]

                        sections_html = ''
                        colors = ['#1565c0','#c62828','#e65100','#2e7d32']
                        for i, (title, text) in enumerate(sections):
                            sections_html += f'''
                            <div class="section" style="border-left:4px solid {colors[i]}">
                                <h2 style="color:{colors[i]}">{title}</h2>
                                <div class="content">{_md_to_html(text)}</div>
                            </div>'''

                        html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>第十人审查报告 - {decision["asset_name"]}</title>
<style>
  body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
        max-width:900px;margin:0 auto;padding:30px 40px;color:#333}}
  h1{{color:#1a1a2e;border-bottom:3px solid #333;padding-bottom:10px}}
  .meta{{background:#f5f5f5;border-radius:8px;padding:14px;margin:16px 0;font-size:14px}}
  .meta span{{margin-right:20px}}
  .section{{background:#fafafa;border-radius:0 8px 8px 0;
             padding:20px 24px;margin:20px 0;page-break-inside:avoid}}
  .section h2{{margin-top:0;font-size:16px}}
  .content{{font-size:14px;line-height:1.8}}
  .content h3,.content h4{{color:#444;margin:12px 0 6px}}
  .content ul{{padding-left:20px}}
  .footer{{color:#aaa;font-size:12px;text-align:center;margin-top:40px;
            border-top:1px solid #eee;padding-top:16px}}
  @media print{{body{{padding:10px 20px}}.section{{page-break-inside:avoid}}}}
</style></head><body>
<h1>🔍 第十人审查报告</h1>
<div class="meta">
  <span>📅 {date.today().isoformat()}</span>
  <span>🎯 标的：{decision["asset_name"]} ({decision.get("code","")})</span>
  <span>📌 方向：{_direction}</span>
  <span>💰 金额：¥{decision.get("amount_cny",0):,.0f}</span>
</div>
{sections_html}
<div class="footer">FamilyFund 第十人系统　{date.today().isoformat()}　仅供个人投资决策参考</div>
</body></html>'''

                        reports_dir = os.path.join(_tm_data_dir, 'tenth_man_reports')
                        os.makedirs(reports_dir, exist_ok=True)
                        fname = f"{date.today().isoformat()}_{decision['asset_name']}.html"
                        fpath = os.path.join(reports_dir, fname)
                        with open(fpath, 'w', encoding='utf-8') as f:
                            f.write(html)

                        st.download_button(
                            label=f"⬇ 下载 {fname}",
                            data=html.encode('utf-8'),
                            file_name=fname,
                            mime='text/html',
                        )
                        st.success(f"已保存至 {fpath}　用浏览器打开可打印为 PDF")
                    except Exception as e:
                        st.error(f"报告生成失败：{e}")
