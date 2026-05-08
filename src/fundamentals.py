"""fundamentals.py — 个股基本面数据拉取与缓存。

Code → YF_Symbol 映射和基本面缓存均存储在 $FAMILYFUND_DATA/yf_symbols.json。
缓存当日有效（同 market_cache.json 策略）。
"""

import json
import os
from datetime import date

FIELDS = [
    'currentPrice', 'currency',
    'trailingPE', 'forwardPE', 'priceToBook', 'returnOnEquity',
    'dividendYield', 'trailingEps', 'forwardEps',
    'revenueGrowth', 'earningsGrowth', 'marketCap',
]

_DEFAULT_SYMBOLS = {
    '601838':  '601838.SS',
    'HK0700':  '0700.HK',
    'SAP.DE':  'SAP',
}


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, 'yf_symbols.json')


def load_yf_symbols(data_dir: str) -> dict:
    """加载 yf_symbols.json。文件不存在时返回预设默认映射。"""
    p = _path(data_dir)
    if not os.path.exists(p):
        return dict(_DEFAULT_SYMBOLS)
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save_yf_symbols(data_dir: str, data: dict):
    """写入 yf_symbols.json。"""
    with open(_path(data_dir), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_fundamentals(yf_symbol: str) -> dict | None:
    """从 yfinance 拉取基本面数据。失败返回 None。"""
    try:
        import yfinance as yf
        info = yf.Ticker(yf_symbol).info
        result = {}
        for field in FIELDS:
            v = info.get(field)
            if v is not None:
                try:
                    result[field] = round(float(v), 6)
                except (TypeError, ValueError):
                    pass
        return result if result else None
    except Exception:
        return None


def get_fundamentals(data_dir: str, code: str, force_refresh: bool = False) -> dict | None:
    """获取单只个股基本面（带当日缓存）。

    Returns:
        dict of fundamentals，或 None（无映射 / 拉取失败）
    """
    data = load_yf_symbols(data_dir)
    yf_symbol = data.get(code)
    if not yf_symbol or yf_symbol.startswith('_'):
        return None

    today = date.today().isoformat()
    cache = data.get('_cache', {})
    cached = cache.get(yf_symbol, {})

    if not force_refresh and cached.get('updated') == today:
        result = {k: v for k, v in cached.items() if k != 'updated'}
        return result if result else None

    # 拉取新数据
    fresh = fetch_fundamentals(yf_symbol)
    if fresh:
        fresh['updated'] = today
        if '_cache' not in data:
            data['_cache'] = {}
        data['_cache'][yf_symbol] = fresh
        save_yf_symbols(data_dir, data)
        return {k: v for k, v in fresh.items() if k != 'updated'}

    # 拉取失败，返回旧缓存（若有）
    if cached:
        return {k: v for k, v in cached.items() if k != 'updated'}
    return None


def get_all_fundamentals(data_dir: str, codes: list, force_refresh: bool = False) -> dict:
    """批量获取基本面数据。返回 {code: fundamentals_dict}，无数据的 code 不出现在结果中。"""
    result = {}
    for code in codes:
        f = get_fundamentals(data_dir, code, force_refresh=force_refresh)
        if f:
            result[code] = f
    return result


def add_yf_symbol(data_dir: str, code: str, yf_symbol: str):
    """新增/更新 Code → YF_Symbol 映射，清除该 symbol 的旧缓存。"""
    data = load_yf_symbols(data_dir)
    data[code.strip()] = yf_symbol.strip()
    # 清除旧缓存，下次访问时重新拉取
    cache = data.get('_cache', {})
    if yf_symbol in cache:
        del cache[yf_symbol]
        data['_cache'] = cache
    save_yf_symbols(data_dir, data)


def remove_yf_symbol(data_dir: str, code: str):
    """删除 Code → YF_Symbol 映射及其缓存。"""
    data = load_yf_symbols(data_dir)
    yf_symbol = data.pop(code, None)
    if yf_symbol:
        cache = data.get('_cache', {})
        cache.pop(yf_symbol, None)
        data['_cache'] = cache
    save_yf_symbols(data_dir, data)


# ── PE 历史分位 ────────────────────────────────────────────

_PE_CACHE_KEY = '_pe_history'


def get_pe_percentile(code: str, current_pe: float | None) -> dict | None:
    """从百度估值接口拉取个股历史 PE，计算当前分位数。

    支持 A股（6位数字）和港股（5位数字，如 00700）。
    SAP 等美股跳过（返回 None）。

    Returns:
        {
            'percentile': float,   # 0-100，当前PE在历史中的分位
            'pe_min':     float,
            'pe_max':     float,
            'pe_median':  float,
            'days':       int,     # 历史天数
        }
        或 None（不支持/失败）
    """
    if current_pe is None:
        return None

    # 判断市场类型
    code = code.strip()
    if code.isdigit() and len(code) == 6:
        # A股
        symbol = code
        fetch_fn = 'stock_zh_valuation_baidu'
    elif code.isdigit() and len(code) == 5:
        # 港股（如 00700）
        symbol = code
        fetch_fn = 'stock_zh_valuation_baidu'
    elif code.upper().startswith('HK') and code[2:].isdigit():
        # HK0700 格式
        symbol = code[2:].zfill(5)
        fetch_fn = 'stock_zh_valuation_baidu'
    else:
        return None  # 美股/其他，跳过

    try:
        import akshare as ak
        fn = getattr(ak, fetch_fn)
        df = fn(symbol=symbol, indicator='市盈率(TTM)')
        if df is None or df.empty:
            return None
        values = df['value'].dropna()
        if len(values) < 10:
            return None
        pct = float((values <= current_pe).sum() / len(values) * 100)
        return {
            'percentile': round(pct, 1),
            'pe_min':     round(float(values.min()), 2),
            'pe_max':     round(float(values.max()), 2),
            'pe_median':  round(float(values.median()), 2),
            'days':       len(values),
        }
    except Exception:
        return None
