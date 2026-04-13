"""benchmark.py — 基准指数与宏观数据获取、缓存、归一化

支持基准：
  - CSI 300 (沪深300)   — akshare 日频
  - S&P 500 CNY         — yfinance 日频 + USDCNY 汇率换算
  - CPI                 — akshare 月频，同比链式计算累计指数
  - M2                  — akshare 月频，余额归一化

缓存策略：
  - 缓存文件：$FAMILYFUND_DATA/benchmark_cache.json（iCloud 目录）
  - 当天已更新则直接读缓存；过期则尝试拉新数据，失败时 fallback 到旧缓存
  - 各基准独立更新：某个接口失败不影响其他基准
"""

import json
import os
from datetime import date, datetime, timedelta

import pandas as pd

# ── 缓存文件路径 ──────────────────────────────────────────────
_DATA_DIR = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'),
)
CACHE_PATH = os.path.join(_DATA_DIR, 'benchmark_cache.json')

# ── 基准显示名称 ──────────────────────────────────────────────
BENCHMARK_DISPLAY_NAMES = {
    'csi300':   'CSI 300 沪深300',
    'sp500_cny': 'S&P 500 (CNY)',
    'cpi':      'CPI 累计通胀',
    'm2':       'M2 货币供应',
}

BENCHMARK_COLORS = {
    'csi300':   '#FF6B6B',
    'sp500_cny': '#4ECDC4',
    'cpi':      '#FFA726',
    'm2':       '#AB47BC',
}


# ══════════════════════════════════════════════════════════════
# Cache I/O
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
    """当天已更新则视为新鲜。"""
    ts = cache.get(f'{key}_updated')
    if not ts:
        return False
    return ts == date.today().isoformat()


# ══════════════════════════════════════════════════════════════
# Fetchers
# ══════════════════════════════════════════════════════════════

