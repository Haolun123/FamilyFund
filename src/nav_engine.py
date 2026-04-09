import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import os
import sys
import fcntl

matplotlib.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'PingFang SC']
matplotlib.rcParams['axes.unicode_minus'] = False

# --- 配置路径 ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(BASE_DIR, 'data', 'portfolio.csv')
OUTPUT_FUND_NAV = os.path.join(BASE_DIR, 'data', 'output_fund_nav.csv')
OUTPUT_CLASS_NAV = os.path.join(BASE_DIR, 'data', 'output_class_nav.csv')
OUTPUT_ALLOCATION = os.path.join(BASE_DIR, 'data', 'output_allocation.csv')
OUTPUT_FUND_CHART = os.path.join(BASE_DIR, 'output', 'fund_nav_trend.png')
OUTPUT_CLASS_CHART = os.path.join(BASE_DIR, 'output', 'class_nav_trend.png')
OUTPUT_PIE_CHART = os.path.join(BASE_DIR, 'output', 'asset_allocation.png')

VALID_ASSET_CLASSES = {
    'US_Index_Fund', 'CN_Index_Fund', 'ETF_Stock',
    'Fixed_Income', 'Gold', 'Company_Stock', 'Cash',
}

CLASS_DISPLAY_NAMES = {
    'US_Index_Fund': '美股指数基金',
    'CN_Index_Fund': 'A股指数基金',
    'ETF_Stock': 'ETF与股票',
    'Fixed_Income': '固定收益',
    'Gold': '黄金',
    'Company_Stock': '公司股票',
    'Cash': '现金',
}

REQUIRED_COLUMNS = [
    'Date', 'Asset_Class', 'Platform', 'Name', 'Currency',
    'Exchange_Rate', 'Shares', 'Current_Price', 'Total_Value', 'Net_Cash_Flow',
]


# ============================================================
# Data Loading & Validation
# ============================================================

def load_portfolio(csv_path=None):
    """读取 portfolio.csv 并返回 DataFrame。"""
    if csv_path is None:
        csv_path = DEFAULT_INPUT

    if not os.path.exists(csv_path):
        print(f"错误: 找不到输入文件 {csv_path}")
        return None

    df = pd.read_csv(csv_path)
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
    df['Code'] = df['Code'].fillna('')
    df['Exchange_Rate'] = df['Exchange_Rate'].fillna(1.0)
    return df


def validate_portfolio(df):
    """校验 portfolio DataFrame，返回 (errors, warnings) 列表。"""
    errors = []
    warnings = []

    # Check required columns
    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        errors.append(f"缺少必需列: {missing}")
        return errors, warnings

    # Check Asset_Class values
    invalid_classes = set(df['Asset_Class'].unique()) - VALID_ASSET_CLASSES
    if invalid_classes:
        errors.append(f"无效的 Asset_Class 值: {invalid_classes}")

    # Check Exchange_Rate > 0
    bad_rates = df[df['Exchange_Rate'] <= 0]
    if len(bad_rates) > 0:
        for _, row in bad_rates.iterrows():
            errors.append(f"Exchange_Rate 必须 > 0: {row['Name']} ({row['Exchange_Rate']})")

    # Check dates are sorted
    dates = df['Date'].unique().tolist()
    if dates != sorted(dates):
        errors.append("日期未按升序排列")

    # Check Day 0 constraint: NCF == TV for each holding on the first date
    first_date = dates[0] if dates else None
    if first_date:
        day0 = df[df['Date'] == first_date]
        for _, row in day0.iterrows():
            tv = float(row['Total_Value'])
            ncf = float(row['Net_Cash_Flow'])
            if abs(tv - ncf) > 0.01:
                warnings.append(
                    f"建仓日 {row['Name']}: Total_Value({tv}) != Net_Cash_Flow({ncf})"
                )

    return errors, warnings


# ============================================================
# Core NAV Algorithm (reusable at any aggregation level)
# ============================================================

