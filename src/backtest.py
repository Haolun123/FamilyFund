"""backtest.py — DCA 定投策略回测引擎

对比「固定金额定投」与「PE×VIX/QVIX 或 MA200×VIX 矩阵策略」的历史表现。

支持标的：
  - csi300    沪深300       PE×QVIX 百分位矩阵（akshare，2015起）
  - csi_a500  中证A500      PE×QVIX 百分位矩阵（akshare，2015起）
  - sp500     标普500       PE×VIX 矩阵（Shiller Yale Excel，~2000起）
  - ndx100    纳指100       PE×VIX 矩阵（⚠️ PE 使用标普500代理，见局限说明）
  - gold      黄金 (USD/oz) MA200乖离率×VIX 矩阵（yfinance，~2000起）

缓存：$FAMILYFUND_DATA/backtest_cache.json，当天已更新则不重新拉取。
"""

import json
import os
import sys
from datetime import date, timedelta

import pandas as pd
from scipy.optimize import brentq

# ── sibling imports ───────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from market_monitor import (
    lookup_multiplier,
    lookup_a_share_multiplier,
    lookup_gold_multiplier,
)
from nav_engine import _compute_max_drawdown_series

# ── Cache ─────────────────────────────────────────────────────
_DATA_DIR = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'),
)
CACHE_PATH = os.path.join(_DATA_DIR, 'backtest_cache.json')

SHILLER_URL = 'http://www.econ.yale.edu/~shiller/data/ie_data.xls'

_MA200_WARMUP_DAYS = 300   # fetch extra days before start_date for gold MA200


# ══════════════════════════════════════════════════════════════
# Cache I/O  (same pattern as benchmark.py)
# ══════════════════════════════════════════════════════════════

def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)


def _cache_is_fresh(cache: dict, key: str) -> bool:
    ts = cache.get(f'{key}_updated')
    return bool(ts and ts == date.today().isoformat())


# ══════════════════════════════════════════════════════════════
# Raw fetchers — return list[dict] for JSON serialisation
# ══════════════════════════════════════════════════════════════

def _fetch_price_history(target: str, start_date: str) -> list[dict] | None:
    """拉取日线收盘价，返回 [{'date': 'YYYY-MM-DD', 'value': float}]。"""
    try:
        if target in ('csi300', 'csi_a500'):
            import akshare as ak
            sym = 'sh000300' if target == 'csi300' else 'sh000510'
            df = ak.stock_zh_index_daily(symbol=sym)
            df = df[['date', 'close']].rename(columns={'close': 'value'})
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            df = df[df['date'] >= start_date]
        else:
            import yfinance as yf
            sym = {'gold': 'GC=F', 'sp500': '^GSPC', 'ndx100': '^NDX'}[target]
            raw = yf.download(sym, start=start_date, progress=False, auto_adjust=True)
            if raw.empty:
                return None
            close = raw['Close'].squeeze()
            df = pd.DataFrame({'date': close.index.strftime('%Y-%m-%d'), 'value': close.values})
        return df.dropna(subset=['value']).to_dict('records')
    except Exception as e:
        print(f'[backtest] price fetch failed for {target}: {e}')
        return None


def _fetch_pe_shiller(start_date: str) -> list[dict] | None:
    """Shiller Yale Excel → 月频 Trailing PE（P/E，非 CAPE）。
    返回 [{'date': 'YYYY-MM-01', 'value': float}]。
    """
    try:
        df = pd.read_excel(SHILLER_URL, sheet_name='Data', header=7)
        df.columns = [str(c).strip() for c in df.columns]

        # 解析 Shiller 日期格式：1871.01 = 1871年1月
        def _parse_date(d):
            try:
                f = float(d)
                year = int(f)
                month = round((f - year) * 100)
                if month == 0:
                    month = 1
                if not 1 <= month <= 12:
                    return None
                return f'{year:04d}-{month:02d}-01'
            except (ValueError, TypeError):
                return None

        df['date'] = df['Date'].apply(_parse_date)
        df = df.dropna(subset=['date'])

        # Trailing PE = P / E
        # E（12个月滚动收益）最近几个月可能为 NaN（Shiller 数据滞后），
        # 用 forward-fill 填补：收益变化缓慢，用上一个月值代替合理
        p_col = pd.to_numeric(df['P'], errors='coerce')
        e_col = pd.to_numeric(df['E'], errors='coerce').ffill()
        df['value'] = p_col / e_col
        df = df[df['value'] > 0].dropna(subset=['value'])
        df = df[df['date'] >= start_date][['date', 'value']]
        return df.to_dict('records')
    except Exception as e:
        print(f'[backtest] Shiller PE fetch failed: {e}')
        return None


