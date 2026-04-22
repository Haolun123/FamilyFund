"""quarterly_engine.py — 季度家庭财报核算引擎

职责：
  1. 加载 balance_sheet.csv，自动聚合 Asset_Investment 行（2026Q2起）
  2. 计算净资产、大类资产占比、财务比率
  3. QoQ 对比
  4. 生成格式化资产负债表 DataFrame

数据文件：
  - $FAMILYFUND_DATA/balance_sheet.csv
  - $FAMILYFUND_DATA/cashflow_log.csv
  - $FAMILYFUND_DATA/portfolio.csv（用于 2026Q2 起自动聚合投资类资产）
"""

import os
import pandas as pd

_DATA_DIR = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'),
)

BALANCE_SHEET_PATH = os.path.join(_DATA_DIR, 'balance_sheet.csv')
CASHFLOW_LOG_PATH  = os.path.join(_DATA_DIR, 'cashflow_log.csv')
PORTFOLIO_PATH     = os.path.join(_DATA_DIR, 'portfolio.csv')

# 基金建仓季度——从此季度起 Asset_Investment 由引擎自动聚合
FUND_START_QUARTER = '2026Q2'

CATEGORY_DISPLAY = {
    'Asset_Current':      '流动资产',
    'Asset_Investment':   '金融投资',
    'Asset_RealEstate':   '不动产/车辆',
    'Asset_PrivateEquity':'私募股权',
    'Asset_BadDebt':      '坏账净值',
    'Liability_Current':  '流动负债',
    'Liability_LongTerm': '长期负债',
    'Liability_Family':   '家庭内部负债',
}


# ══════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════

def load_balance_sheet(bs_path: str = BALANCE_SHEET_PATH,
                       portfolio_path: str = PORTFOLIO_PATH) -> pd.DataFrame:
    """加载 balance_sheet.csv，对 FUND_START_QUARTER 起的 Asset_Investment(auto)
    行自动从 portfolio.csv 聚合季末市值。

    Returns:
        DataFrame with all rows; CNY_Amount column is always numeric.
    """
    df = pd.read_csv(bs_path)
    df['CNY_Amount'] = pd.to_numeric(df['CNY_Amount'], errors='coerce').fillna(0.0)
    df['Amount']     = pd.to_numeric(df['Amount'],     errors='coerce').fillna(0.0)

    # 自动聚合 Asset_Investment 行（仅 sub_category == 'auto'，且季度 >= FUND_START_QUARTER）
    auto_mask = (
        (df['Category'] == 'Asset_Investment') &
        (df['Sub_Category'] == 'auto') &
        (df['Quarter'] >= FUND_START_QUARTER)
    )
    if auto_mask.any() and os.path.exists(portfolio_path):
        port = pd.read_csv(portfolio_path)
        port['Date'] = pd.to_datetime(port['Date'])
        for idx in df[auto_mask].index:
            quarter = df.at[idx, 'Quarter']
            year    = int(quarter[:4])
            q_num   = int(quarter[5])
            # 季末月份：Q1=3, Q2=6, Q3=9, Q4=12
            q_end_month = q_num * 3
            q_end = pd.Timestamp(year=year, month=q_end_month, day=1) + pd.offsets.MonthEnd(0)
            # 取季末当天或之前最近一个快照
            available = port[port['Date'] <= q_end]
            if not available.empty:
                snap_date = available['Date'].max()
                total = available[available['Date'] == snap_date]['Total_Value'].sum()
                df.at[idx, 'CNY_Amount'] = round(total, 2)
                df.at[idx, 'Amount']     = round(total, 2)

    return df


def load_cashflow_log(path: str = CASHFLOW_LOG_PATH) -> pd.DataFrame | None:
    """加载 cashflow_log.csv。不存在时返回 None。"""
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce').fillna(0.0)
    return df


def available_quarters(df: pd.DataFrame) -> list[str]:
    """返回 balance_sheet 中所有季度，按时间升序。"""
    return sorted(df['Quarter'].unique().tolist())


# ══════════════════════════════════════════════════════════════
# 核心计算
# ══════════════════════════════════════════════════════════════

def compute_net_worth(df: pd.DataFrame, quarter: str) -> dict:
    """计算指定季度的净资产和大类汇总。

    Returns:
        {
            'quarter': str,
            'total_assets': float,
            'total_liabilities': float,
            'net_worth': float,
            'debt_ratio': float,          # 总负债/总资产
            'current_ratio': float,       # 流动资产/流动负债（流动负债=0时返回None）
            'investment_ratio': float,    # 金融投资/总资产
            'asset_breakdown': dict,      # {category: amount}
            'liability_breakdown': dict,  # {category: amount}
        }
    """
    q = df[df['Quarter'] == quarter]

    asset_cats = [c for c in q['Category'].unique() if c.startswith('Asset')]
    liab_cats  = [c for c in q['Category'].unique() if c.startswith('Liability')]

    asset_breakdown = {}
    for cat in asset_cats:
        asset_breakdown[cat] = float(q[q['Category'] == cat]['CNY_Amount'].sum())

    liab_breakdown = {}
    for cat in liab_cats:
        liab_breakdown[cat] = float(q[q['Category'] == cat]['CNY_Amount'].sum())

    total_assets      = sum(asset_breakdown.values())
    total_liabilities = sum(liab_breakdown.values())
    net_worth         = total_assets - total_liabilities

    current_assets = asset_breakdown.get('Asset_Current', 0)
    current_liabs  = liab_breakdown.get('Liability_Current', 0)
    investment     = asset_breakdown.get('Asset_Investment', 0)

    return {
        'quarter':            quarter,
        'total_assets':       round(total_assets, 2),
        'total_liabilities':  round(total_liabilities, 2),
        'net_worth':          round(net_worth, 2),
        'debt_ratio':         round(total_liabilities / total_assets * 100, 2) if total_assets else 0,
        'current_ratio':      round(current_assets / current_liabs, 2) if current_liabs else None,
        'investment_ratio':   round(investment / total_assets * 100, 2) if total_assets else 0,
        'asset_breakdown':    {k: round(v, 2) for k, v in asset_breakdown.items()},
        'liability_breakdown':{k: round(v, 2) for k, v in liab_breakdown.items()},
    }


