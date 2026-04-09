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
    compute_class_nav, compute_allocation, CLASS_DISPLAY_NAMES,
    VALID_ASSET_CLASSES,
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


@st.cache_data
def load_data(csv_path):
    df = load_portfolio(csv_path)
    if df is None:
        return None, None, None, None
    errors, warnings = validate_portfolio(df)
    if errors:
        return None, None, None, None
    fund_nav = compute_fund_nav(df)
    class_nav = compute_class_nav(df)
    allocation = compute_allocation(df)
    return df, fund_nav, class_nav, allocation


# Pick the best available data file
csv_path = DEFAULT_CSV if os.path.exists(DEFAULT_CSV) else SAMPLE_CSV
raw_df, fund_nav_df, class_nav_dict, allocation_df = load_data(csv_path)

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

# Filter data by date range
filtered_raw = raw_df[(raw_df['Date'] >= date_start) & (raw_df['Date'] <= date_end)]
filtered_fund = fund_nav_df[(fund_nav_df['Date'] >= date_start) & (fund_nav_df['Date'] <= date_end)]

# ─── Tabs ───

tab_dashboard, tab_update = st.tabs(["Dashboard", "Weekly Update"])

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

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总资产", f"¥{latest_fund['Total_Value']:,.0f}")
    with col2:
        st.metric("单位净值", f"{latest_fund['NAV']:.4f}")
    with col3:
        ret = latest_fund['Cumulative_Return(%)']
        st.metric("累计收益率", f"{ret:+.2f}%")
    with col4:
        st.metric("持仓数", f"{len(latest_holdings)}")

    # Fund NAV chart
    fig_fund = px.line(
        filtered_fund, x='Date', y='NAV',
        title='基金净值走势',
        labels={'Date': '日期', 'NAV': '单位净值'},
    )
    fig_fund.add_hline(y=1.0, line_dash="dash", line_color="red", opacity=0.5,
                       annotation_text="基准线 1.0")
    fig_fund.update_traces(line_color='#1f77b4', line_width=2.5)
    fig_fund.update_layout(hovermode='x unified', height=400)
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

    # ─── Reset button ───
    if st.button("重置为上周模板", type="secondary"):
        latest_rows = raw_df[raw_df['Date'] == last_snapshot_date].copy()
        template = latest_rows.drop(columns=['Date']).reset_index(drop=True)
        template['Net_Cash_Flow'] = 0.0
        st.session_state['update_template'] = template
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

        # Non-Cash rows should have NCF = 0 (external cash flows go through Cash only)
        non_cash_ncf = df[(df['Asset_Class'] != 'Cash') & (df['Net_Cash_Flow'].fillna(0) != 0)]
        if len(non_cash_ncf) > 0:
            names = non_cash_ncf['Name'].tolist()
            warnings.append(f"非现金持仓有 Net_Cash_Flow ≠ 0: {', '.join(str(n) for n in names)}。"
                            f"外部资金进出应仅记录在 Cash 行，内部调仓 NCF 应为 0")

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

            # Atomic write via temp file
            tmp_path = csv_path + '.tmp'
            combined_df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, csv_path)

            st.success(f"已保存 {len(save_df)} 条记录 (日期: {new_date_str}) 到 {os.path.basename(csv_path)}")
            st.balloons()

            # Clear cache and refresh
            st.cache_data.clear()
            del st.session_state['update_template']
            st.rerun()
