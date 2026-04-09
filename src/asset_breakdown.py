import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import os
import math

matplotlib.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'PingFang SC']
matplotlib.rcParams['axes.unicode_minus'] = False

# --- 配置路径 ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_XLSX = os.path.join(BASE_DIR, 'CurrentAsset.xlsx')
OUTPUT_BREAKDOWN_CSV = os.path.join(BASE_DIR, 'data', 'output_asset_breakdown.csv')
OUTPUT_SUMMARY_CSV = os.path.join(BASE_DIR, 'data', 'output_asset_summary.csv')
OUTPUT_PIE_CHART = os.path.join(BASE_DIR, 'output', 'asset_allocation.png')

# --- 分区 → 资产类别映射 ---
# 每个 section header 对应一个默认资产类别
SECTION_CLASS_MAP = {
    '标普场外': 'US_Index_Fund',
    '纳指场外': 'US_Index_Fund',
    'A500场外': 'CN_Index_Fund',
    '中信证券': 'ETF_Stock',
    '招商银行': 'Fixed_Income',
    '工商银行': 'Fixed_Income',
    '公司股票': 'Company_Stock',
    '实物': 'Gold',
}

# 需要从所属分区中重分类的特殊持仓（按名称匹配）
HOLDING_OVERRIDES = {
    '现金': 'Cash',
    '国债ETF': 'Fixed_Income',
    '国债ETF东财': 'Fixed_Income',
    '短融ETF': 'Fixed_Income',
    '黄金': 'Gold',
}

# 独立行（不属于任何分区）的分类
STANDALONE_CLASS = {
    '现金（中行）': 'Cash',
    '现金（招行）': 'Cash',
}

# 资产类别显示名称
CLASS_DISPLAY_NAMES = {
    'US_Index_Fund': '美股指数基金',
    'CN_Index_Fund': 'A股指数基金',
    'ETF_Stock': 'ETF与股票',
    'Fixed_Income': '固定收益',
    'Gold': '黄金',
    'Company_Stock': '公司股票',
    'Cash': '现金',
}

# 列名标准化映射（XLSX 中出现的 header 变体 → 标准字段名）
COLUMN_MAP = {
    '基金公司': 'name',
    '标的': 'name',
    '项目': 'code',
    '代码': 'code',
    '成本': 'cost_price',
    '净值': 'current_price',
    '份额': 'shares',
    '总成本': 'total_cost',
    '当前金额': 'current_value',
    '待确认金额': 'pending_amount',
    '目前定投计划': 'dca_plan',
    '总金额': 'total_value',
    '浮盈/浮亏金额': 'pnl_amount',
    '浮盈/浮亏': 'pnl_percent',
}

# Section header 的识别列表
SECTION_HEADERS = set(SECTION_CLASS_MAP.keys()) | {'汇率'}

# Column header 的识别关键字
COLUMN_HEADER_MARKERS = {'基金公司', '标的'}


def _is_section_header(row):
    """判断是否是分区标题行（只有 col A 有值，其余为空）。"""
    val_a = row.iloc[0]
    if pd.isna(val_a):
        return False
    val_a_str = str(val_a).strip()
    # Check if it's a known section header OR if only col A has a value
    rest_empty = all(pd.isna(row.iloc[i]) for i in range(1, len(row)))
    return rest_empty and len(val_a_str) > 0


def _is_column_header(row):
    """判断是否是列标题行。"""
    val_a = row.iloc[0]
    if pd.isna(val_a):
        return False
    return str(val_a).strip() in COLUMN_HEADER_MARKERS


def _is_data_row(row):
    """判断是否是有效数据行（col A 有值，且不是标题行，至少有总成本或当前金额）。"""
    val_a = row.iloc[0]
    if pd.isna(val_a):
        return False
    val_a_str = str(val_a).strip()
    if val_a_str in COLUMN_HEADER_MARKERS or val_a_str in SECTION_HEADERS:
        return False
    # Must have at least total_cost or current_value
    has_financial = not pd.isna(row.iloc[5]) or not pd.isna(row.iloc[6])
    return has_financial