def _fetch_pe_akshare(symbol: str, start_date: str) -> list[dict] | None:
    """akshare A股 PE 历史，返回 [{'date': 'YYYY-MM-DD', 'value': float}]。"""
    try:
        import akshare as ak
        df = ak.stock_index_pe_lg(symbol=symbol)
        # 列名通常为 ['日期', '滚动市盈率', ...]
        date_col = df.columns[0]
        pe_col   = df.columns[1]
        df = df[[date_col, pe_col]].rename(columns={date_col: 'date', pe_col: 'value'})
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        df = df.dropna(subset=['value'])
        df = df[df['date'] >= start_date]
        return df.to_dict('records')
    except Exception as e:
        print(f'[backtest] akshare PE fetch failed for {symbol}: {e}')
        return None


def _fetch_vix_history(start_date: str) -> list[dict] | None:
    """yfinance ^VIX 日线，返回 [{'date': 'YYYY-MM-DD', 'value': float}]。"""
    try:
        import yfinance as yf
        raw = yf.download('^VIX', start=start_date, progress=False, auto_adjust=True)
        if raw.empty:
            return None
        close = raw['Close'].squeeze()
        df = pd.DataFrame({'date': close.index.strftime('%Y-%m-%d'), 'value': close.values})
        return df.dropna(subset=['value']).to_dict('records')
    except Exception as e:
        print(f'[backtest] VIX fetch failed: {e}')
        return None


def _fetch_qvix_history(start_date: str) -> list[dict] | None:
    """akshare QVIX 全历史，返回 [{'date': 'YYYY-MM-DD', 'value': float}]。"""
    try:
        import akshare as ak
        df = ak.index_option_300etf_qvix()
        df = df[['date', 'close']].rename(columns={'close': 'value'})
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        df = df.dropna(subset=['value'])
        df = df[df['date'] >= start_date]
        return df.to_dict('records')
    except Exception as e:
        print(f'[backtest] QVIX fetch failed: {e}')
        return None


# ══════════════════════════════════════════════════════════════
# Cached public getters
# ══════════════════════════════════════════════════════════════

def _records_to_series(records: list[dict]) -> pd.Series:
    """list[{'date', 'value'}] → pd.Series(float, index=DatetimeIndex)。"""
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    return pd.Series(df['value'].values, index=df['date']).sort_index()


def get_price_series(target: str, start_date: str) -> pd.Series | None:
    cache = _load_cache()
    key = f'bt_price_{target}'
    if not _cache_is_fresh(cache, key):
        records = _fetch_price_history(target, start_date)
        if records:
            cache[key] = records
            cache[f'{key}_updated'] = date.today().isoformat()
            _save_cache(cache)
    records = cache.get(key)
    if not records:
        return None
    s = _records_to_series(records)
    return s[s.index >= pd.Timestamp(start_date)]


def get_pe_series(target: str, start_date: str) -> pd.Series | None:
    """gold → None；ndx100 → 标普500 Shiller PE（代理）；其余各自获取。"""
    if target == 'gold':
        return None

    if target in ('sp500', 'ndx100'):
        cache = _load_cache()
        key = 'bt_pe_shiller'
        if not _cache_is_fresh(cache, key):
            records = _fetch_pe_shiller(start_date)
            if records:
                cache[key] = records
                cache[f'{key}_updated'] = date.today().isoformat()
                _save_cache(cache)
        records = cache.get(key)
        if not records:
            return None
        s = _records_to_series(records)
        return s[s.index >= pd.Timestamp(start_date)]

    # A股
    symbol_map = {'csi300': '沪深300', 'csi_a500': '中证500'}
    symbol = symbol_map[target]
    cache = _load_cache()
    key = f'bt_pe_{target}'
    if not _cache_is_fresh(cache, key):
        records = _fetch_pe_akshare(symbol, start_date)
        if records:
            cache[key] = records
            cache[f'{key}_updated'] = date.today().isoformat()
            _save_cache(cache)
    records = cache.get(key)
    if not records:
        return None
    s = _records_to_series(records)
    return s[s.index >= pd.Timestamp(start_date)]


def get_vol_series(target: str, start_date: str) -> pd.Series | None:
    """A股 → QVIX；其余 → VIX。"""
    cache = _load_cache()
    if target in ('csi300', 'csi_a500'):
        key = 'bt_qvix'
        if not _cache_is_fresh(cache, key):
            records = _fetch_qvix_history(start_date)
            if records:
                cache[key] = records
                cache[f'{key}_updated'] = date.today().isoformat()
                _save_cache(cache)
        records = cache.get(key)
    else:
        key = 'bt_vix'
        if not _cache_is_fresh(cache, key):
            records = _fetch_vix_history(start_date)
            if records:
                cache[key] = records
                cache[f'{key}_updated'] = date.today().isoformat()
                _save_cache(cache)
        records = cache.get(key)
    if not records:
        return None
    s = _records_to_series(records)
    return s[s.index >= pd.Timestamp(start_date)]


