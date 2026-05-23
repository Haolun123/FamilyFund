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


def fetch_all_proxies(asset_classes: list = None, start: str = '2005-01-01') -> dict:
    """批量拉所有非 fixed_yield 资产类的代理价格序列。

    Args:
        asset_classes: 想拉的类别列表（None = 全部 PROXY_MAP）
        start: 起始日

    Returns:
        {asset_class: pd.Series}（仅成功的）
    """
    if asset_classes is None:
        asset_classes = list(PROXY_MAP.keys())
    fetched = {}
    for ac in asset_classes:
        cfg = PROXY_MAP.get(ac)
        if not cfg or cfg['type'] == 'fixed_yield':
            continue
        s = fetch_proxy_series(ac, start)
        if s is not None and len(s) > 0:
            # 去时区
            if s.index.tz is not None:
                s.index = s.index.tz_localize(None)
            fetched[ac] = s
    return fetched


def _build_daily_returns(fetched: dict, start: str) -> pd.DataFrame | None:
    """从已拉的价格序列构建公共日期 DataFrame 的日收益。"""
    if not fetched:
        # 无外部数据，构造人造日期
        end_date = pd.Timestamp.today().normalize()
        idx = pd.date_range(start, end_date, freq='B')
        return pd.DataFrame(index=idx)

    common_idx = None
    for s in fetched.values():
        common_idx = s.index if common_idx is None else common_idx.intersection(s.index)
    if common_idx is None or len(common_idx) < 252:
        return None

    daily_returns = pd.DataFrame(index=common_idx)
    for ac, s in fetched.items():
        s_aligned = s.reindex(common_idx).ffill()
        daily_returns[ac] = s_aligned.pct_change().fillna(0)
    return daily_returns


def compute_portfolio_metrics(daily_returns: pd.DataFrame, weights: dict) -> dict:
    """从日收益 DataFrame + 权重 dict 算组合指标（不拉数据，纯计算）。

    fixed_yield 类型直接添加日收益列。
    """
    if daily_returns is None or len(daily_returns) == 0:
        return {'error': '日收益数据为空'}

    dr = daily_returns.copy()

    # fixed_yield 类型：直接日复利
    for ac, w in weights.items():
        if w <= 0:
            continue
        cfg = PROXY_MAP.get(ac)
        if cfg and cfg['type'] == 'fixed_yield':
            rate = cfg['rate']
            daily_r = (1 + rate) ** (1 / 252) - 1
            dr[ac] = daily_r

    # 加权
    w_series = pd.Series(weights)
    cols = [c for c in dr.columns if c in w_series.index and w_series[c] > 0]
    if not cols:
        return {'error': '所有权重为 0 或无对应数据'}

    w_filtered = w_series[cols]
    w_normalized = w_filtered / w_filtered.sum()
    portfolio_daily = (dr[cols] * w_normalized).sum(axis=1)

    # 净值
    nav = (1 + portfolio_daily).cumprod()

    # 累计 / CAGR
    total_return = nav.iloc[-1] - 1
    years = len(nav) / 252
    cagr = (nav.iloc[-1]) ** (1 / years) - 1 if years > 0 else 0

    # 最大回撤
    peak = nav.cummax()
    dd = (nav - peak) / peak
    max_dd = float(dd.min())

    # 年度回报
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

    # 滚动窗口
    rolling_1y = nav.pct_change(252).dropna()
    rolling_3y_cagr = (nav / nav.shift(252 * 3)).dropna() ** (1 / 3) - 1
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
        'normalized_weights':      w_normalized.to_dict(),
    }


# ── 组合回测 ──

def _normalize_to_returns(series: pd.Series) -> pd.Series:
    """价格 → 日收益率。"""
    return series.pct_change().dropna()


def run_stress_test(weights: dict, start: str = '2005-01-01',
                    data_dir: str = None, force_refresh: bool = False) -> dict:
    """组合历史压力测试（向后兼容包装器）。

    新代码建议用 fetch_all_proxies + compute_portfolio_metrics 分步调用。

    Args:
        weights: {asset_class: weight}, 权重和 = 1
        start: 起始日（默认 2005-01-01）

    Returns:
        组合压力测试结果（含 portfolio_nav, cagr, max_drawdown 等）
    """
    # 1. 拉所有非 fixed_yield 序列
    classes_with_weight = [ac for ac, w in weights.items() if w > 0]
    fetched = fetch_all_proxies(asset_classes=classes_with_weight, start=start)

    # 2. 构建日收益 DataFrame
    daily_returns = _build_daily_returns(fetched, start)
    if daily_returns is None:
        return {'error': '公共日期不足 1 年'}

    # 3. 算组合
    result = compute_portfolio_metrics(daily_returns, weights)
    if 'error' in result:
        return result

    # 4. 加 metadata（向后兼容）
    result['proxies_used'] = {ac: PROXY_MAP[ac]['name'] for ac in fetched.keys()}
    result['missing_assets'] = [
        ac for ac in weights if ac not in result.get('normalized_weights', {})
        and weights[ac] > 0 and PROXY_MAP.get(ac, {}).get('type') != 'fixed_yield'
    ]
    return result


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


