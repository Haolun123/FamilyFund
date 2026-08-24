"""historical_cost.py — 历史真实成本跟踪。

提供与「4月10日以来」口径并列的「历史真实浮盈」视角：
- 初始均价从 historical_cost.json 手动录入（含4月10日前所有历史买入）
- 每次保存快照后，用 portfolio.csv 的 NCF 记录自动滚动更新加权均价
- SAP 单独走实时盈亏平衡价（不在 json 里维护）

滚动均价公式（基于 portfolio.csv NCF）：
    当前均价 = (初始均价 × 初始份额 + 4月10日后所有正NCF之和) / 当前总份额

核心函数：
  load_historical_cost(data_dir)                       → dict
  save_historical_cost(data_dir, cost_map)             → None
  roll_historical_cost(data_dir, portfolio_df)         → dict  滚动更新并持久化
  compute_historical_pl(data_dir, portfolio_df, ...)   → DataFrame
"""

import json
import os
import math
import pandas as pd

# 建仓基准日：4月10日之前的成本用手动录入值，之后用 portfolio NCF 滚动
BASELINE_DATE = '2026-04-10'


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, 'historical_cost.json')


def load_historical_cost(data_dir: str) -> dict:
    """读取 historical_cost.json，返回 {code: {avg_cost, currency, name}} 字典。"""
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith('_')}
    except Exception:
        return {}