# ══════════════════════════════════════════════════════════════
# Utilities
# ══════════════════════════════════════════════════════════════

def _parse_multiplier(raw: str | None, top_mult: float) -> float:
    """'0.3x'→0.3  '顶格'→top_mult  其余→0.0（包括暂停/—/None）"""
    if raw == '顶格':
        return top_mult
    if raw is None or raw in ('暂停', '—', ''):
        return 0.0
    try:
        return float(str(raw).rstrip('x'))
    except (ValueError, AttributeError):
        return 0.0


def _series_asof(series: pd.Series | None, dt: pd.Timestamp) -> float | None:
    """安全 asof 查找：返回 dt 当天或之前最近的值，无数据返回 None。"""
    if series is None or series.empty:
        return None
    s = series.sort_index()
    idx = s.index.asof(dt)
    if pd.isnull(idx):
        return None
    val = s[idx]
    return None if pd.isnull(val) else float(val)


def _xirr(dates: list, amounts: list, final_value: float) -> float | None:
    """独立 XIRR 计算（复用 nav_engine.compute_xirr 核心逻辑）。

    amounts[i]：第 i 期投入金额（正数，现金流出）
    final_value：终值（现金流入）
    """
    if not dates or not amounts or final_value <= 0:
        return None
    cashflows = list(amounts)
    cashflows[-1] -= final_value   # 最后一期扣除终值 = 净流出

    if all(cf == 0 for cf in cashflows):
        return None

    t0 = pd.Timestamp(dates[0])
    years = [(pd.Timestamp(d) - t0).days / 365.0 for d in dates]

    def npv(rate):
        return sum(cf / (1 + rate) ** y for cf, y in zip(cashflows, years))

    try:
        return round(brentq(npv, -0.999, 100.0, maxiter=1000), 6)
    except (ValueError, RuntimeError):
        return None


# ══════════════════════════════════════════════════════════════
# Core simulation
# ══════════════════════════════════════════════════════════════

def _run_simulation(
    monthly_dates: list,
    price_series: pd.Series,
    pe_series: pd.Series | None,
    vol_series: pd.Series | None,
    target: str,
    base_amount: float,
    top_mult: float,
) -> pd.DataFrame:
    """
    按月模拟固定策略和矩阵策略，返回逐期明细 DataFrame。

    gold 标的：pe_series=None，MA200 从 price_series 滚动计算。
    ndx100：pe_series 为标普500 Shiller PE（代理）。
    """
    is_gold = (target == 'gold')
    is_a_share = target in ('csi300', 'csi_a500')

    # 黄金：预先计算全历史 MA200，避免循环内重复计算
    if is_gold:
        ma200_series = price_series.rolling(200).mean()
    else:
        ma200_series = None

    records = []
    fixed_cum_shares  = 0.0
    fixed_cum_cost    = 0.0
    matrix_cum_shares = 0.0
    matrix_cum_cost   = 0.0

    for dt in monthly_dates:
        dt = pd.Timestamp(dt)

        price = _series_asof(price_series, dt)
        if price is None or price <= 0:
            continue

        vol = _series_asof(vol_series, dt)

        if is_gold:
            ma200 = _series_asof(ma200_series, dt)
            pe_or_bias = ((price - ma200) / ma200 * 100) if (ma200 and ma200 > 0) else None
            raw_mult = lookup_gold_multiplier(pe_or_bias, vol)
        else:
            pe_or_bias = _series_asof(pe_series, dt)
            if is_a_share:
                raw_mult = lookup_a_share_multiplier(pe_or_bias, vol, target)
            else:
                raw_mult = lookup_multiplier(pe_or_bias, vol, target)

        mult = _parse_multiplier(raw_mult, top_mult)

        # 固定策略：每期都买 base_amount
        fixed_shares_bought  = base_amount / price
        fixed_cum_shares    += fixed_shares_bought
        fixed_cum_cost      += base_amount
        fixed_cum_value      = fixed_cum_shares * price

        # 矩阵策略：按倍数买入（暂停=0x 则不投入）
        matrix_amount        = base_amount * mult
        matrix_shares_bought = (matrix_amount / price) if mult > 0 else 0.0
        matrix_cum_shares   += matrix_shares_bought
        matrix_cum_cost     += matrix_amount
        matrix_cum_value     = matrix_cum_shares * price

        records.append({
            'date':              dt.strftime('%Y-%m-%d'),
            'price':             round(price, 4),
            'pe_or_bias':        round(pe_or_bias, 2) if pe_or_bias is not None else None,
            'vol':               round(vol, 2) if vol is not None else None,
            'raw_mult':          raw_mult,
            'multiplier':        mult,
            'fixed_amount':      round(base_amount, 2),
            'fixed_cum_shares':  round(fixed_cum_shares, 4),
            'fixed_cum_cost':    round(fixed_cum_cost, 2),
            'fixed_cum_value':   round(fixed_cum_value, 2),
            'matrix_amount':     round(matrix_amount, 2),
            'matrix_cum_shares': round(matrix_cum_shares, 4),
            'matrix_cum_cost':   round(matrix_cum_cost, 2),
            'matrix_cum_value':  round(matrix_cum_value, 2),
        })

    return pd.DataFrame(records)