def compute_qoq(df: pd.DataFrame, q_prev: str, q_curr: str) -> dict:
    """计算两个相邻季度的 QoQ 变化。

    Returns:
        {
            'net_worth_prev': float,
            'net_worth_curr': float,
            'net_worth_delta': float,
            'net_worth_pct': float,
            'asset_delta': dict,      # {category: delta}
            'liability_delta': dict,
        }
    """
    prev = compute_net_worth(df, q_prev)
    curr = compute_net_worth(df, q_curr)

    nw_delta = curr['net_worth'] - prev['net_worth']
    nw_pct   = nw_delta / prev['net_worth'] * 100 if prev['net_worth'] else 0

    all_cats = set(list(prev['asset_breakdown']) + list(curr['asset_breakdown']))
    asset_delta = {
        cat: round(curr['asset_breakdown'].get(cat, 0) - prev['asset_breakdown'].get(cat, 0), 2)
        for cat in sorted(all_cats)
    }

    all_liab = set(list(prev['liability_breakdown']) + list(curr['liability_breakdown']))
    liab_delta = {
        cat: round(curr['liability_breakdown'].get(cat, 0) - prev['liability_breakdown'].get(cat, 0), 2)
        for cat in sorted(all_liab)
    }

    return {
        'q_prev':           q_prev,
        'q_curr':           q_curr,
        'net_worth_prev':   prev['net_worth'],
        'net_worth_curr':   curr['net_worth'],
        'net_worth_delta':  round(nw_delta, 2),
        'net_worth_pct':    round(nw_pct, 2),
        'total_assets_prev':   prev['total_assets'],
        'total_assets_curr':   curr['total_assets'],
        'total_liab_prev':     prev['total_liabilities'],
        'total_liab_curr':     curr['total_liabilities'],
        'asset_delta':      asset_delta,
        'liability_delta':  liab_delta,
    }


# ══════════════════════════════════════════════════════════════
# 格式化展示
# ══════════════════════════════════════════════════════════════

def generate_balance_sheet_table(df: pd.DataFrame, quarter: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """生成格式化的资产端和负债端 DataFrame（含小计行），用于 Dashboard 展示。

    Returns:
        (asset_df, liability_df)
        columns: 大类, 子类, 账户/项目, 金额(CNY)
    """
    q = df[df['Quarter'] == quarter].copy()
    q['Display_Category'] = q['Category'].map(CATEGORY_DISPLAY).fillna(q['Category'])

    def _build_side(categories):
        rows = []
        for cat in categories:
            cat_rows = q[q['Category'] == cat]
            display  = CATEGORY_DISPLAY.get(cat, cat)
            for _, r in cat_rows.iterrows():
                rows.append({
                    '大类':     display,
                    '子类':     r['Sub_Category'],
                    '账户/项目': r['Account'],
                    '金额(CNY)': r['CNY_Amount'],
                    'Notes':    r.get('Notes', ''),
                })
            # 小计行
            subtotal = cat_rows['CNY_Amount'].sum()
            rows.append({
                '大类':     display,
                '子类':     '── 小计',
                '账户/项目': '',
                '金额(CNY)': round(subtotal, 2),
                'Notes':    '',
            })
        return pd.DataFrame(rows)

    asset_cats = [c for c in ['Asset_Current', 'Asset_Investment', 'Asset_RealEstate',
                               'Asset_PrivateEquity', 'Asset_BadDebt']
                  if c in q['Category'].values]
    liab_cats  = [c for c in ['Liability_Current', 'Liability_LongTerm', 'Liability_Family']
                  if c in q['Category'].values]

    asset_df = _build_side(asset_cats)
    liab_df  = _build_side(liab_cats)

    return asset_df, liab_df


def generate_waterfall_data(qoq: dict) -> list[dict]:
    """生成净资产瀑布图数据（Plotly waterfall chart 格式）。

    Returns:
        list of {label, value, type}  type: 'absolute'|'relative'
    """
    data = [{'label': qoq['q_prev'] + ' 净资产', 'value': qoq['net_worth_prev'], 'type': 'absolute'}]

    # 资产变化分解
    cat_labels = {
        'Asset_Current':       '流动资产变化',
        'Asset_Investment':    '金融投资变化',
        'Asset_RealEstate':    '不动产/车辆变化',
        'Asset_PrivateEquity': '私募变化',
        'Asset_BadDebt':       '坏账净值变化',
    }
    for cat, label in cat_labels.items():
        delta = qoq['asset_delta'].get(cat, 0)
        if delta != 0:
            data.append({'label': label, 'value': delta, 'type': 'relative'})

    # 负债变化（负债增加→净资产减少，取反）
    liab_labels = {
        'Liability_Current':  '流动负债变化',
        'Liability_LongTerm': '长期负债变化',
        'Liability_Family':   '家庭内部负债变化',
    }
    for cat, label in liab_labels.items():
        delta = qoq['liability_delta'].get(cat, 0)
        if delta != 0:
            data.append({'label': label, 'value': -delta, 'type': 'relative'})

    data.append({'label': qoq['q_curr'] + ' 净资产', 'value': qoq['net_worth_curr'], 'type': 'absolute'})
    return data