def save_historical_cost(data_dir: str, cost_map: dict) -> None:
    """写入 historical_cost.json（保留元信息字段，原子写入，写前备份到 backups/，保留10份）。"""
    import shutil
    path = _json_path(data_dir)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass
        # 写前备份
        try:
            backup_dir = os.path.join(data_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            ts = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
            shutil.copy2(path, os.path.join(backup_dir, f'historical_cost_{ts}.json'))
            # 只保留最近10份
            all_bk = sorted([
                f for f in os.listdir(backup_dir)
                if f.startswith('historical_cost_') and f.endswith('.json')
            ])
            for old in all_bk[:-10]:
                os.remove(os.path.join(backup_dir, old))
        except Exception:
            pass  # 备份失败不阻断写入

    meta = {k: v for k, v in existing.items() if k.startswith('_')}
    meta['_updated'] = pd.Timestamp.now().strftime('%Y-%m-%d')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({**meta, **cost_map}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def roll_historical_cost(data_dir: str, portfolio_df: pd.DataFrame) -> dict:
    """用 portfolio.csv 的 NCF 滚动更新各标的加权均价，持久化并返回新 map。

    滚动公式：
        初始总成本 = 手动录入均价 × 建仓日份额（BASELINE_DATE 当天 Shares）
        后续追加成本 = BASELINE_DATE 之后所有正 NCF 之和（买入净额，含手续费）
        当前总份额 = portfolio 最新 Shares
        当前均价 = (初始总成本 + 后续追加成本) / 当前总份额

    注意：
    - portfolio NCF 是人民币口径，对于港股/EUR资产，需要除以当期汇率还原原币成本
    - 这里统一用人民币口径的历史总成本，均价也折算人民币，保持一致
    """
    cost_map = load_historical_cost(data_dir)
    if not cost_map:
        return cost_map

    portfolio_df = portfolio_df.copy()
    # Code 可能被读成 int64（纯数字代码），统一转成补零的6位字符串
    portfolio_df['Code'] = portfolio_df['Code'].fillna('').astype(str).apply(
        lambda x: x.zfill(6) if x.isdigit() and len(x) <= 6 else x
    )
    portfolio_df['Date'] = pd.to_datetime(portfolio_df['Date']).dt.strftime('%Y-%m-%d')

    latest_date = portfolio_df['Date'].max()
    latest = portfolio_df[portfolio_df['Date'] == latest_date]
    baseline = portfolio_df[portfolio_df['Date'] == BASELINE_DATE]

    for code, entry in cost_map.items():
        avg_cost_raw = entry.get('avg_cost')
        if avg_cost_raw is None:
            continue
        try:
            avg_cost_raw = float(avg_cost_raw)
            if math.isnan(avg_cost_raw) or avg_cost_raw <= 0:
                continue
        except (TypeError, ValueError):
            continue

        currency = entry.get('currency', 'CNY')

        # 建仓日份额和汇率
        bl_rows = baseline[baseline['Code'] == code]
        if bl_rows.empty:
            continue
        bl_row = bl_rows.iloc[0]
        baseline_shares = float(bl_row.get('Shares', 0))
        baseline_fx = float(bl_row.get('Exchange_Rate', 1.0))

        # 当前份额
        cur_rows = latest[latest['Code'] == code]
        if cur_rows.empty:
            continue
        current_shares = float(cur_rows.iloc[0].get('Shares', 0))
        if current_shares <= 0:
            continue

        # 建仓日后所有正 NCF（买入净额，人民币口径，不含卖出）
        after_baseline = portfolio_df[
            (portfolio_df['Code'] == code) &
            (portfolio_df['Date'] > BASELINE_DATE) &
            (portfolio_df['Net_Cash_Flow'] > 0)
        ]
        post_ncf_cny = float(after_baseline['Net_Cash_Flow'].sum())

        # 初始总成本（人民币）= 手动均价（原币）× 建仓日汇率 × 建仓日份额
        if currency == 'CNY':
            baseline_cost_cny = avg_cost_raw * baseline_shares
        else:
            baseline_cost_cny = avg_cost_raw * baseline_fx * baseline_shares

        # 历史累计买入总成本（均价法：卖出不影响均价，分母用累计买入份额）
        total_cost_cny = baseline_cost_cny + post_ncf_cny

        # 累计买入份额（baseline + 4月10日后每次买入的份额增量）
        # 通过每次快照的份额变化推算：只取份额增加的周
        df_code = portfolio_df[portfolio_df['Code'] == code].sort_values('Date')
        df_code_after = df_code[df_code['Date'] > BASELINE_DATE].copy()
        df_code_after['shares_delta'] = df_code_after['Shares'].diff().fillna(0)
        # 第一行 delta 用当前 Shares - baseline_shares
        if not df_code_after.empty:
            first_idx = df_code_after.index[0]
            df_code_after.at[first_idx, 'shares_delta'] = (
                float(df_code_after.at[first_idx, 'Shares']) - baseline_shares
            )
        buy_shares_after = float(df_code_after[df_code_after['shares_delta'] > 0]['shares_delta'].sum())
        total_buy_shares = baseline_shares + buy_shares_after

        # 均价 = 总买入成本 / 总买入份额（卖出不影响均价）
        new_avg_cny = total_cost_cny / total_buy_shares if total_buy_shares > 0 else avg_cost_raw

        # 存回原币种均价
        cur_fx = float(cur_rows.iloc[0].get('Exchange_Rate', 1.0))
        if currency == 'CNY':
            entry['avg_cost'] = round(new_avg_cny, 6)
            entry.pop('_avg_cost_cny', None)  # CNY 标的不需要，avg_cost 即人民币均价
        else:
            entry['avg_cost'] = round(new_avg_cny / cur_fx, 6) if cur_fx > 0 else avg_cost_raw
            entry['_avg_cost_cny'] = round(new_avg_cny, 6)  # 仅非 CNY 标的缓存人民币均价
        entry['current_shares'] = round(current_shares, 6)  # 当前份额，冗余存储供外部使用

    save_historical_cost(data_dir, cost_map)
    return cost_map


def compute_historical_pl(
    data_dir: str,
    portfolio_df: pd.DataFrame,
    sap_break_even_eur: float | None = None,
) -> pd.DataFrame:
    """计算每个持仓的历史真实浮盈。

    Returns:
        DataFrame with columns:
            Code, Name, Asset_Class,
            Market_Value_CNY,
            Hist_Avg_Cost_CNY,    历史均价（人民币）
            Hist_Total_Cost_CNY,  历史总成本（人民币）
            Hist_PL_CNY,          历史浮盈（人民币）
            Hist_PL_Rate,         历史浮盈率（%）
    """
    # 只读取，不触发滚动更新（roll_historical_cost 由 Weekly Update 保存快照时统一触发）
    cost_map = load_historical_cost(data_dir)
    if not cost_map:
        return pd.DataFrame()

    latest_date = portfolio_df['Date'].max()
    latest = portfolio_df[portfolio_df['Date'] == latest_date].copy()
    latest['Code'] = latest['Code'].fillna('').astype(str).apply(
        lambda x: x.zfill(6) if x.isdigit() and len(x) <= 6 else x
    )

    rows = []

    for _, holding in latest.iterrows():
        code = str(holding.get('Code', ''))
        asset_class = holding.get('Asset_Class', '')
        shares = float(holding.get('Shares', 0))
        current_price = float(holding.get('Current_Price', 0))
        exchange_rate = float(holding.get('Exchange_Rate', 1.0))
        market_cny = shares * current_price * exchange_rate

        if shares <= 0:
            continue

        # SAP 单独处理
        if asset_class == 'Company_Stock':
            if sap_break_even_eur is None:
                continue
            hist_avg_cny = sap_break_even_eur * exchange_rate
            hist_cost_cny = hist_avg_cny * shares
            hist_pl_cny = market_cny - hist_cost_cny
            hist_pl_rate = hist_pl_cny / hist_cost_cny * 100 if hist_cost_cny > 0 else 0
            rows.append({
                'Code': code,
                'Name': holding.get('Name', ''),
                'Asset_Class': asset_class,
                'Market_Value_CNY': round(market_cny, 2),
                'Hist_Avg_Cost_CNY': round(hist_avg_cny, 4),
                'Hist_Total_Cost_CNY': round(hist_cost_cny, 2),
                'Hist_PL_CNY': round(hist_pl_cny, 2),
                'Hist_PL_Rate': round(hist_pl_rate, 2),
            })
            continue

        if code not in cost_map:
            continue

        entry = cost_map[code]
        avg_cost_raw = entry.get('avg_cost')
        if avg_cost_raw is None:
            continue
        try:
            avg_cost_raw = float(avg_cost_raw)
            if math.isnan(avg_cost_raw) or avg_cost_raw <= 0:
                continue
        except (TypeError, ValueError):
            continue

        currency = entry.get('currency', 'CNY')
        if currency == 'CNY':
            avg_cost_cny = avg_cost_raw
        else:
            # 非 CNY：优先用缓存的 _avg_cost_cny，没有则用 avg_cost × 实时汇率
            cached = entry.get('_avg_cost_cny')
            avg_cost_cny = float(cached) if cached is not None else avg_cost_raw * exchange_rate

        try:
            avg_cost_cny = float(avg_cost_cny)
            if math.isnan(avg_cost_cny) or avg_cost_cny <= 0:
                continue
        except (TypeError, ValueError):
            continue

        hist_cost_cny = avg_cost_cny * shares
        hist_pl_cny = market_cny - hist_cost_cny
        hist_pl_rate = hist_pl_cny / hist_cost_cny * 100 if hist_cost_cny > 0 else 0

        rows.append({
            'Code': code,
            'Name': holding.get('Name', ''),
            'Asset_Class': asset_class,
            'Market_Value_CNY': round(market_cny, 2),
            'Hist_Avg_Cost_CNY': round(avg_cost_cny, 4),
            'Hist_Total_Cost_CNY': round(hist_cost_cny, 2),
            'Hist_PL_CNY': round(hist_pl_cny, 2),
            'Hist_PL_Rate': round(hist_pl_rate, 2),
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()