# ══════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════

def run_backtest(
    target: str,
    start_date: str,
    base_amount: float,
    freq: str = 'M',
    top_multiplier: float = 10.0,
) -> dict:
    """
    运行 DCA 回测，返回固定策略 vs 矩阵策略的对比结果。

    Args:
        target:          'csi300' / 'csi_a500' / 'sp500' / 'ndx100' / 'gold'
        start_date:      'YYYY-MM-DD'，回测起始日
        base_amount:     每期基准定投金额
        freq:            'M'（月频）
        top_multiplier:  '顶格'对应的实际倍数

    Returns:
        {
            'fixed':        {'total_cost', 'final_value', 'profit_loss', 'xirr', 'max_drawdown', 'periods'},
            'matrix':       {'total_cost', 'final_value', 'profit_loss', 'xirr', 'max_drawdown', 'periods'},
            'history':      pd.DataFrame,   # 逐期明细
            'target':       str,
            'start_date':   str,
            'base_amount':  float,
            'top_multiplier': float,
        }
    """
    # 黄金需要额外拉取 MA200 预热数据
    fetch_start = start_date
    if target == 'gold':
        fetch_start = (pd.Timestamp(start_date) - timedelta(days=_MA200_WARMUP_DAYS)).strftime('%Y-%m-%d')

    price_series = get_price_series(target, fetch_start)
    if price_series is None or price_series.empty:
        raise ValueError(f'无法获取 {target} 价格数据，请检查网络连接')

    pe_series  = get_pe_series(target, start_date)
    vol_series = get_vol_series(target, start_date)

    # 生成定投日期序列（月频=每月1日，周频=每周一）
    monthly_dates = pd.date_range(
        start=start_date,
        end=pd.Timestamp.today().normalize(),
        freq='MS' if freq == 'M' else 'W-MON',
    ).tolist()

    if len(monthly_dates) < 2:
        raise ValueError(f'回测区间过短（少于2个月），请选择更早的起始日期')

    history_df = _run_simulation(
        monthly_dates, price_series, pe_series, vol_series,
        target, base_amount, top_multiplier,
    )

    if history_df.empty:
        raise ValueError('回测期间无有效数据')

    def _summarize(cum_cost_col: str, cum_value_col: str, amount_col: str) -> dict:
        last = history_df.iloc[-1]
        total_cost  = float(last[cum_cost_col])
        final_value = float(last[cum_value_col])

        invested = history_df[history_df[amount_col] > 0]
        dates_list   = pd.to_datetime(invested['date']).tolist()
        amounts_list = invested[amount_col].tolist()
        xirr_val = _xirr(dates_list, amounts_list, final_value)

        val_list = history_df[cum_value_col].tolist()
        mdd_series = _compute_max_drawdown_series(val_list)
        max_dd = min(mdd_series) * 100 if mdd_series else None

        return {
            'total_cost':   round(total_cost, 2),
            'final_value':  round(final_value, 2),
            'profit_loss':  round(final_value - total_cost, 2),
            'xirr':         round(xirr_val * 100, 2) if xirr_val is not None else None,
            'max_drawdown': round(max_dd, 2) if max_dd is not None else None,
            'periods':      int((history_df[amount_col] > 0).sum()),
        }

    return {
        'fixed':          _summarize('fixed_cum_cost',  'fixed_cum_value',  'fixed_amount'),
        'matrix':         _summarize('matrix_cum_cost', 'matrix_cum_value', 'matrix_amount'),
        'history':        history_df,
        'target':         target,
        'start_date':     start_date,
        'base_amount':    base_amount,
        'top_multiplier': top_multiplier,
    }
