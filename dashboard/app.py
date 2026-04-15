import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import os
import sys
from datetime import date, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from nav_engine import (
    load_portfolio, validate_portfolio, compute_fund_nav,
    compute_class_nav, compute_allocation, compute_cost_basis,
    compute_xirr,
    CLASS_DISPLAY_NAMES, VALID_ASSET_CLASSES,
    _atomic_write_csv, update_snapshot, delete_snapshot,
)
from fx_service import get_exchange_rate, get_stock_price, load_sap_price_cache, save_sap_price_cache
from sap_stock import load_own_sap, load_move_sap, own_sap_summary, move_sap_summary
from pdf_report import generate_report as generate_pdf_report
from benchmark import get_benchmarks, BENCHMARK_DISPLAY_NAMES, BENCHMARK_COLORS
from market_monitor import (
    get_market_data, set_pe_override,
    compute_bias, compute_vix_signal, compute_pe_signal, compute_qvix_signal,
    lookup_multiplier, lookup_a_share_multiplier,
    TARGETS,
)

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
    return df, fund_nav, class_nav, allocation, cost_basis, xirr


# Pick the best available data file
csv_path = DEFAULT_CSV if os.path.exists(DEFAULT_CSV) else SAMPLE_CSV
raw_df, fund_nav_df, class_nav_dict, allocation_df, cost_basis_df, xirr_value = load_data(csv_path)

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
all_classes = sorted(raw_df['Asset_Class'].unique())
display_map = {cls: CLASS_DISPLAY_NAMES.get(cls, cls) for cls in all_classes}

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

# ─── Tabs ───

tab_dashboard, tab_update, tab_history, tab_sap, tab_market = st.tabs(
    ["Dashboard", "Weekly Update", "History", "SAP Stock", "Market Monitor"]
)

# ═══════════════════════════════════════════════════════════
# Tab 1: Dashboard
# ═══════════════════════════════════════════════════════════

