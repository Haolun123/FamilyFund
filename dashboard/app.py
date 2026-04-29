import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import os
import sys
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
from pdf_report import generate_report as generate_pdf_report
from benchmark import get_benchmarks, BENCHMARK_DISPLAY_NAMES, BENCHMARK_COLORS
from fundamentals import (
    load_yf_symbols, save_yf_symbols, get_all_fundamentals,
    add_yf_symbol, remove_yf_symbol,
)
from market_monitor import (
    get_market_data, set_pe_override,
    compute_bias, compute_vix_signal, compute_pe_signal, compute_qvix_signal,
    lookup_multiplier, lookup_a_share_multiplier, lookup_gold_multiplier,
    SP500_PE_BANDS, SP500_VIX_BANDS, SP500_MATRIX,
    NDX100_PE_BANDS, NDX100_VIX_BANDS, NDX100_MATRIX,
    CSI300_PE_BANDS, CSI300_QVIX_BANDS, CSI300_MATRIX,
    CSI_A500_PE_BANDS, CSI_A500_QVIX_BANDS, CSI_A500_MATRIX,
    GOLD_BIAS_BANDS, GOLD_VIX_BANDS, GOLD_MATRIX,
    TARGETS,
)
from backtest import run_backtest

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

# ─── Sidebar ───

st.sidebar.title("📊 FamilyFund")
st.sidebar.caption("家庭基金管理仪表板")
st.sidebar.divider()

# Data source info
data_name = os.path.basename(csv_path)
dates = sorted(raw_df['Date'].unique())
st.sidebar.markdown(f"**数据源**: `{data_name}`")
st.sidebar.markdown(f"**周期数**: {len(dates)}")

# PDF export
st.sidebar.divider()
st.sidebar.subheader("导出")
_pdf_bytes = generate_pdf_report(raw_df, fund_nav_df, class_nav_dict, allocation_df, cost_basis_df)
_latest_date = fund_nav_df.iloc[-1]['Date']
st.sidebar.download_button(
    label="Download PDF Report",
    data=_pdf_bytes,
    file_name=f"FamilyFund_{_latest_date}.pdf",
    mime="application/pdf",
)

# Date range filter
st.sidebar.subheader("日期范围")
date_options = dates
if len(date_options) > 1:
    start_idx, end_idx = st.sidebar.select_slider(
        "选择日期范围",
        options=list(range(len(date_options))),
        value=(0, len(date_options) - 1),
        format_func=lambda i: date_options[i],
        label_visibility="collapsed",
    )
    date_start = date_options[start_idx]
    date_end = date_options[end_idx]
else:
    date_start = date_end = date_options[0]

# Asset class filter
st.sidebar.subheader("资产类别")
# Asset class filter（Cash 作为流动性储备，不参与分类对比）
all_classes = sorted(c for c in raw_df['Asset_Class'].unique() if c != 'Cash')
display_map = {cls: CLASS_DISPLAY_NAMES.get(cls, cls) for cls in raw_df['Asset_Class'].unique()}

select_all = st.sidebar.checkbox("全选", value=True)
if select_all:
    selected_classes = all_classes
else:
    selected_classes = st.sidebar.multiselect(
        "选择类别",
        options=all_classes,
        default=all_classes,
        format_func=lambda c: display_map[c],
        label_visibility="collapsed",
    )

# Benchmark overlay
st.sidebar.divider()
st.sidebar.subheader("基准对比")
selected_benchmarks = []
for bkey, bname in BENCHMARK_DISPLAY_NAMES.items():
    if st.sidebar.checkbox(bname, value=False, key=f"bm_{bkey}"):
        selected_benchmarks.append(bkey)

# Load benchmark data (cached, with iCloud fallback)
fund_start_date = raw_df['Date'].min()
benchmark_data = get_benchmarks(fund_start_date) if selected_benchmarks else {}

