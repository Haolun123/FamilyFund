"""portfolio_stress_test.py — 组合历史压力测试（2026-05-23 新建）。

把当前 Asset_Class 权重套到历史数据，回测 2005-至今 21 年期间组合表现。
回答：「如果我 2008/2015/2020 年就用这个配置，会经历多大回撤？」

代理映射：
- Fixed_Income → 中国 10Y 国债收益率 + 1.5%（保守加点反映理财收益）
- US_Blend_Fund → 标普 500 总收益（^GSPC + 股息再投）
- US_Growth_Fund → 纳指 100（^NDX）
- CN_Index_Fund → 沪深 300（akshare）
- ETF_Stock → 沪深 300 代理（混合个股/ETF 简化）
- Gold → 黄金期货 GC=F（USD/oz × USD/CNY）
- Company_Stock → SAP.DE（EUR/法兰克福）
- Cash → 1.5%/年（货币基金代理）

输出：
- 累计回报 / CAGR
- 最大单年回撤、最大单年涨幅
- 最大滚动 1Y / 3Y 回撤
- 滚动 1Y 涨幅分位
- 与沪深 300 / 标普 500 基准对比
"""

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


# ── Asset_Class → 代理标的映射 ──

PROXY_MAP = {
    'Fixed_Income':    {'type': 'fixed_yield', 'rate': 0.04, 'name': '理财（10Y国债+1.5%）'},
    'US_Blend_Fund':   {'type': 'yfinance', 'symbol': '^GSPC', 'name': '标普500'},
    'US_Growth_Fund':  {'type': 'yfinance', 'symbol': '^NDX', 'name': '纳指100'},
    'CN_Index_Fund':   {'type': 'akshare_index', 'symbol': 'sh000300', 'name': '沪深300'},
    'ETF_Stock':       {'type': 'akshare_index', 'symbol': 'sh000300', 'name': '沪深300（个股混合代理）'},
    'Gold':            {'type': 'gold_cny', 'name': '黄金（GC=F × USD/CNY）'},
    'Company_Stock':   {'type': 'yfinance', 'symbol': 'SAP.DE', 'name': 'SAP'},
    'Cash':            {'type': 'fixed_yield', 'rate': 0.015, 'name': '货币基金'},
}


# ── 缓存 ──

def _cache_path(data_dir: str) -> str:
    return os.path.join(data_dir, 'stress_test_cache.json')


CACHE_TTL_DAYS = 30  # 历史数据更新慢，缓存 30 天


def _is_fresh(updated_str: str) -> bool:
    if not updated_str:
        return False
    try:
        ts = datetime.fromisoformat(updated_str)
    except Exception:
        return False
    return (datetime.now() - ts) < timedelta(days=CACHE_TTL_DAYS)


# ── 数据拉取 ──

def _fetch_yfinance_close(symbol: str, start: str = '2005-01-01') -> pd.Series | None:
    """拉 yfinance 收盘价（含分红再投，用 Adj Close）。"""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        hist = t.history(start=start, auto_adjust=True)  # auto_adjust 含拆分+分红再投
        if hist is None or len(hist) == 0:
            return None
        return hist['Close']
    except Exception as e:
        print(f'[stress_test] yfinance {symbol} 失败: {e}')
        return None


def _fetch_akshare_index_close(symbol: str, start: str = '2005-01-01') -> pd.Series | None:
    """拉 akshare A 股指数收盘价（如 sh000300=沪深300）。"""
    try:
        import akshare as ak
        # 沪深300 是 sh000300
        if symbol.startswith('sh'):
            code = symbol[2:]
            df = ak.stock_zh_index_daily(symbol=symbol)
            if df is None or len(df) == 0:
                return None
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            df = df[df.index >= start]
            return df['close']
        return None
    except Exception as e:
        print(f'[stress_test] akshare {symbol} 失败: {e}')
        return None


