"""backtest.py — DCA 定投策略回测引擎

对比「固定金额定投」与「PE×VIX/QVIX 或 MA200×VIX 矩阵策略」的历史表现。

支持标的：
  - csi300    沪深300       PE×QVIX 百分位矩阵（akshare，2015起）
  - csi_a500  中证A500      PE×QVIX 百分位矩阵（akshare，2015起）
  - sp500     标普500       PE×VIX 矩阵（Shiller Yale Excel，~2000起）
  - ndx100    纳指100       PE×VXN 矩阵（⚠️ PE 使用标普500代理；VXN 来源 CBOE，比 VIX 更精准反映纳指期权恐慌）
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
        # 明确取'滚动市盈率'列（与 market_monitor.py 保持一致）
        df = df[['日期', '滚动市盈率']].rename(columns={'日期': 'date', '滚动市盈率': 'value'})
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


def _fetch_vxn_history(start_date: str) -> list[dict] | None:
    """CBOE VXN 历史数据，返回 [{'date': 'YYYY-MM-DD', 'value': float}]。
    来源：cdn.cboe.com（与 market_monitor._fetch_vxn 相同数据源）。
    """
    try:
        import requests
        url = 'https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_VXN.json'
        r = requests.get(url, timeout=15)
        if not r.ok:
            return None
        records = []
        for item in r.json().get('data', []):
            d = item.get('date', '')
            v = item.get('close')
            if d and v and d >= start_date:
                records.append({'date': d, 'value': float(v)})
        return records if records else None
    except Exception as e:
        print(f'[backtest] VXN fetch failed: {e}')
        # Fallback: yfinance ^VXN
        try:
            import yfinance as yf
            raw = yf.download('^VXN', start=start_date, progress=False, auto_adjust=True)
            if raw.empty:
                return None
            close = raw['Close'].squeeze()
            df = pd.DataFrame({'date': close.index.strftime('%Y-%m-%d'), 'value': close.values})
            return df.dropna(subset=['value']).to_dict('records')
        except Exception:
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

    # A股：每次尝试从 legulegu 拉取全量最新数据，成功则覆盖缓存；
    # 失败（SSL/网络）则使用 iCloud 缓存的历史数据（可能滞后但不崩溃）
    symbol_map = {'csi300': '沪深300', 'csi_a500': '中证500'}
    symbol = symbol_map[target]
    cache = _load_cache()
    key = f'bt_pe_{target}'

    fresh = _fetch_pe_akshare(symbol, '2005-01-01')  # 拉全量，不限 start_date
    if fresh:
        cache[key] = fresh
        cache[f'{key}_updated'] = date.today().isoformat()
        _save_cache(cache)
    else:
        if not cache.get(key):
            print(f'[backtest] A股PE({symbol}) 无缓存且拉取失败，回测将跳过PE信号')
        # 有缓存则静默使用旧数据

    records = cache.get(key)
    if not records:
        return None
    s = _records_to_series(records)
    return s[s.index >= pd.Timestamp(start_date)]


def get_vol_series(target: str, start_date: str) -> pd.Series | None:
    """A股 → QVIX；纳指100 → VXN；其余 → VIX。"""
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
    elif target == 'ndx100':
        key = 'bt_vxn'
        if not _cache_is_fresh(cache, key):
            records = _fetch_vxn_history(start_date)
            if records:
                cache[key] = records
                cache[f'{key}_updated'] = date.today().isoformat()
                _save_cache(cache)
        records = cache.get(key)
    else:
        # sp500 / gold → VIX
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

    假设前提：每期按倍数投入的资金均能到位（由用户 Cash 储备保证），
    不模拟资金约束。对比指标用 XIRR 和每元成本市值，无需虚拟账户。
    """
    is_gold    = (target == 'gold')
    is_a_share = target in ('csi300', 'csi_a500')

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
    end_date: str | None = None,
) -> dict:
    """
    运行 DCA 回测，返回固定策略 vs 矩阵策略的对比结果。

    对比前提：每期按倍数投入的资金均能到位（由用户 Cash 储备保证）。
    主要对比指标：XIRR（时间加权资金效率）和每元成本市值。

    Args:
        target:           'csi300' / 'csi_a500' / 'sp500' / 'ndx100' / 'gold'
        start_date:       'YYYY-MM-DD'，回测起始日
        base_amount:      每期基准定投金额
        freq:             'M'（月频）/ 'W'（周频）
        top_multiplier:   '顶格'对应的实际倍数
        end_date:         'YYYY-MM-DD'，回测截止日（None = 今日）

    Returns:
        {
            'fixed':        {'total_cost', 'final_value', 'profit_loss', 'xirr', 'max_drawdown', 'periods', 'value_per_cost'},
            'matrix':       {'total_cost', 'final_value', 'profit_loss', 'xirr', 'max_drawdown', 'periods', 'value_per_cost'},
            'history':      pd.DataFrame,
            'target':       str,
            'start_date':   str,
            'end_date':     str,
            'base_amount':  float,
            'top_multiplier': float,
        }
    """
    # 价格往前多取 10 天，确保月初节假日也能通过 asof 找到最近交易日价格
    # 黄金需要额外拉取 MA200 预热数据
    fetch_start = (pd.Timestamp(start_date) - timedelta(days=10)).strftime('%Y-%m-%d')
    if target == 'gold':
        fetch_start = (pd.Timestamp(start_date) - timedelta(days=_MA200_WARMUP_DAYS)).strftime('%Y-%m-%d')

    price_series = get_price_series(target, fetch_start)
    if price_series is None or price_series.empty:
        raise ValueError(f'无法获取 {target} 价格数据，请检查网络连接')

    pe_series  = get_pe_series(target, start_date)
    vol_series = get_vol_series(target, start_date)

    # 生成定投日期序列（月频=每月1日，周频=每周一）
    range_end = pd.Timestamp(end_date) if end_date else pd.Timestamp.today().normalize()
    monthly_dates = pd.date_range(
        start=start_date,
        end=range_end,
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
            'total_cost':      round(total_cost, 2),
            'final_value':     round(final_value, 2),
            'profit_loss':     round(final_value - total_cost, 2),
            'value_per_cost':  round(final_value / total_cost, 4) if total_cost > 0 else None,
            'xirr':            round(xirr_val * 100, 2) if xirr_val is not None else None,
            'max_drawdown':    round(max_dd, 2) if max_dd is not None else None,
            'periods':         int((history_df[amount_col] > 0).sum()),
        }

    return {
        'fixed':          _summarize('fixed_cum_cost',  'fixed_cum_value',  'fixed_amount'),
        'matrix':         _summarize('matrix_cum_cost', 'matrix_cum_value', 'matrix_amount'),
        'history':        history_df,
        'target':         target,
        'start_date':     start_date,
        'end_date':       end_date or pd.Timestamp.today().strftime('%Y-%m-%d'),
        'base_amount':    base_amount,
        'top_multiplier': top_multiplier,
    }


