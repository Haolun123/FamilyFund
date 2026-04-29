"""fundamentals.py — 个股基本面数据拉取与缓存。

Code → YF_Symbol 映射和基本面缓存均存储在 $FAMILYFUND_DATA/yf_symbols.json。
缓存当日有效（同 market_cache.json 策略）。
"""

import json
import os
from datetime import date

FIELDS = [
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