def compute_target_weights(raw_df, reports_dir: str,
                           smart_beta_target_cny: float = 150_000.0) -> dict:
    """计算「调仓完成后」的目标 Asset_Class 权重。

    逻辑：
    - 个股池目标合计：从 decisions.json 的 target_position 字段（核心+卫星 tier）求和
    - Smart Beta（红利低波 ETF）目标：smart_beta_target_cny（C1' 决策默认 15 万）
    - ETF_Stock 类目标 = 个股池目标 + Smart Beta 目标
    - 资金来源：Fixed_Income 类（从 FI 抽出资金给 ETF_Stock）
    - 其他类（Gold/US_Blend/US_Growth/CN_Index/Cash/Company_Stock）保持当前金额不变

    Args:
        raw_df: portfolio.csv DataFrame
        reports_dir: Finance Reports 目录
        smart_beta_target_cny: 红利低波 ETF 目标金额（默认 15 万，P10 决策）

    Returns:
        {asset_class: weight}, 权重和 = 1
    """
    if raw_df is None or len(raw_df) == 0:
        return {}

    # 1. 当前各类总市值
    latest_date = raw_df['Date'].max()
    latest = raw_df[raw_df['Date'] == latest_date]
    current_value_by_class = latest.groupby('Asset_Class')['Total_Value'].sum().to_dict()
    total = sum(current_value_by_class.values())
    if total <= 0:
        return {}

    # 2. 算个股池目标合计（从 decisions.json）
    try:
        from research_library import get_position_summary
        from research_library import _pace_to_target_amount
        summary = get_position_summary(reports_dir, raw_df=raw_df)
        pool_target_cny = 0
        for r in summary:
            tier = r.get('tier', '')
            if tier not in ('核心', '卫星'):
                continue
            target_str = r.get('target_position', '')
            low, high = _pace_to_target_amount(target_str)
            if low is not None:
                # 用区间中点
                pool_target_cny += (low + high) / 2
    except Exception:
        pool_target_cny = 0

    # 3. ETF_Stock 类目标 = 个股池 + Smart Beta
    etf_stock_target = pool_target_cny + smart_beta_target_cny

    # 4. 算资金缺口：ETF_Stock 增加多少 → 从 Fixed_Income 抽多少
    etf_stock_current = current_value_by_class.get('ETF_Stock', 0)
    fi_current = current_value_by_class.get('Fixed_Income', 0)
    delta = etf_stock_target - etf_stock_current

    # 5. 构造目标值
    target_value_by_class = dict(current_value_by_class)
    target_value_by_class['ETF_Stock'] = etf_stock_target
    target_value_by_class['Fixed_Income'] = max(0, fi_current - delta)

    # 6. 归一化为权重
    target_total = sum(target_value_by_class.values())
    return {k: v / target_total for k, v in target_value_by_class.items()}


def compare_portfolios(current_weights: dict, target_weights: dict,
                       start: str = '2005-01-01') -> dict:
    """对比当前 vs 目标组合的压力测试。

    Returns:
        {
            'current': stress_test_result,
            'target':  stress_test_result,
            'diff': {
                'cagr_pp': float,           # CAGR 百分点差
                'mdd_pp': float,            # 最大回撤百分点差（正=变差）
                'worst_year_pp': float,
                'p10_1y_pp': float,
            }
        }
    """
    cur_result = run_stress_test(current_weights, start=start)
    tgt_result = run_stress_test(target_weights, start=start)

    diff = {}
    if 'error' not in cur_result and 'error' not in tgt_result:
        diff = {
            'cagr_pp':       (tgt_result['cagr'] - cur_result['cagr']) * 100,
            'mdd_pp':        (tgt_result['max_drawdown'] - cur_result['max_drawdown']) * 100,
            'worst_year_pp': (tgt_result['worst_year'][1] - cur_result['worst_year'][1]) * 100,
        }
        cur_p10 = cur_result.get('rolling_1y_quantiles', {}).get('p10')
        tgt_p10 = tgt_result.get('rolling_1y_quantiles', {}).get('p10')
        if cur_p10 is not None and tgt_p10 is not None:
            diff['p10_1y_pp'] = (tgt_p10 - cur_p10) * 100

    return {
        'current': cur_result,
        'target':  tgt_result,
        'diff':    diff,
    }