def _run_nav_calculation(dates, total_values, net_cash_flows):
    """核心净值核算算法。

    Args:
        dates: 日期列表
        total_values: 每期总市值列表
        net_cash_flows: 每期净现金流列表

    Returns:
        list[dict]: 每期的 NAV 核算快照
    """
    current_shares = 0.0
    records = []

    for i, (date, tv, ncf) in enumerate(zip(dates, total_values, net_cash_flows)):
        if i == 0:
            nav = 1.0
            current_shares = tv / nav if tv != 0 else 0.0
            cumulative_return = 0.0
        else:
            value_before_cf = tv - ncf
            if current_shares == 0:
                nav = 1.0
                cumulative_return = 0.0
            else:
                nav = value_before_cf / current_shares
                cumulative_return = nav - 1.0

            if nav != 0:
                new_shares = ncf / nav
            else:
                new_shares = 0.0
            current_shares += new_shares

        records.append({
            'Date': date,
            'Total_Value': round(tv, 2),
            'Net_Cash_Flow': round(ncf, 2),
            'NAV': round(nav, 4),
            'Total_Shares': round(current_shares, 2),
            'Cumulative_Return(%)': round(cumulative_return * 100, 2),
        })

    return records


# ============================================================
# Fund-Level NAV
# ============================================================

def compute_fund_nav(df):
    """汇总全部持仓，计算基金整体净值。"""
    agg = df.groupby('Date').agg(
        Total_Value=('Total_Value', 'sum'),
        Net_Cash_Flow=('Net_Cash_Flow', 'sum'),
    ).reset_index().sort_values('Date')

    records = _run_nav_calculation(
        agg['Date'].tolist(),
        agg['Total_Value'].tolist(),
        agg['Net_Cash_Flow'].tolist(),
    )
    return pd.DataFrame(records)


# ============================================================
# Per-Class NAV
# ============================================================

def compute_class_nav(df):
    """按 Asset_Class 分组，分别计算每个类别的净值。

    Returns:
        dict: {Asset_Class: DataFrame}
    """
    result = {}
    for cls in sorted(df['Asset_Class'].unique()):
        cls_df = df[df['Asset_Class'] == cls]
        agg = cls_df.groupby('Date').agg(
            Total_Value=('Total_Value', 'sum'),
            Net_Cash_Flow=('Net_Cash_Flow', 'sum'),
        ).reset_index().sort_values('Date')

        records = _run_nav_calculation(
            agg['Date'].tolist(),
            agg['Total_Value'].tolist(),
            agg['Net_Cash_Flow'].tolist(),
        )
        result_df = pd.DataFrame(records)
        result_df['Asset_Class'] = cls
        result[cls] = result_df

    return result


# ============================================================
# Allocation Snapshot
# ============================================================

def compute_allocation(df, date=None):
    """计算指定日期的资产配置比例。默认取最新日期。"""
    if date is None:
        date = df['Date'].max()

    snapshot = df[df['Date'] == date]
    agg = snapshot.groupby('Asset_Class')['Total_Value'].sum().reset_index()
    grand_total = agg['Total_Value'].sum()
    agg['Allocation_Percent'] = (agg['Total_Value'] / grand_total).round(4) if grand_total else 0
    agg['Display_Name'] = agg['Asset_Class'].map(CLASS_DISPLAY_NAMES)
    agg['Date'] = date
    return agg.sort_values('Total_Value', ascending=False).reset_index(drop=True)


# ============================================================
# Cost Basis & P/L
# ============================================================

def compute_cost_basis(df):
    """计算每个持仓的成本基础和盈亏。

    Cost_Basis = 该资产所有历史 Net_Cash_Flow 之和
    P/L = 最新市值 - Cost_Basis

    Returns:
        DataFrame with: Asset_Class, Platform, Name, Cost_Basis,
                       Market_Value, Profit_Loss, Profit_Loss_Rate
    """
    latest_date = df['Date'].max()
    latest = df[df['Date'] == latest_date]

    group_cols = ['Asset_Class', 'Platform', 'Name']
    cost = df.groupby(group_cols)['Net_Cash_Flow'].sum().reset_index()
    cost.rename(columns={'Net_Cash_Flow': 'Cost_Basis'}, inplace=True)

    market = latest[group_cols + ['Total_Value']].copy()
    market.rename(columns={'Total_Value': 'Market_Value'}, inplace=True)

    result = pd.merge(market, cost, on=group_cols, how='left')
    result['Cost_Basis'] = result['Cost_Basis'].fillna(0)
    result['Profit_Loss'] = result['Market_Value'] - result['Cost_Basis']
    result['Profit_Loss_Rate'] = result.apply(
        lambda r: round(r['Profit_Loss'] / r['Cost_Basis'] * 100, 2)
        if r['Cost_Basis'] > 0 else None, axis=1
    )
    return result.sort_values('Market_Value', ascending=False).reset_index(drop=True)