def _fetch_gold_cny(start: str = '2005-01-01') -> pd.Series | None:
    """黄金 USD/oz → CNY/g。"""
    try:
        import yfinance as yf
        gc = yf.Ticker('GC=F').history(start=start, auto_adjust=True)
        usd_cny = yf.Ticker('USDCNY=X').history(start=start, auto_adjust=True)
        if gc is None or len(gc) == 0:
            return None
        gc_close = gc['Close']
        # 如果 USDCNY 拉不到（早期可能没数据），用 6.8 常数代理
        if usd_cny is None or len(usd_cny) == 0:
            cny = pd.Series(6.8, index=gc_close.index)
        else:
            cny = usd_cny['Close'].reindex(gc_close.index, method='ffill').fillna(6.8)
        return gc_close * cny / 31.1035  # USD/oz × CNY/USD ÷ g/oz = CNY/g
    except Exception as e:
        print(f'[stress_test] 黄金 失败: {e}')
        return None


def _make_fixed_yield_series(rate: float, dates: pd.DatetimeIndex) -> pd.Series:
    """生成固定年化收益的"净值"序列（每日复利）。"""
    daily_return = (1 + rate) ** (1 / 252) - 1
    n = len(dates)
    nav = np.cumprod(np.full(n, 1 + daily_return))
    return pd.Series(nav, index=dates)


def fetch_proxy_series(asset_class: str, start: str = '2005-01-01') -> pd.Series | None:
    """根据 PROXY_MAP 拉单个资产类的代理序列。"""
    cfg = PROXY_MAP.get(asset_class)
    if not cfg:
        return None
    t = cfg['type']
    if t == 'yfinance':
        return _fetch_yfinance_close(cfg['symbol'], start)
    if t == 'akshare_index':
        return _fetch_akshare_index_close(cfg['symbol'], start)
    if t == 'gold_cny':
        return _fetch_gold_cny(start)
    if t == 'fixed_yield':
        # 需要外部传入 dates 才能生成；返回 None，由调用方处理
        return None
    return None


# ── 组合回测 ──

def _normalize_to_returns(series: pd.Series) -> pd.Series:
    """价格 → 日收益率。"""
    return series.pct_change().dropna()