# Filter data by date range
filtered_raw = raw_df[(raw_df['Date'] >= date_start) & (raw_df['Date'] <= date_end)]
filtered_fund = fund_nav_df[(fund_nav_df['Date'] >= date_start) & (fund_nav_df['Date'] <= date_end)]

# ─── Global session_state initialization ───
# Must run before any tab widgets to prevent widget value= overriding cache
if 'sap_price_initialized' not in st.session_state:
    _sap_cache = load_sap_price_cache()
    if _sap_cache:
        st.session_state['sap_current_price'] = _sap_cache['price_eur']
        st.session_state['sap_fx_rate'] = _sap_cache['fx_rate']
    st.session_state['sap_price_initialized'] = True

# ─── Tabs ───

tab_dashboard, tab_update, tab_history, tab_sap, tab_market, tab_backtest, tab_quarterly = st.tabs(
    ["Dashboard", "Weekly Update", "History", "SAP Stock", "Market Monitor", "Backtest", "Quarterly Report"]
)

# ═══════════════════════════════════════════════════════════
# Tab 1: Dashboard
# ═══════════════════════════════════════════════════════════

with tab_dashboard:

    # 加载目标配置比例（不缓存，用户可能刚修改过）
    target_alloc = load_target_allocation(os.path.dirname(csv_path))

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
    for cls in selected_classes:
        if cls in class_nav_dict:
            nav_df = class_nav_dict[cls]
            filtered_cls = nav_df[(nav_df['Date'] >= date_start) & (nav_df['Date'] <= date_end)]
            if len(filtered_cls) > 0:
                latest = filtered_cls.iloc[-1]
                alloc_row = allocation_df[allocation_df['Asset_Class'] == cls]
                alloc_pct = alloc_row['Allocation_Percent'].values[0] * 100 if len(alloc_row) > 0 else 0
                perf_rows.append({
                    '资产类别': display_map[cls],
                    '净值': f"{latest['NAV']:.4f}",
                    '收益率': f"{latest['Cumulative_Return(%)']:+.2f}%",
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
        'US_Growth_Fund': lookup_multiplier(_rb_pe_ndx, _rb_vix, 'ndx100'),
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
                st.markdown(f"**{type_labels.get(entry['type'], entry['type'])}**")
            with c2:
                if entry['type'] in ('买入', '卖出'):
                    # asset_label = "Name (Code)" displayed; asset_name = Name stored internally
                    current_label = entry.get('asset_label', '')
                    selected_label = st.selectbox(
                        "资产", options=[''] + asset_options_labels,
                        index=([''] + asset_options_labels).index(current_label)
                              if current_label in ([''] + asset_options_labels) else 0,
                        key=f"rb_asset_{idx}", label_visibility="collapsed",
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

# ═══════════════════════════════════════════════════════════
# Tab 3: History Management
# ═══════════════════════════════════════════════════════════

with tab_history:

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

# ═══════════════════════════════════════════════════════════
# Tab 4: SAP Stock
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
# Tab 5: 市场温度计
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
    qvix_entry      = market_data.get('qvix')
    pe_sp_entry     = market_data.get('pe_sp500')
    pe_ndx_entry    = market_data.get('pe_ndx100')
    treasury_entry  = market_data.get('treasury_10y')

    vix_val       = vix_entry.get('price')      if vix_entry      else None
    qvix_val      = qvix_entry.get('price')     if qvix_entry     else None
    pe_sp         = (pe_sp_entry.get('manual_override') or pe_sp_entry.get('value'))   if pe_sp_entry  else None
    pe_ndx        = (pe_ndx_entry.get('manual_override') or pe_ndx_entry.get('value')) if pe_ndx_entry else None
    treasury_val  = treasury_entry.get('price') if treasury_entry else None
    sp_src        = ('手动' if (pe_sp_entry or {}).get('manual_override') else 'VOO auto') if pe_sp_entry else '—'
    ndx_src       = ('手动' if (pe_ndx_entry or {}).get('manual_override') else 'QQQ auto') if pe_ndx_entry else '—'

    vix_label,   vix_emoji   = compute_vix_signal(vix_val)
    qvix_label,  qvix_emoji  = compute_qvix_signal(qvix_val)
    sp_pe_label,  sp_pe_emoji  = compute_pe_signal(pe_sp,  'sp500')
    ndx_pe_label, ndx_pe_emoji = compute_pe_signal(pe_ndx, 'ndx100')

    # 美债收益率信号（仅展示，不参与矩阵）
    if treasury_val is None:
        treasury_label, treasury_emoji = '数据不可用', '❓'
    elif treasury_val >= 4.5:
        treasury_label, treasury_emoji = '偏高（压估值）', '🔴'
    elif treasury_val >= 3.5:
        treasury_label, treasury_emoji = '中性', '🟡'
    else:
        treasury_label, treasury_emoji = '偏低（宽松）', '🟢'

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    with kpi1:
        st.metric("VIX 恐慌指数", f"{vix_val:.1f}" if vix_val else "—")
        st.markdown(f"{vix_emoji} **{vix_label}**")
        st.caption(f"美股恐慌指数　更新: {meta.get('vix_updated', '未知')}")
    with kpi2:
        st.metric("QVIX A股波动率", f"{qvix_val:.1f}" if qvix_val else "—")
        st.markdown(f"{qvix_emoji} **{qvix_label}**")
        st.caption(f"300ETF期权隐含波动率　更新: {meta.get('qvix_updated', '未知')}")
    with kpi3:
        st.metric("标普500 PE", f"{pe_sp:.1f}" if pe_sp else "—")
        st.markdown(f"{sp_pe_emoji} **{sp_pe_label}**")
        st.caption(f"来源: {sp_src}　更新: {meta.get('pe_sp500_updated', '未知')}")
    with kpi4:
        st.metric("纳指100 PE", f"{pe_ndx:.1f}" if pe_ndx else "—")
        st.markdown(f"{ndx_pe_emoji} **{ndx_pe_label}**")
        st.caption(f"来源: {ndx_src}　更新: {meta.get('pe_ndx100_updated', '未知')}")
    with kpi5:
        st.metric("美债10Y收益率", f"{treasury_val:.2f}%" if treasury_val else "—")
        st.markdown(f"{treasury_emoji} **{treasury_label}**")
        st.caption(f"仅供参考，不参与矩阵计算　更新: {meta.get('treasury_10y_updated', '未知')}")

    # PE 手动覆盖（折叠）
    with st.expander("PE 手动覆盖（网络不可达时使用）", expanded=False):
        st.caption("填入后点击应用，系统将使用手动值直到清除。留空表示清除手动值，恢复自动获取。")
        ov_col1, ov_col2, ov_col3 = st.columns([2, 2, 1])
        with ov_col1:
            sp_ov = st.number_input(
                "标普PE (VOO)",
                value=float(pe_sp) if pe_sp else 0.0,
                min_value=0.0, step=0.1, format="%.1f",
                key="pe_sp_override",
            )
        with ov_col2:
            ndx_ov = st.number_input(
                "纳指PE (QQQ)",
                value=float(pe_ndx) if pe_ndx else 0.0,
                min_value=0.0, step=0.1, format="%.1f",
                key="pe_ndx_override",
            )
        with ov_col3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("应用", key="pe_override_apply"):
                set_pe_override('sp500',  sp_ov  if sp_ov  > 0 else None)
                set_pe_override('ndx100', ndx_ov if ndx_ov > 0 else None)
                st.success("已保存 PE 手动值")
                st.rerun()
            if st.button("清除手动值", key="pe_override_clear"):
                set_pe_override('sp500',  None)
                set_pe_override('ndx100', None)
                st.success("已清除，将恢复自动获取")
                st.rerun()

    st.divider()

    # ─── Section 3: 定投倍数建议（标普 + 纳指） ───

    st.subheader("定投倍数建议")
    st.caption("基于 PE × VIX 矩阵，适用于标普500和纳指100。A股请参考下方A股矩阵，黄金请参考黄金矩阵。")

    mult_sp  = lookup_multiplier(pe_sp,  vix_val, 'sp500')
    mult_ndx = lookup_multiplier(pe_ndx, vix_val, 'ndx100')

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
        else:
            st.info("标普500: 数据不完整，无法计算")

    with mult_col2:
        color = _mult_color(mult_ndx)
        if pe_ndx and vix_val:
            st.markdown(
                f"<div style='border:1px solid #ddd; border-radius:8px; padding:16px; text-align:center;'>"
                f"<div style='font-size:14px; color:#666; margin-bottom:8px;'>纳指100</div>"
                f"<div style='font-size:13px; color:#999;'>PE {pe_ndx:.1f} × VIX {vix_val:.1f}</div>"
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
    ndx_col_labels = ['VIX<18', 'VIX 18-24', 'VIX 24-31', 'VIX>31']
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
        ndx_col = _find_col(vix_val, NDX100_VIX_BANDS) if vix_val else None
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
    st.caption("⚠️ 仅供参考，不构成投资建议。数据来自公开市场，存在延迟。")

    # ─── Section: 个股基本面 ───

    st.divider()
    st.subheader("个股基本面")

    _data_dir = os.path.dirname(csv_path)
    _yf_map   = load_yf_symbols(_data_dir)
    _sym_map  = {k: v for k, v in _yf_map.items() if not k.startswith('_')}

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

                r1c1, r1c2, r1c3, r1c4 = st.columns(4)
                with r1c1: st.metric('PE (TTM)',    _fmt(f.get('trailingPE'), 'x'))
                with r1c2: st.metric('Forward PE',  _fmt(f.get('forwardPE'),  'x'))
                with r1c3: st.metric('PB',          _fmt(f.get('priceToBook'),'x'))
                with r1c4: st.metric('ROE',         _fmt(f.get('returnOnEquity'), '%'))

                r2c1, r2c2, r2c3, r2c4 = st.columns(4)
                with r2c1: st.metric('EPS (TTM)',   _fmt(f.get('trailingEps'), eps_fmt))
                with r2c2: st.metric('Forward EPS', _fmt(f.get('forwardEps'),  eps_fmt))
                with r2c3: st.metric('股息率',       _fmt(f.get('dividendYield'), 'pct_direct'))
                with r2c4: st.metric('营收增长 YoY', _fmt(f.get('revenueGrowth'), '+%'))

                if not f:
                    st.caption('⚠️ 数据暂不可用，请检查 YF Symbol 或网络连接')
    else:
        st.info('暂无持仓个股匹配 YF Symbol 映射。请在下方添加映射后，确认持仓中有对应 Code。')

    # YF Symbol 管理
    st.divider()
    with st.expander("管理个股 YF Symbol 映射", expanded=False):
        st.caption("""**Symbol 填写规则：**
- A股上海（6开头）：`601838` → `601838.SS`
- A股深圳（0/3开头）：`000001` → `000001.SZ`
- 港股（去掉前导零）：`HK0700` → `0700.HK`，`01810` → `1810.HK`
- 美股：直接用 ticker（`NVDA`、`AAPL`）
- 欧股 ADR（在美上市）：`SAP.DE` → `SAP`""")

        if _sym_map:
            st.markdown("**当前映射：**")
            for code, sym in _sym_map.items():
                mc1, mc2, mc3 = st.columns([3, 3, 1])
                with mc1: st.code(code)
                with mc2: st.code(sym)
                with mc3:
                    if st.button('🗑️', key=f'del_{code}', help=f'删除 {code}'):
                        remove_yf_symbol(_data_dir, code)
                        st.rerun()
        else:
            st.info('暂无映射，请添加。')

        st.markdown("**新增映射：**")
        add_c1, add_c2, add_c3 = st.columns([3, 3, 2])
        with add_c1:
            new_code = st.text_input('portfolio.csv Code', key='new_yf_code',
                                     placeholder='如 601838')
        with add_c2:
            new_sym = st.text_input('YF Symbol', key='new_yf_sym',
                                    placeholder='如 601838.SS')
        with add_c3:
            st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
            if st.button('➕ 添加', key='add_yf'):
                if new_code.strip() and new_sym.strip():
                    add_yf_symbol(_data_dir, new_code.strip(), new_sym.strip())
                    st.success(f'已添加 {new_code} → {new_sym}')
                    st.rerun()
                else:
                    st.warning('Code 和 Symbol 均不能为空')

# ═══════════════════════════════════════════════════════════
# Tab 7: 回测
# ═══════════════════════════════════════════════════════════

with tab_backtest:
    st.header("定投策略回测")
    st.caption(
        "对比「固定金额定投」与「PE×VIX/QVIX 矩阵策略定投」的历史表现。"
        "相同起始日期、相同基准金额，验证市场温度计矩阵是否真正有效。"
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
        'csi300': _date(2015, 1, 1), 'csi_a500': _date(2015, 1, 1),
        'sp500':  _date(2000, 1, 1), 'ndx100':   _date(2000, 1, 1),
        'gold':   _date(2000, 1, 1),
    }
    _MIN_DATES = {
        'csi300': _date(2015, 1, 1), 'csi_a500': _date(2015, 1, 1),
        'sp500':  _date(1990, 1, 1), 'ndx100':   _date(1990, 1, 1),
        'gold':   _date(1990, 1, 1),
    }
    _CURRENCY = {
        'csi300': '¥', 'csi_a500': '¥',
        'sp500': '$', 'ndx100': '$', 'gold': '$',
    }

    # ─── Controls ───────────────────────────────────────────
    bt_col1, bt_col2, bt_col3 = st.columns(3)
    with bt_col1:
        bt_target = st.selectbox(
            "回测标的", options=list(_TARGET_NAMES.keys()),
            format_func=lambda t: _TARGET_NAMES[t], key='bt_target',
        )
    with bt_col2:
        bt_start = st.date_input(
            "回测起始日期",
            value=_DEFAULT_STARTS.get(bt_target, _date(2015, 1, 1)),
            min_value=_MIN_DATES.get(bt_target, _date(2000, 1, 1)),
            max_value=_date.today() - timedelta(days=365),
            key='bt_start_date',
        )
    with bt_col3:
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

    if bt_target == 'ndx100':
        st.info(
            "**纳指100 回测说明**：纳指历史 PE 无免费数据源，回测使用标普500 PE（Shiller）作为代理信号。"
            "纳指实际 PE 历史上高于标普（科技溢价），代理信号在科技泡沫期（2000年、2021年）偏乐观。"
            "当前实时展示仍使用 QQQ 真实 PE，本说明仅适用于回测历史数据。"
        )

    st.divider()

    # ─── Run ────────────────────────────────────────────────
    if bt_run:
        top_mult = bt_top_gold if bt_target == 'gold' else bt_top_equity
        with st.spinner(f"正在拉取 {_TARGET_NAMES[bt_target]} 历史数据并运行回测..."):
            try:
                result = run_backtest(
                    target=bt_target,
                    start_date=bt_start.strftime('%Y-%m-%d'),
                    base_amount=bt_base_amount,
                    freq='W' if bt_freq == '周频' else 'M',
                    top_multiplier=top_mult,
                )
                st.session_state['bt_result'] = result
            except Exception as e:
                st.error(f"回测运行失败: {e}")

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

        # 定投次数单独展示（无超额概念）
        c6.markdown(
            f"<div style='border:1px solid #e0e0e0;border-radius:8px;padding:14px;'>"
            f"<div style='font-size:12px;color:#888;'>定投次数</div>"
            f"<div style='font-size:13px;color:#444;margin-top:4px;'>固定: {fixed['periods']} 期</div>"
            f"<div style='font-size:13px;color:#444;'>矩阵: {matrix['periods']} 期（暂停{fixed['periods']-matrix['periods']}期）</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

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
            show_df = history_df[[
                'date', 'price', 'pe_or_bias', 'vol', 'raw_mult',
                'matrix_amount', 'matrix_cum_cost', 'matrix_cum_value',
            ]].rename(columns={
                'date': '日期', 'price': '价格',
                'pe_or_bias': 'PE/乖离率', 'vol': 'VIX/QVIX',
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

# ═══════════════════════════════════════════════════════════
# Tab 7: Quarterly Report
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

        def _generate_quarterly_pdf(q_curr, curr, q_prev, qoq, asset_df, liab_df, compare_df=None):
            import io
            import matplotlib
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_pdf import PdfPages
            try:
                import mpl_fontkit as fk
                fk.install('NotoSansSC', verbose=False)
            except Exception:
                pass
            matplotlib.rcParams['font.sans-serif'] = [
                'Arial Unicode MS', 'Noto Sans CJK SC', 'Noto Sans SC',
                'PingFang SC', 'SimHei',
            ]
            matplotlib.rcParams['axes.unicode_minus'] = False

            PAGE = (11.69, 8.27)
            buf = io.BytesIO()

            def _style_table(tbl, fontsize=7):
                tbl.auto_set_font_size(False)
                tbl.set_fontsize(fontsize)
                tbl.scale(1, 1.4)
                for (row, col), cell in tbl.get_celld().items():
                    cell.set_edgecolor('#e0e0e0')
                    cell.PAD = 0.04
                    if row == 0:
                        cell.set_facecolor('#2196F3')
                        cell.set_text_props(color='white', fontweight='bold', fontsize=fontsize)
                    elif '小计' in str(cell.get_text().get_text()):
                        cell.set_facecolor('#e3f2fd')
                        cell.set_text_props(fontweight='bold')
                    elif row % 2 == 0:
                        cell.set_facecolor('#fafafa')

            with PdfPages(buf) as pdf:
                # ── Page 1: KPI + 资产负债表（左右分列）──
                fig = plt.figure(figsize=PAGE)
                fig.suptitle(f'FamilyFund 家庭财报  {q_curr}', fontsize=15,
                             fontweight='bold', color='#1a237e', y=0.98)

                # KPI 行（顶部 12% 高度）
                ax_kpi = fig.add_axes([0.02, 0.86, 0.96, 0.10])
                ax_kpi.axis('off')
                kpis = [
                    ('家庭净资产', f'¥{curr["net_worth"]:,.0f}'),
                    ('总资产',     f'¥{curr["total_assets"]:,.0f}'),
                    ('总负债',     f'¥{curr["total_liabilities"]:,.0f}'),
                    ('资产负债率', f'{curr["debt_ratio"]:.1f}%'),
                    ('金融投资占比', f'{curr["investment_ratio"]:.1f}%'),
                ]
                if q_prev and qoq:
                    kpis.append(('QoQ净资产', f'¥{qoq["net_worth_delta"]:+,.0f}'))
                n = len(kpis)
                for i, (label, val) in enumerate(kpis):
                    x = (i + 0.5) / n
                    ax_kpi.text(x, 0.75, val, ha='center', va='center',
                                fontsize=12, fontweight='bold', color='#1565c0',
                                transform=ax_kpi.transAxes)
                    ax_kpi.text(x, 0.15, label, ha='center', va='center',
                                fontsize=7.5, color='#666', transform=ax_kpi.transAxes)

                # 分隔线
                fig.add_axes([0.02, 0.845, 0.96, 0.005]).set_axis_off()

                # 资产端表格（左半）
                ax_a = fig.add_axes([0.02, 0.06, 0.47, 0.77])
                ax_a.axis('off')
                ax_a.set_title('资产端', fontsize=10, fontweight='bold',
                               color='#1565c0', pad=4, loc='left')
                a_vals = []
                for _, r in asset_df.iterrows():
                    if r['账户/项目'] == '':
                        continue
                    a_vals.append([r['大类'], r['账户/项目'],
                                   f'¥{r["金额(CNY)"]:,.0f}'])
                if a_vals:
                    tbl_a = ax_a.table(
                        cellText=a_vals,
                        colLabels=['大类', '项目', '金额'],
                        loc='upper center', cellLoc='left',
                        colWidths=[0.28, 0.42, 0.30],
                    )
                    _style_table(tbl_a)
                ax_a.text(0.0, -0.02, f'总资产  ¥{curr["total_assets"]:,.0f}',
                          transform=ax_a.transAxes, fontsize=9,
                          fontweight='bold', color='#1565c0')

                # 负债端表格（右半）
                ax_l = fig.add_axes([0.51, 0.06, 0.47, 0.77])
                ax_l.axis('off')
                ax_l.set_title('负债端', fontsize=10, fontweight='bold',
                               color='#c62828', pad=4, loc='left')
                l_vals = []
                for _, r in liab_df.iterrows():
                    if r['账户/项目'] == '':
                        continue
                    l_vals.append([r['大类'], r['账户/项目'],
                                   f'¥{r["金额(CNY)"]:,.0f}'])
                if l_vals:
                    tbl_l = ax_l.table(
                        cellText=l_vals,
                        colLabels=['大类', '项目', '金额'],
                        loc='upper center', cellLoc='left',
                        colWidths=[0.28, 0.42, 0.30],
                    )
                    _style_table(tbl_l)
                ax_l.text(0.0, -0.02,
                          f'总负债  ¥{curr["total_liabilities"]:,.0f}    '
                          f'净资产  ¥{curr["net_worth"]:,.0f}',
                          transform=ax_l.transAxes, fontsize=9,
                          fontweight='bold', color='#c62828')

                # 垂直分隔线
                fig.add_axes([0.494, 0.06, 0.003, 0.80]).set_axis_off()

                fig.text(0.5, 0.01,
                         f'FamilyFund Quarterly Report · {q_curr} · Generated by FamilyFund',
                         ha='center', fontsize=7, color='#999')
                pdf.savefig(fig, bbox_inches='tight')
                plt.close(fig)

                # ── Page 2: 净资产瀑布图 ──
                if q_prev and qoq and compare_df is not None:
                    wf_data = generate_waterfall_data(qoq)
                    labels = [d['label'] for d in wf_data]
                    values = [d['value'] for d in wf_data]
                    types  = [d['type']  for d in wf_data]
                    running = 0
                    bottoms, heights, colors_wf = [], [], []
                    for v, t in zip(values, types):
                        if t == 'absolute':
                            bottoms.append(0)
                            heights.append(v)
                            colors_wf.append('#1565c0')
                            running = v
                        else:
                            bottoms.append(min(running, running + v))
                            heights.append(abs(v))
                            colors_wf.append('#2e7d32' if v >= 0 else '#d32f2f')
                            running += v

                    fig2, ax_wf = plt.subplots(figsize=PAGE)
                    fig2.suptitle(f'净资产变化瀑布图  {q_prev} → {q_curr}', fontsize=14,
                                  fontweight='bold', color='#1a237e', y=0.97)
                    ax_wf.bar(range(len(labels)), heights, bottom=bottoms,
                              color=colors_wf, width=0.55, edgecolor='white', linewidth=0.5)
                    ax_wf.set_xticks(range(len(labels)))
                    wrapped = [l.replace(' ', '\n', 1) if len(l) > 8 else l for l in labels]
                    ax_wf.set_xticklabels(wrapped, rotation=0, ha='center', fontsize=9)
                    ax_wf.yaxis.set_major_formatter(
                        matplotlib.ticker.FuncFormatter(lambda x, _: f'¥{x/1e4:.0f}万'))
                    ax_wf.tick_params(axis='x', which='both', length=0)
                    ax_wf.grid(axis='y', alpha=0.3, linestyle='--')
                    ax_wf.spines['top'].set_visible(False)
                    ax_wf.spines['right'].set_visible(False)
                    # 在柱子上方标注金额
                    for i, (h, b, v) in enumerate(zip(heights, bottoms, values)):
                        y_pos = b + h + max(heights) * 0.01
                        ax_wf.text(i, y_pos, f'¥{v/1e4:+.0f}万',
                                   ha='center', va='bottom', fontsize=8,
                                   color='#2e7d32' if v >= 0 else '#d32f2f',
                                   fontweight='bold')
                    fig2.subplots_adjust(left=0.1, right=0.95, top=0.90, bottom=0.15)
                    fig2.text(0.5, 0.02,
                              f'FamilyFund Quarterly Report · {q_curr} · Page 2',
                              ha='center', fontsize=7, color='#999')
                    pdf.savefig(fig2, bbox_inches='tight')
                    plt.close(fig2)

                    # ── Page 3: 资产结构对比 ──
                    fig3, ax_bar = plt.subplots(figsize=PAGE)
                    fig3.suptitle(f'资产结构对比  {q_prev} vs {q_curr}', fontsize=14,
                                  fontweight='bold', color='#1a237e', y=0.97)
                    x_pos = range(len(compare_df))
                    w = 0.35
                    bars1 = ax_bar.bar([i - w/2 for i in x_pos], compare_df[q_prev],
                                        width=w, label=q_prev, color='#90CAF9', edgecolor='white')
                    bars2 = ax_bar.bar([i + w/2 for i in x_pos], compare_df[q_curr],
                                        width=w, label=q_curr, color='#1565c0', edgecolor='white')
                    ax_bar.set_xticks(list(x_pos))
                    ax_bar.set_xticklabels(compare_df['大类'], rotation=20,
                                           ha='right', fontsize=10)
                    ax_bar.yaxis.set_major_formatter(
                        matplotlib.ticker.FuncFormatter(lambda x, _: f'¥{x/1e4:.0f}万'))
                    ax_bar.legend(fontsize=10, loc='upper right')
                    ax_bar.grid(axis='y', alpha=0.3, linestyle='--')
                    ax_bar.spines['top'].set_visible(False)
                    ax_bar.spines['right'].set_visible(False)
                    ax_bar.tick_params(axis='x', which='both', length=0)
                    fig3.subplots_adjust(left=0.1, right=0.95, top=0.90, bottom=0.18)
                    fig3.text(0.5, 0.02,
                              f'FamilyFund Quarterly Report · {q_curr} · Page 3',
                              ha='center', fontsize=7, color='#999')
                    pdf.savefig(fig3, bbox_inches='tight')
                    plt.close(fig3)

            buf.seek(0)
            return buf.getvalue()

        col_export, _ = st.columns([1, 3])
        with col_export:
            try:
                _cd = compare_df if (q_prev and 'compare_df' in dir()) else None
                _qoq = qoq if q_prev else None
                pdf_bytes = _generate_quarterly_pdf(
                    q_curr, curr, q_prev, _qoq, asset_df, liab_df, _cd
                )
                st.download_button(
                    label="📄 下载季度 PDF 报告",
                    data=pdf_bytes,
                    file_name=f"FamilyFund_Quarterly_{q_curr}.pdf",
                    mime="application/pdf",
                    type="primary",
                )
            except Exception as e:
                st.error(f"PDF 生成失败: {e}")