# ── 各标的可用最早日期（取所有数据源的最晚起始日期）────────────
# sp500/gold：价格1927+，VIX 1990-01-02 → 瓶颈是 VIX
# ndx100：价格1985+，VXN 2009-09-14 → 瓶颈是 VXN
# csi300：legulegu PE 2005-04-08，QVIX 2015+ → 瓶颈是 QVIX
# csi_a500：legulegu PE（中证500代理）2007-01-15，QVIX 2015+ → 瓶颈是 QVIX
_TARGET_MIN_DATES = {
    'sp500':    '1990-01-01',
    'ndx100':   '2009-10-01',
    'gold':     '1990-01-01',
    'csi300':   '2015-01-01',   # QVIX 限制（PE 从 2005 起已够）
    'csi_a500': '2015-01-01',   # QVIX 限制（PE 从 2007 起已够）
}


def run_all_targets(
    user_start_date: str,
    base_amount: float,
    freq: str = 'M',
    top_multiplier_equity: float = 10.0,
    top_multiplier_gold:   float = 5.0,
    end_date: str | None = None,
) -> list[dict]:
    """批量跑所有5个标的，返回散点图所需数据。

    每个标的使用 max(user_start_date, 该标的最早可用日期) 作为实际起始日期。

    Returns:
        [
            {
                'target':       str,
                'actual_start': str,    # 实际使用的起始日期
                'xirr_excess':  float | None,   # 矩阵XIRR - 固定XIRR（%）
                'pl_excess':    float | None,   # 矩阵盈亏 - 固定盈亏（CNY）
                'fixed_xirr':   float | None,
                'matrix_xirr':  float | None,
                'fixed_pl':     float | None,
                'matrix_pl':    float | None,
                'error':        str | None,
            }
        ]
    """
    from datetime import datetime

    _TARGET_NAMES = {
        'csi300':   'CSI300 沪深300',
        'csi_a500': '中证A500',
        'sp500':    '标普500',
        'ndx100':   '纳指100',
        'gold':     '黄金',
    }
    _TOP_MULT = {
        'csi300':   top_multiplier_equity,
        'csi_a500': top_multiplier_equity,
        'sp500':    top_multiplier_equity,
        'ndx100':   top_multiplier_equity,
        'gold':     top_multiplier_gold,
    }

    results = []
    for target in ['sp500', 'ndx100', 'csi300', 'csi_a500', 'gold']:
        min_date = _TARGET_MIN_DATES.get(target, '2000-01-01')
        actual_start = max(user_start_date, min_date)
        try:
            r = run_backtest(
                target=target,
                start_date=actual_start,
                base_amount=base_amount,
                freq=freq,
                top_multiplier=_TOP_MULT[target],
                end_date=end_date,
            )
            fixed  = r['fixed']
            matrix = r['matrix']
            xirr_e = (matrix['xirr'] - fixed['xirr']) if (
                matrix['xirr'] is not None and fixed['xirr'] is not None
            ) else None
            pl_e = (matrix['profit_loss'] - fixed['profit_loss']) if (
                matrix['profit_loss'] is not None and fixed['profit_loss'] is not None
            ) else None
            results.append({
                'target':       target,
                'label':        _TARGET_NAMES[target],
                'actual_start': actual_start,
                'xirr_excess':  round(xirr_e, 2) if xirr_e is not None else None,
                'pl_excess':    round(pl_e, 2)   if pl_e   is not None else None,
                'fixed_xirr':   fixed['xirr'],
                'matrix_xirr':  matrix['xirr'],
                'fixed_pl':     fixed['profit_loss'],
                'matrix_pl':    matrix['profit_loss'],
                'error':        None,
            })
        except Exception as e:
            results.append({
                'target': target, 'label': _TARGET_NAMES[target],
                'actual_start': actual_start,
                'xirr_excess': None, 'pl_excess': None,
                'fixed_xirr': None, 'matrix_xirr': None,
                'fixed_pl': None, 'matrix_pl': None,
                'error': str(e)[:80],
            })
    return results