# ============================================================
# File I/O with Locking
# ============================================================

def _atomic_write_csv(df, csv_path):
    """Write DataFrame to CSV with file lock + atomic replace."""
    lock_path = csv_path + '.lock'
    with open(lock_path, 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            tmp_path = csv_path + '.tmp'
            df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, csv_path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def update_snapshot(csv_path, date, updates_df):
    """Replace all rows for a given date with updated rows."""
    full_df = pd.read_csv(csv_path)
    full_df['Date'] = pd.to_datetime(full_df['Date']).dt.strftime('%Y-%m-%d')
    full_df = full_df[full_df['Date'] != date]
    updates_df = updates_df.copy()
    updates_df['Date'] = date
    combined = pd.concat([full_df, updates_df], ignore_index=True)
    combined = combined.sort_values('Date').reset_index(drop=True)
    _atomic_write_csv(combined, csv_path)


def delete_snapshot(csv_path, date):
    """Delete all rows for a given date."""
    full_df = pd.read_csv(csv_path)
    full_df['Date'] = pd.to_datetime(full_df['Date']).dt.strftime('%Y-%m-%d')
    filtered = full_df[full_df['Date'] != date].reset_index(drop=True)
    _atomic_write_csv(filtered, csv_path)


# ============================================================
# Terminal Reports
# ============================================================

def generate_combined_report(fund_nav_df, class_nav_dict, allocation_df):
    """生成完整的终端报告。"""
    latest_fund = fund_nav_df.iloc[-1]

    print("\n" + "=" * 60)
    print(" 📊 家庭基金综合报告 (FamilyFund Unified Report)")
    print("=" * 60)

    # Fund-level summary
    print("\n── 基金整体 ──")
    print(f"  统计日期     : {latest_fund['Date']}")
    print(f"  基金总市值   : ¥ {latest_fund['Total_Value']:>14,.2f}")
    print(f"  当前总份额   : {latest_fund['Total_Shares']:>14,.2f} 份")
    print(f"  💰 单位净值  : {latest_fund['NAV']:.4f}")
    print(f"  📈 累计收益率: {latest_fund['Cumulative_Return(%)']:+.2f}%")

    # Per-class NAV summary
    print("\n── 分类净值 ──")
    print(f"  {'资产类别':<16s} {'净值':>8s} {'收益率':>10s} {'市值':>16s} {'占比':>8s}")
    print("  " + "-" * 58)

    for _, alloc_row in allocation_df.iterrows():
        cls = alloc_row['Asset_Class']
        if cls in class_nav_dict:
            cls_latest = class_nav_dict[cls].iloc[-1]
            nav = cls_latest['NAV']
            ret = cls_latest['Cumulative_Return(%)']
        else:
            nav = 1.0
            ret = 0.0
        display = CLASS_DISPLAY_NAMES.get(cls, cls)
        tv = alloc_row['Total_Value']
        alloc_pct = alloc_row['Allocation_Percent'] * 100
        print(f"  {display:<14s} {nav:>8.4f} {ret:>+9.2f}% ¥{tv:>14,.2f} {alloc_pct:>6.1f}%")

    print("\n" + "=" * 60 + "\n")


# ============================================================
# Charts
# ============================================================

def plot_fund_nav(fund_nav_df, output_path=None):
    """绘制基金整体净值走势图。"""
    if output_path is None:
        output_path = OUTPUT_FUND_CHART
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    df = fund_nav_df.copy()
    df['Date'] = pd.to_datetime(df['Date'])

    plt.figure(figsize=(10, 5), dpi=150)
    plt.plot(df['Date'], df['NAV'], marker='o', linestyle='-', color='#1f77b4',
             linewidth=2, markersize=5)
    plt.title('Family Fund NAV Trend', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Date', fontsize=10)
    plt.ylabel('Net Asset Value (NAV)', fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.axhline(y=1.0, color='r', linestyle='-', linewidth=1, alpha=0.5)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"✅ 基金净值走势图: {output_path}")


def plot_class_nav(class_nav_dict, output_path=None):
    """绘制各资产类别净值走势对比图。"""
    if output_path is None:
        output_path = OUTPUT_CLASS_CHART
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    colors = ['#2196F3', '#4CAF50', '#FF9800', '#9C27B0', '#F44336', '#00BCD4', '#795548']

    plt.figure(figsize=(12, 6), dpi=150)
    for i, (cls, nav_df) in enumerate(sorted(class_nav_dict.items())):
        df = nav_df.copy()
        df['Date'] = pd.to_datetime(df['Date'])
        display = CLASS_DISPLAY_NAMES.get(cls, cls)
        color = colors[i % len(colors)]
        plt.plot(df['Date'], df['NAV'], marker='o', linestyle='-', color=color,
                 linewidth=1.5, markersize=4, label=display)

    plt.title('Asset Class NAV Comparison', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Date', fontsize=10)
    plt.ylabel('NAV', fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.axhline(y=1.0, color='r', linestyle='-', linewidth=1, alpha=0.3)
    plt.legend(loc='best', fontsize=9)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"✅ 分类净值对比图: {output_path}")


def plot_allocation_pie(allocation_df, output_path=None):
    """生成资产配置饼图。"""
    if output_path is None:
        output_path = OUTPUT_PIE_CHART
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    data = allocation_df[allocation_df['Total_Value'] > 0].sort_values('Total_Value', ascending=False)
    labels = data['Display_Name'].tolist()
    values = data['Total_Value'].tolist()
    colors = ['#2196F3', '#4CAF50', '#FF9800', '#9C27B0', '#F44336', '#00BCD4', '#795548']

    fig, ax = plt.subplots(figsize=(10, 7), dpi=150)
    wedges, texts, autotexts = ax.pie(
        values, labels=labels,
        autopct=lambda pct: f'{pct:.1f}%\n¥{pct / 100 * sum(values):,.0f}',
        colors=colors[:len(data)], startangle=90, pctdistance=0.75,
        wedgeprops=dict(width=0.5, edgecolor='white', linewidth=2),
    )
    for t in texts:
        t.set_fontsize(10)
    for t in autotexts:
        t.set_fontsize(8)

    ax.set_title('Family Asset Allocation', fontsize=14, fontweight='bold', pad=20)
    centre = plt.Circle((0, 0), 0.5, fc='white')
    ax.add_artist(centre)
    ax.text(0, 0, f'¥{sum(values):,.0f}', ha='center', va='center', fontsize=12, fontweight='bold')

    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"✅ 资产配置饼图: {output_path}")


# ============================================================
# CSV Export
# ============================================================

def export_results(fund_nav_df, class_nav_dict, allocation_df,
                   fund_path=None, class_path=None, alloc_path=None):
    """导出所有计算结果到 CSV。"""
    fund_path = fund_path or OUTPUT_FUND_NAV
    class_path = class_path or OUTPUT_CLASS_NAV
    alloc_path = alloc_path or OUTPUT_ALLOCATION

    for p in [fund_path, class_path, alloc_path]:
        os.makedirs(os.path.dirname(p), exist_ok=True)

    fund_nav_df.to_csv(fund_path, index=False)
    print(f"✅ 基金净值底稿: {fund_path}")

    class_frames = []
    for cls, nav_df in sorted(class_nav_dict.items()):
        class_frames.append(nav_df)
    if class_frames:
        all_class = pd.concat(class_frames, ignore_index=True)
        all_class.to_csv(class_path, index=False)
        print(f"✅ 分类净值底稿: {class_path}")

    allocation_df.to_csv(alloc_path, index=False)
    print(f"✅ 资产配置快照: {alloc_path}")


# ============================================================
# Main Entry Point
# ============================================================

def run(csv_path=None):
    """完整运行流程：加载 → 校验 → 计算 → 报告 → 图表 → 导出。"""
    df = load_portfolio(csv_path)
    if df is None:
        return None

    errors, warnings = validate_portfolio(df)
    if errors:
        print("\n❌ 数据校验失败:")
        for e in errors:
            print(f"   {e}")
        return None
    if warnings:
        print("\n⚠️  数据校验警告:")
        for w in warnings:
            print(f"   {w}")

    fund_nav_df = compute_fund_nav(df)
    class_nav_dict = compute_class_nav(df)
    allocation_df = compute_allocation(df)

    generate_combined_report(fund_nav_df, class_nav_dict, allocation_df)
    plot_fund_nav(fund_nav_df)
    plot_class_nav(class_nav_dict)
    plot_allocation_pie(allocation_df)
    export_results(fund_nav_df, class_nav_dict, allocation_df)

    return fund_nav_df, class_nav_dict, allocation_df


if __name__ == "__main__":
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    run(input_path)