def run_stress_test(weights: dict, start: str = '2005-01-01',
                    data_dir: str = None, force_refresh: bool = False) -> dict:
    """组合历史压力测试。

    Args:
        weights: {asset_class: weight}, 权重和 = 1
        start: 起始日（默认 2005-01-01）
        data_dir: 缓存目录
        force_refresh: 强制重拉

    Returns:
        {
            'portfolio_nav': pd.Series,         # 组合每日累计净值
            'cumulative_return': float,          # 总累计 %
            'years': float,                      # 年限
            'cagr': float,                       # 年化复利 %
            'max_drawdown': float,               # 最大回撤 %（任意时点）
            'yearly_returns': dict,              # {year: return_pct}
            'best_year': (year, return_pct),
            'worst_year': (year, return_pct),
            'rolling_1y_min': float,             # 滚动 1Y 最差
            'rolling_1y_max': float,
            'rolling_3y_min': float,
            'rolling_3y_max': float,
            'rolling_1y_quantiles': dict,        # P10/P50/P90 滚动 1Y
            'data_start': str, 'data_end': str,
            'proxies_used': dict,                # {asset_class: proxy_name}
        }
    """
    # 1. 拉所有非 fixed_yield 序列
    fetched = {}
    for ac, w in weights.items():
        if w <= 0:
            continue
        cfg = PROXY_MAP.get(ac)
        if not cfg:
            continue
        if cfg['type'] != 'fixed_yield':
            s = fetch_proxy_series(ac, start)
            if s is not None and len(s) > 0:
                fetched[ac] = s

    if not fetched:
        # 全是 fixed_yield 类型（无外部数据），生成人造日期序列
        end_date = pd.Timestamp.today().normalize()
        common_idx = pd.date_range(start, end_date, freq='B')
    else:
        # 2. 求公共日期范围
        common_idx = None
        for s in fetched.values():
            # 去时区
            if s.index.tz is not None:
                s.index = s.index.tz_localize(None)
            common_idx = s.index if common_idx is None else common_idx.intersection(s.index)
        if common_idx is None or len(common_idx) < 252:
            return {'error': f'公共日期不足 1 年（{len(common_idx) if common_idx is not None else 0} 天）'}

    # 3. 各代理对齐到公共日期，转日收益
    daily_returns = pd.DataFrame(index=common_idx)
    for ac, s in fetched.items():
        s_aligned = s.reindex(common_idx).ffill()
        daily_returns[ac] = s_aligned.pct_change().fillna(0)

    # 4. fixed_yield 类型的资产：直接用日复利
    for ac, w in weights.items():
        if w <= 0:
            continue
        cfg = PROXY_MAP.get(ac)
        if cfg and cfg['type'] == 'fixed_yield':
            rate = cfg['rate']
            daily_r = (1 + rate) ** (1 / 252) - 1
            daily_returns[ac] = daily_r

    # 5. 加权日收益
    w_series = pd.Series(weights)
    # 只保留有数据的列
    cols = [c for c in daily_returns.columns if c in w_series.index]
    w_filtered = w_series[cols]
    w_normalized = w_filtered / w_filtered.sum()  # 重新归一化（防止某类无数据）
    portfolio_daily = (daily_returns[cols] * w_normalized).sum(axis=1)

    # 6. 净值序列
    nav = (1 + portfolio_daily).cumprod()

    # 7. 累计 / CAGR
    total_return = nav.iloc[-1] - 1
    years = len(nav) / 252
    cagr = (nav.iloc[-1]) ** (1 / years) - 1 if years > 0 else 0

    # 8. 最大回撤
    peak = nav.cummax()
    dd = (nav - peak) / peak
    max_dd = float(dd.min())

    # 9. 年度回报
    nav_year_end = nav.resample('YE').last()
    yearly_returns = {}
    for i in range(1, len(nav_year_end)):
        year = int(nav_year_end.index[i].year)
        ret = float(nav_year_end.iloc[i] / nav_year_end.iloc[i-1] - 1)
        yearly_returns[year] = ret

    if yearly_returns:
        sorted_years = sorted(yearly_returns.items(), key=lambda x: x[1])
        worst_year = sorted_years[0]
        best_year = sorted_years[-1]
    else:
        worst_year = best_year = (None, 0)

    # 10. 滚动窗口
    rolling_1y = nav.pct_change(252).dropna()
    rolling_3y_cagr = (nav / nav.shift(252 * 3)).dropna() ** (1 / 3) - 1

    # 滚动 1Y 最大回撤（每个 252 天窗口的最大回撤）
    rolling_1y_min = float(rolling_1y.min()) if len(rolling_1y) > 0 else 0
    rolling_1y_max = float(rolling_1y.max()) if len(rolling_1y) > 0 else 0
    rolling_3y_min = float(rolling_3y_cagr.min()) if len(rolling_3y_cagr) > 0 else 0
    rolling_3y_max = float(rolling_3y_cagr.max()) if len(rolling_3y_cagr) > 0 else 0

    return {
        'portfolio_nav':           nav,
        'cumulative_return':       float(total_return),
        'years':                   years,
        'cagr':                    float(cagr),
        'max_drawdown':            max_dd,
        'yearly_returns':          yearly_returns,
        'best_year':               best_year,
        'worst_year':              worst_year,
        'rolling_1y_min':          rolling_1y_min,
        'rolling_1y_max':          rolling_1y_max,
        'rolling_3y_min':          rolling_3y_min,
        'rolling_3y_max':          rolling_3y_max,
        'rolling_1y_quantiles':    {
            'p10': float(rolling_1y.quantile(0.10)),
            'p50': float(rolling_1y.quantile(0.50)),
            'p90': float(rolling_1y.quantile(0.90)),
        } if len(rolling_1y) > 0 else None,
        'data_start':              str(nav.index[0].date()),
        'data_end':                str(nav.index[-1].date()),
        'proxies_used':            {ac: PROXY_MAP[ac]['name'] for ac in fetched.keys()},
        'normalized_weights':      w_normalized.to_dict(),
        'missing_assets':          [ac for ac in weights if ac not in cols and weights[ac] > 0
                                    and PROXY_MAP.get(ac, {}).get('type') != 'fixed_yield'],
    }


def get_current_weights(raw_df) -> dict:
    """从 portfolio.csv 算当前 Asset_Class 权重（含 Cash）。"""
    if raw_df is None or len(raw_df) == 0:
        return {}
    latest_date = raw_df['Date'].max()
    latest = raw_df[raw_df['Date'] == latest_date]
    by_class = latest.groupby('Asset_Class')['Total_Value'].sum()
    total = by_class.sum()
    if total <= 0:
        return {}
    return (by_class / total).to_dict()