def _safe_float(val, default=0.0):
    """安全转换为 float。"""
    if pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_asset_xlsx(xlsx_path=None):
    """解析 CurrentAsset.xlsx，提取所有持仓明细。

    Args:
        xlsx_path: XLSX 文件路径，默认使用 DEFAULT_XLSX。

    Returns:
        list[dict]: 每个 dict 代表一条持仓记录，包含 platform/name/code 等字段。
        若文件不存在返回 None。
    """
    if xlsx_path is None:
        xlsx_path = DEFAULT_XLSX

    if not os.path.exists(xlsx_path):
        print(f"错误: 找不到输入文件 {xlsx_path}")
        return None

    df = pd.read_excel(xlsx_path, header=None)
    holdings = []
    current_section = None
    current_col_map = None  # The column header mapping for current section

    for idx, row in df.iterrows():
        val_a = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ''

        # Skip known non-data markers
        if val_a == '汇率' or (val_a and val_a.replace('.', '', 1).isdigit() and current_section is None):
            current_section = None
            continue

        # Check for standalone cash entries (rows at the end, outside sections)
        if val_a in STANDALONE_CLASS:
            current_val = _safe_float(row.iloc[6])
            total_val = _safe_float(row.iloc[9])
            if total_val == 0 and current_val != 0:
                total_val = current_val + _safe_float(row.iloc[7])
            holdings.append({
                'platform': val_a,
                'section': val_a,
                'name': val_a,
                'code': '',
                'cost_price': _safe_float(row.iloc[2]),
                'current_price': _safe_float(row.iloc[3]),
                'shares': _safe_float(row.iloc[4]),
                'total_cost': _safe_float(row.iloc[5]),
                'current_value': current_val,
                'pending_amount': _safe_float(row.iloc[7]),
                'total_value': total_val,
                'pnl_amount': _safe_float(row.iloc[10]),
                'pnl_percent': _safe_float(row.iloc[11]),
            })
            continue

        # Detect section header
        if _is_section_header(row):
            if val_a in SECTION_HEADERS:
                current_section = val_a
            else:
                current_section = None
            continue

        # Detect column header
        if _is_column_header(row):
            continue

        # Skip rows outside any known section
        if current_section is None:
            continue

        # Check if this is a data row
        if _is_data_row(row):
            name = val_a
            code = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ''

            current_val = _safe_float(row.iloc[6])
            total_val = _safe_float(row.iloc[9])
            if total_val == 0 and current_val != 0:
                total_val = current_val + _safe_float(row.iloc[7])

            holdings.append({
                'platform': current_section,
                'section': current_section,
                'name': name,
                'code': code,
                'cost_price': _safe_float(row.iloc[2]),
                'current_price': _safe_float(row.iloc[3]),
                'shares': _safe_float(row.iloc[4]),
                'total_cost': _safe_float(row.iloc[5]),
                'current_value': current_val,
                'pending_amount': _safe_float(row.iloc[7]),
                'total_value': total_val,
                'pnl_amount': _safe_float(row.iloc[10]),
                'pnl_percent': _safe_float(row.iloc[11]),
            })

    return holdings


def classify_assets(holdings):
    """将持仓按资产类别分组。

    Args:
        holdings: parse_asset_xlsx() 返回的持仓列表。

    Returns:
        dict: {asset_class: [holding_dicts]} — 按资产类别分组的持仓。
    """
    classified = {cls: [] for cls in CLASS_DISPLAY_NAMES}

    for h in holdings:
        name = h['name']

        # Check holding-level overrides first
        asset_class = None
        for pattern, cls in HOLDING_OVERRIDES.items():
            if name == pattern:
                asset_class = cls
                break

        # Check standalone entries
        if asset_class is None and name in STANDALONE_CLASS:
            asset_class = STANDALONE_CLASS[name]

        # Fall back to section-level mapping
        if asset_class is None:
            asset_class = SECTION_CLASS_MAP.get(h['section'])

        if asset_class is None:
            continue

        h_copy = dict(h)
        h_copy['asset_class'] = asset_class
        classified[asset_class].append(h_copy)

    return classified


def compute_summary(classified):
    """计算每个资产类别的汇总。

    Args:
        classified: classify_assets() 的返回值。

    Returns:
        list[dict]: 每个 dict 是一个类别的汇总行。
    """
    summary = []
    grand_total = sum(
        sum(_safe_float(h.get('total_value', h.get('current_value', 0))) for h in holdings)
        for holdings in classified.values()
    )

    for cls, holdings in classified.items():
        if not holdings:
            continue
        total_cost = sum(_safe_float(h['total_cost']) for h in holdings)
        total_value = sum(_safe_float(h.get('total_value', h.get('current_value', 0))) for h in holdings)
        pnl_amount = sum(_safe_float(h['pnl_amount']) for h in holdings)
        pnl_percent = pnl_amount / total_cost if total_cost != 0 else 0.0
        allocation = total_value / grand_total if grand_total != 0 else 0.0

        summary.append({
            'Asset_Class': cls,
            'Display_Name': CLASS_DISPLAY_NAMES[cls],
            'Total_Cost': round(total_cost, 2),
            'Total_Value': round(total_value, 2),
            'PnL_Amount': round(pnl_amount, 2),
            'PnL_Percent': round(pnl_percent, 4),
            'Allocation_Percent': round(allocation, 4),
        })

    return summary