def _fetch_csi300(start_date: str) -> list[dict] | None:
    """拉取沪深300日线数据，返回 [{date, value}, ...]。"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol='sh000300')
        df = df.rename(columns={'date': 'date', 'close': 'close'})
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df = df[df['date'] >= start_date][['date', 'close']].copy()
        df = df.sort_values('date').reset_index(drop=True)
        return [{'date': r['date'], 'value': float(r['close'])} for _, r in df.iterrows()]
    except Exception:
        return None


def _fetch_sp500_cny(start_date: str) -> list[dict] | None:
    """拉取 S&P500 日线 + USDCNY 汇率，返回 CNY 计价的 [{date, value}, ...]。"""
    try:
        import yfinance as yf
        # 多拉几天确保覆盖
        fetch_start = (pd.to_datetime(start_date) - timedelta(days=5)).strftime('%Y-%m-%d')
        sp = yf.download('^GSPC', start=fetch_start, progress=False, auto_adjust=True)
        fx = yf.download('USDCNY=X', start=fetch_start, progress=False, auto_adjust=True)

        if sp.empty or fx.empty:
            return None

        sp.index = pd.to_datetime(sp.index).strftime('%Y-%m-%d')
        fx.index = pd.to_datetime(fx.index).strftime('%Y-%m-%d')

        # 取收盘价
        sp_close = sp['Close'].squeeze()
        fx_close = fx['Close'].squeeze()

        # 对齐日期（外连接，用前值填充汇率缺失）
        merged = pd.DataFrame({'sp': sp_close, 'fx': fx_close})
        merged['fx'] = merged['fx'].ffill().bfill()
        merged = merged.dropna(subset=['sp'])
        merged['cny'] = merged['sp'] * merged['fx']
        merged = merged[merged.index >= start_date].sort_index()

        return [{'date': d, 'value': float(row['cny'])} for d, row in merged.iterrows()]
    except Exception:
        return None


def _fetch_cpi(start_date: str) -> list[dict] | None:
    """拉取 CPI 数据，使用「全国-当月」列（已是以100为基的环比指数），
    链式相乘得到累计指数，返回月频归一化序列 [{date, value}, ...]。
    """
    try:
        import akshare as ak
        df = ak.macro_china_cpi()
        # 月份格式: "2026年03月份"
        df['month'] = pd.to_datetime(
            df['月份'].str.replace('份', ''), format='%Y年%m月'
        ).dt.strftime('%Y-%m-01')
        df['mom'] = pd.to_numeric(df['全国-当月'], errors='coerce')  # e.g. 101.0 = +1% MoM
        df = df[['month', 'mom']].dropna().sort_values('month').reset_index(drop=True)

        # 链式计算累计指数：Index(t) = Index(t-1) × (mom/100)
        records = []
        index_val = 1.0
        for _, row in df.iterrows():
            index_val *= row['mom'] / 100.0
            records.append({'month': row['month'], 'raw_index': index_val})

        if not records:
            return None

        # 归一化：用 start_date 之前最近一期作为基准（宏观数据有滞后，正常现象）
        start_month = pd.to_datetime(start_date).strftime('%Y-%m-01')
        # 找 <= start_month 的最后一期，或 >= start_month 的第一期
        before = [r for r in records if r['month'] <= start_month]
        after  = [r for r in records if r['month'] >  start_month]
        if before:
            base = before[-1]['raw_index']
            base_month = before[-1]['month']
        elif after:
            base = after[0]['raw_index']
            base_month = after[0]['month']
        else:
            return None

        # 返回从 base_month 开始的所有数据（含滞后期）
        result = [
            {'date': r['month'], 'value': round(r['raw_index'] / base, 6)}
            for r in records if r['month'] >= base_month
        ]
        return result if result else None
    except Exception:
        return None


def _fetch_m2(start_date: str) -> list[dict] | None:
    """拉取 M2 余额数据，返回月频归一化序列 [{date, value}, ...]。"""
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        # 月份格式: "2026年02月份"
        df['month'] = pd.to_datetime(
            df['月份'].str.replace('份', ''), format='%Y年%m月'
        ).dt.strftime('%Y-%m-01')
        df['m2'] = pd.to_numeric(df['货币和准货币(M2)-数量(亿元)'], errors='coerce')
        df = df[['month', 'm2']].dropna().sort_values('month').reset_index(drop=True)

        start_month = pd.to_datetime(start_date).strftime('%Y-%m-01')
        # 宏观数据有约1个月滞后，用最近可用月份作为基准
        df_before = df[df['month'] <= start_month]
        df_use = df_before if not df_before.empty else df
        df_use = df_use.reset_index(drop=True)

        if df_use.empty:
            return None

        base = df_use.iloc[-1]['m2']
        base_month = df_use.iloc[-1]['month']
        df_from_base = df[df['month'] >= base_month].reset_index(drop=True)
        return [
            {'date': row['month'], 'value': round(row['m2'] / base, 6)}
            for _, row in df_from_base.iterrows()
        ]
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# Normalization
# ══════════════════════════════════════════════════════════════

def _normalize(records: list[dict], start_date: str) -> list[dict]:
    """将数据序列在 start_date（或之后最近一个交易日）归一化为 1.0。"""
    if not records:
        return records

    # 找基准值：start_date 当天，或之后第一个有数据的日期
    base = None
    for r in sorted(records, key=lambda x: x['date']):
        if r['date'] >= start_date:
            base = r['value']
            break

    if base is None or base == 0:
        return records

    return [{'date': r['date'], 'value': round(r['value'] / base, 6)} for r in records]


# ══════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════

def get_benchmarks(start_date: str) -> dict:
    """获取所有基准数据（已归一化），带缓存和 fallback。

    Args:
        start_date: 基金起始日期 YYYY-MM-DD（第一个 snapshot 的日期）

    Returns:
        dict: {
            'csi300':    [{'date': ..., 'value': ...}, ...],
            'sp500_cny': [...],
            'cpi':       [...],
            'm2':        [...],
            'meta': {
                'csi300_updated': '2026-04-13',
                'sp500_cny_updated': '2026-04-13',
                'cpi_updated': '2026-04-13',
                'm2_updated': '2026-04-13',
            }
        }
        value = None 表示该基准完全不可用（无缓存且拉取失败）
    """
    cache = _load_cache()
    cache_dirty = False

    fetchers = {
        'csi300':    lambda: _normalize(_fetch_csi300(start_date), start_date),
        'sp500_cny': lambda: _normalize(_fetch_sp500_cny(start_date), start_date),
        'cpi':       lambda: _fetch_cpi(start_date),   # 已在内部归一化
        'm2':        lambda: _fetch_m2(start_date),    # 已在内部归一化
    }

    result = {}
    for key, fetcher in fetchers.items():
        if _cache_is_fresh(cache, key):
            result[key] = cache.get(key)
        else:
            fresh = fetcher()
            if fresh:
                cache[key] = fresh
                cache[f'{key}_updated'] = date.today().isoformat()
                cache_dirty = True
                result[key] = fresh
            else:
                # fallback to stale cache
                result[key] = cache.get(key)

    if cache_dirty:
        _save_cache(cache)

    # meta: 每个基准的数据截至日期
    meta = {}
    for key in fetchers:
        updated = cache.get(f'{key}_updated', '未知')
        data = result.get(key)
        if data:
            latest = max(r['date'] for r in data)
            meta[f'{key}_data_until'] = latest
        meta[f'{key}_updated'] = updated

    result['meta'] = meta
    return result