with tab_dashboard:

    # ─── Section 1: Fund Overview ───

    st.header("基金总览")

    # KPI metrics
    latest_fund = filtered_fund.iloc[-1] if len(filtered_fund) > 0 else fund_nav_df.iloc[-1]
    latest_date = latest_fund['Date']
    latest_holdings = raw_df[raw_df['Date'] == raw_df['Date'].max()]

    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
    with col1:
        st.metric("总资产", f"¥{latest_fund['Total_Value']:,.0f}")
    with col2:
        st.metric("单位净值", f"{latest_fund['NAV']:.4f}")
    with col3:
        ret = latest_fund['Cumulative_Return(%)']
        st.metric("累计收益率", f"{ret:+.2f}%")
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
    with col6:
        mdd = latest_fund.get('Max_Drawdown(%)')
        if mdd is not None and not pd.isna(mdd):
            st.metric("最大回撤", f"{mdd:.2f}%")
        else:
            st.metric("最大回撤", "—")
    with col7:
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

    col_chart, col_pie = st.columns([3, 2])

    # Multi-line NAV chart
    with col_chart:
        class_lines = []
        for cls in selected_classes:
            if cls in class_nav_dict:
                nav_df = class_nav_dict[cls].copy()
                nav_df = nav_df[(nav_df['Date'] >= date_start) & (nav_df['Date'] <= date_end)]
                nav_df['Display_Name'] = display_map[cls]
                class_lines.append(nav_df)

        if class_lines:
            combined = pd.concat(class_lines, ignore_index=True)
            fig_class = px.line(
                combined, x='Date', y='NAV', color='Display_Name',
                title='分类净值对比',
                labels={'Date': '日期', 'NAV': '净值', 'Display_Name': '资产类别'},
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
            fig_pie = px.pie(
                pie_data, values='Total_Value', names='Display_Name',
                title='资产配置',
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Set2,
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

    # ─── Section 4: P/L Analysis ───

    st.header("盈亏分析")

    if cost_basis_df is not None and len(cost_basis_df) > 0:
        # Summary KPIs
        total_cost = cost_basis_df['Cost_Basis'].sum()
        total_market = cost_basis_df['Market_Value'].sum()
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
        chart_data = cost_basis_df.copy()
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
        pl_display = cost_basis_df.copy()
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

    # ─── Cash 调仓辅助器 ───
    with st.expander("💱 调仓辅助器（计算 Cash 余额）", expanded=False):
        st.caption(
            "填入本期买卖操作，系统自动计算 Cash 的最终市值和 NCF。"
            "内部调仓（买入/卖出基金）NCF 始终为 0，只有外部资金进出才计入 NCF。"
        )

        # Initialize rebalance entries in session state
        if 'rebalance_entries' not in st.session_state:
            st.session_state['rebalance_entries'] = []

        # Add entry buttons
        col_add1, col_add2, col_add3 = st.columns(3)
        with col_add1:
            if st.button("＋ 卖出（Cash 增加）", key="rb_add_sell"):
                st.session_state['rebalance_entries'].append({'type': '卖出', 'note': '', 'amount': 0.0})
                st.rerun()
        with col_add2:
            if st.button("＋ 买入（Cash 减少）", key="rb_add_buy"):
                st.session_state['rebalance_entries'].append({'type': '买入', 'note': '', 'amount': 0.0})
                st.rerun()
        with col_add3:
            if st.button("＋ 外部入金/取出", key="rb_add_external"):
                st.session_state['rebalance_entries'].append({'type': '外部', 'note': '', 'amount': 0.0})
                st.rerun()

        # Render each entry row
        entries = st.session_state['rebalance_entries']
        to_remove = []
        for idx, entry in enumerate(entries):
            c1, c2, c3, c4 = st.columns([1.2, 3, 2, 0.7])
            with c1:
                type_label = {'卖出': '🔴 卖出', '买入': '🟢 买入', '外部': '🔵 外部'}.get(entry['type'], entry['type'])
                st.markdown(f"**{type_label}**")
            with c2:
                entry['note'] = st.text_input("备注", value=entry['note'], key=f"rb_note_{idx}", label_visibility="collapsed", placeholder="资产名称/说明")
            with c3:
                raw_val = st.number_input("金额(CNY)", value=abs(entry['amount']), min_value=0.0, step=100.0, format="%.2f", key=f"rb_amt_{idx}", label_visibility="collapsed")
                # Sell → positive cash delta; Buy → negative; External: keep sign from user
                if entry['type'] == '卖出':
                    entry['amount'] = raw_val
                elif entry['type'] == '买入':
                    entry['amount'] = -raw_val
                else:
                    entry['amount'] = raw_val  # External can be negative (withdrawal) — handled below
            with c4:
                if st.button("✕", key=f"rb_del_{idx}"):
                    to_remove.append(idx)

        for idx in reversed(to_remove):
            st.session_state['rebalance_entries'].pop(idx)
        if to_remove:
            st.rerun()

        # For external entries, allow negative (取出)
        # Re-render external sign toggle
        for idx, entry in enumerate(entries):
            if entry['type'] == '外部':
                sign_key = f"rb_ext_sign_{idx}"
                is_out = st.checkbox("取出（负值）", key=sign_key, value=entry['amount'] < 0)
                if is_out:
                    entry['amount'] = -abs(entry['amount'])
                else:
                    entry['amount'] = abs(entry['amount'])

        if entries:
            st.divider()

            # Compute results
            prev_cash_rows = st.session_state['update_template'][
                st.session_state['update_template']['Asset_Class'] == 'Cash'
            ]
            prev_cash_tv = prev_cash_rows['Total_Value'].sum() if len(prev_cash_rows) > 0 else 0.0

            internal_delta = sum(e['amount'] for e in entries if e['type'] in ('卖出', '买入'))
            external_ncf = sum(e['amount'] for e in entries if e['type'] == '外部')
            new_cash_tv = prev_cash_tv + internal_delta + external_ncf

            res_col1, res_col2, res_col3 = st.columns(3)
            with res_col1:
                st.metric("上周 Cash", f"¥{prev_cash_tv:,.0f}")
            with res_col2:
                st.metric("本周 Cash Total_Value", f"¥{new_cash_tv:,.0f}",
                          delta=f"{internal_delta + external_ncf:+,.0f}")
            with res_col3:
                st.metric("Cash Net_Cash_Flow (外部)", f"¥{external_ncf:+,.0f}",
                          help="只有外部入金/取出计入 NCF，内部调仓不计")

            if st.button("✅ 应用到持仓表（更新 Cash 行）", type="primary", key="rb_apply"):
                template = st.session_state['update_template'].copy()
                cash_mask = template['Asset_Class'] == 'Cash'
                if cash_mask.any():
                    # Update all Cash rows proportionally (usually just one)
                    cash_idx = template[cash_mask].index
                    if len(cash_idx) == 1:
                        i = cash_idx[0]
                        template.at[i, 'Total_Value'] = round(new_cash_tv, 2)
                        template.at[i, 'Shares'] = round(new_cash_tv, 2)  # Cash: shares = CNY amount
                        template.at[i, 'Net_Cash_Flow'] = round(external_ncf, 2)
                    else:
                        st.warning("检测到多条 Cash 行，请手动更新 Cash 的 Total_Value 和 NCF")
                else:
                    st.warning("未找到 Cash 行，请手动更新")
                st.session_state['update_template'] = template
                st.session_state['rebalance_entries'] = []
                st.success(f"已更新 Cash → Total_Value: ¥{new_cash_tv:,.0f}，NCF: ¥{external_ncf:+,.0f}")
                st.rerun()

        if entries and st.button("清空所有条目", key="rb_clear", type="secondary"):
            st.session_state['rebalance_entries'] = []
            st.rerun()

    # ─── Editable table ───
    st.markdown("**编辑持仓** (可增删行，修改价格/份额/市值。外部资金进出仅填在 Cash 行的 Net_Cash_Flow)")

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

        # Non-Cash/Company_Stock rows should have NCF = 0
        non_cash_ncf = df[
            ~df['Asset_Class'].isin(['Cash', 'Company_Stock']) &
            (df['Net_Cash_Flow'].fillna(0) != 0)
        ]
        if len(non_cash_ncf) > 0:
            names = non_cash_ncf['Name'].tolist()
            warnings.append(f"非现金持仓有 Net_Cash_Flow ≠ 0: {', '.join(str(n) for n in names)}。"
                            f"外部资金进出应记录在 Cash 行（SAP 归属记录在 Company_Stock 行），内部调仓 NCF 应为 0")

        # Cash rows with NCF (informational)
        cash_ncf = df[(df['Asset_Class'] == 'Cash') & (df['Net_Cash_Flow'].fillna(0) != 0)]
        if len(cash_ncf) > 0:
            total_ncf = cash_ncf['Net_Cash_Flow'].sum()
            warnings.append(f"本期现金流: ¥{total_ncf:+,.2f}")

        # Large price swings
        prev_latest = prev_df[prev_df['Date'] == last_date_str]
        for i, row in df.iterrows():
            name = row.get('Name', '')
            prev_row = prev_latest[prev_latest['Name'] == name]
            if len(prev_row) > 0:
                old_tv = prev_row.iloc[0]['Total_Value']
                new_tv = row.get('Total_Value', 0)
                if old_tv > 0 and not pd.isna(new_tv):
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

        # Initialize from iCloud-synced cache on first visit (before widgets render)
        if 'sap_price_initialized' not in st.session_state:
            cache = load_sap_price_cache()
            if cache:
                st.session_state['sap_current_price'] = cache['price_eur']
                st.session_state['sap_fx_rate'] = cache['fx_rate']
            st.session_state['sap_price_initialized'] = True

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
                "SAP Price (EUR)", value=170.0, min_value=0.01,
                format="%.2f", key="sap_current_price")
        with fx_col:
            sap_fx_rate = st.number_input(
                "EUR/CNY Rate", value=8.0, min_value=0.01,
                format="%.4f", key="sap_fx_rate")
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
            with kpi_cols[0]:
                st.markdown("**Own SAP (ESPP)**")
                st.metric("持股", f"{own_sum['total_shares']:.2f} 股")
                st.metric("成本", f"¥{own_sum['total_cost']:,.2f}")
                st.metric("市值", f"¥{own_value:,.2f}")
                st.metric("盈亏", f"¥{own_pl:+,.2f}")
                if own_sum['break_even_eur']:
                    st.caption(f"盈亏平衡价: {own_sum['break_even_eur']:.2f} EUR")

        if move_df is not None:
            move_sum = move_sap_summary(move_df, fx_rate=sap_fx_rate)
            move_value = move_sum['total_shares'] * sap_price_eur * sap_fx_rate
            move_pl = move_value - move_sum['total_cost']
            with kpi_cols[1]:
                st.markdown("**Move SAP (RSU)**")
                st.metric("持股", f"{move_sum['total_shares']:.2f} 股")
                st.metric("成本", f"¥{move_sum['total_cost']:,.2f}")
                st.metric("市值", f"¥{move_value:,.2f}")
                st.metric("盈亏", f"¥{move_pl:+,.2f}")
                if move_sum['break_even_eur']:
                    st.caption(f"盈亏平衡价: {move_sum['break_even_eur']:.2f} EUR")

        with kpi_cols[2]:
            combined_shares = (own_sum['total_shares'] if own_df is not None else 0) + \
                              (move_sum['total_shares'] if move_df is not None else 0)
            combined_cost = (own_sum['total_cost'] if own_df is not None else 0) + \
                            (move_sum['total_cost'] if move_df is not None else 0)
            combined_value = combined_shares * sap_price_eur * sap_fx_rate
            combined_pl = combined_value - combined_cost
            st.markdown("**Combined**")
            st.metric("总持股", f"{combined_shares:.2f} 股")
            st.metric("总成本", f"¥{combined_cost:,.2f}")
            st.metric("总市值", f"¥{combined_value:,.2f}")
            st.metric("总盈亏", f"¥{combined_pl:+,.2f}")
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
    st.caption("乖离率 = (当前价 − MAn) / MAn × 100%　｜　**粗体**为主要参考均线")

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
            text = f"{emoji} {sign}{b:.2f}%"
            return f"**{text}**" if is_primary else text

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

    vix_entry    = market_data.get('vix')
    qvix_entry   = market_data.get('qvix')
    pe_sp_entry  = market_data.get('pe_sp500')
    pe_ndx_entry = market_data.get('pe_ndx100')

    vix_val  = vix_entry.get('price')    if vix_entry   else None
    qvix_val = qvix_entry.get('price')   if qvix_entry  else None
    pe_sp    = (pe_sp_entry.get('manual_override') or pe_sp_entry.get('value'))   if pe_sp_entry  else None
    pe_ndx   = (pe_ndx_entry.get('manual_override') or pe_ndx_entry.get('value')) if pe_ndx_entry else None
    sp_src   = ('手动' if (pe_sp_entry or {}).get('manual_override') else 'VOO auto') if pe_sp_entry else '—'
    ndx_src  = ('手动' if (pe_ndx_entry or {}).get('manual_override') else 'QQQ auto') if pe_ndx_entry else '—'

    vix_label,   vix_emoji   = compute_vix_signal(vix_val)
    qvix_label,  qvix_emoji  = compute_qvix_signal(qvix_val)
    sp_pe_label,  sp_pe_emoji  = compute_pe_signal(pe_sp,  'sp500')
    ndx_pe_label, ndx_pe_emoji = compute_pe_signal(pe_ndx, 'ndx100')

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
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
    st.caption("基于 PE × VIX 矩阵，仅适用于标普500和纳指100。A股和黄金请参考乖离率颜色自行判断。")

    mult_sp  = lookup_multiplier(pe_sp,  vix_val, 'sp500')
    mult_ndx = lookup_multiplier(pe_ndx, vix_val, 'ndx100')

    def _mult_color(m):
        if m in ('暂停',):
            return '#d32f2f'
        if m in ('观望',):
            return '#f57c00'
        if m == '顶格':
            return '#1565c0'
        return '#2e7d32'

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
        "**倍数说明**: 暂停 = 不建议定投；观望 = 维持最小仓位；顶格 = 全力加仓。"
        "倍数以您自身基准定投金额为基础执行。"
    )
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
        "**倍数说明**: 暂停 = 不建议定投；观望 = 维持最小仓位；顶格 = 全力加仓。"
        "倍数以您自身基准定投金额为基础执行。"
    )
    st.caption("⚠️ 仅供参考，不构成投资建议。数据来自公开市场，存在延迟。")