def generate_report(classified, summary):
    """终端打印资产配置报告。"""
    grand_total_value = sum(s['Total_Value'] for s in summary)
    grand_total_cost = sum(s['Total_Cost'] for s in summary)
    grand_pnl = sum(s['PnL_Amount'] for s in summary)

    print("\n" + "=" * 60)
    print(" 📊 家庭资产配置报告 (Asset Allocation Report)")
    print("=" * 60)

    for s in sorted(summary, key=lambda x: x['Total_Value'], reverse=True):
        pnl_sign = "+" if s['PnL_Amount'] >= 0 else ""
        print(f"\n  {s['Display_Name']} ({s['Asset_Class']})")
        print(f"    总市值: ¥ {s['Total_Value']:>14,.2f}  |  占比: {s['Allocation_Percent'] * 100:5.1f}%")
        print(f"    总成本: ¥ {s['Total_Cost']:>14,.2f}  |  盈亏: {pnl_sign}¥ {s['PnL_Amount']:,.2f} ({s['PnL_Percent'] * 100:+.2f}%)")

        # List holdings
        holdings = classified[s['Asset_Class']]
        for h in holdings:
            tv = _safe_float(h.get('total_value', h.get('current_value', 0)))
            pnl = _safe_float(h['pnl_amount'])
            pnl_s = "+" if pnl >= 0 else ""
            print(f"      - {h['name']:<14s} ¥ {tv:>12,.2f}  {pnl_s}{pnl:,.2f}")

    print("\n" + "-" * 60)
    grand_pnl_sign = "+" if grand_pnl >= 0 else ""
    grand_pnl_pct = grand_pnl / grand_total_cost * 100 if grand_total_cost else 0
    print(f"  💰 总资产: ¥ {grand_total_value:>14,.2f}")
    print(f"  📈 总盈亏: {grand_pnl_sign}¥ {grand_pnl:,.2f} ({grand_pnl_pct:+.2f}%)")
    print("=" * 60 + "\n")


def generate_pie_chart(summary, output_path=None):
    """生成资产配置饼图。"""
    if output_path is None:
        output_path = OUTPUT_PIE_CHART

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Filter out zero-value classes
    data = [(s['Display_Name'], s['Total_Value']) for s in summary if s['Total_Value'] > 0]
    data.sort(key=lambda x: x[1], reverse=True)

    labels = [d[0] for d in data]
    values = [d[1] for d in data]

    colors = ['#2196F3', '#4CAF50', '#FF9800', '#9C27B0', '#F44336', '#00BCD4', '#795548']

    fig, ax = plt.subplots(figsize=(10, 7), dpi=150)
    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct=lambda pct: f'{pct:.1f}%\n¥{pct/100*sum(values):,.0f}',
        colors=colors[:len(data)],
        startangle=90,
        pctdistance=0.75,
        wedgeprops=dict(width=0.5, edgecolor='white', linewidth=2),
    )

    for text in texts:
        text.set_fontsize(10)
    for autotext in autotexts:
        autotext.set_fontsize(8)

    ax.set_title('Family Asset Allocation', fontsize=14, fontweight='bold', pad=20)

    centre_circle = plt.Circle((0, 0), 0.5, fc='white')
    ax.add_artist(centre_circle)

    # Center text: total value
    total = sum(values)
    ax.text(0, 0, f'¥{total:,.0f}', ha='center', va='center', fontsize=12, fontweight='bold')

    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    print(f"✅ 资产配置饼图已生成: {output_path}")


def export_breakdown_csv(classified, output_path=None):
    """导出逐笔持仓明细 CSV。"""
    if output_path is None:
        output_path = OUTPUT_BREAKDOWN_CSV

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    rows = []
    for cls, holdings in classified.items():
        for h in holdings:
            rows.append({
                'Asset_Class': cls,
                'Platform': h['platform'],
                'Name': h['name'],
                'Code': h.get('code', ''),
                'Cost_Price': h['cost_price'],
                'Current_Price': h['current_price'],
                'Shares': h['shares'],
                'Total_Cost': h['total_cost'],
                'Current_Value': h['current_value'],
                'Pending_Amount': h['pending_amount'],
                'Total_Value': _safe_float(h.get('total_value', h.get('current_value', 0))),
                'PnL_Amount': h['pnl_amount'],
                'PnL_Percent': h['pnl_percent'],
            })

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    print(f"✅ 持仓明细已导出: {output_path}")
    return df


def export_summary_csv(summary, output_path=None):
    """导出资产类别汇总 CSV。"""
    if output_path is None:
        output_path = OUTPUT_SUMMARY_CSV

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    df = pd.DataFrame(summary)
    df.to_csv(output_path, index=False)
    print(f"✅ 资产汇总已导出: {output_path}")
    return df


if __name__ == "__main__":
    holdings = parse_asset_xlsx()
    if holdings is not None:
        classified = classify_assets(holdings)
        summary = compute_summary(classified)
        generate_report(classified, summary)
        generate_pie_chart(summary)
        export_breakdown_csv(classified)
        export_summary_csv(summary)
