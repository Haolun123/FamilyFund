"""fundamentals.py — 个股基本面数据拉取与缓存。

yf_symbols.json 数据结构（v2）：
{
    "601838": {
        "yf_symbol":        "601838.SS",
        "show_fundamentals": true       # 是否在基本面面板展示
    },
    "HK0700": {"yf_symbol": "0700.HK",  "show_fundamentals": true},
    "SAP.DE":  {"yf_symbol": "SAP",      "show_fundamentals": true},
    "512890":  {"yf_symbol": "512890.SS","show_fundamentals": false},
    "_cache":  {...}   # 内部缓存，key 以 _ 开头
}

show_fundamentals=true  → 在 Market Tab 个股基本面面板展示
show_fundamentals=false → 仅用于价格刷新路由，不展示基本面
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
    '601838': {'yf_symbol': '601838.SS', 'show_fundamentals': True},
    'HK0700': {'yf_symbol': '0700.HK',  'show_fundamentals': True},
    'SAP.DE': {'yf_symbol': 'SAP',       'show_fundamentals': True},
}


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, 'yf_symbols.json')


def _migrate_if_needed(data: dict) -> dict:
    """自动迁移旧格式（值为字符串）到新格式（值为 dict）。"""
    migrated = False
    for key, val in list(data.items()):
        if key.startswith('_'):
            continue
        if isinstance(val, str):
            # 旧格式：字符串 → 新格式：dict，默认 show_fundamentals=True
            data[key] = {'yf_symbol': val, 'show_fundamentals': True}
            migrated = True
    return data, migrated


def load_yf_symbols(data_dir: str) -> dict:
    """加载 yf_symbols.json，自动迁移旧格式。"""
    p = _path(data_dir)
    if not os.path.exists(p):
        return dict(_DEFAULT_SYMBOLS)
    with open(p, encoding='utf-8') as f:
        data = json.load(f)
    data, migrated = _migrate_if_needed(data)
    if migrated:
        save_yf_symbols(data_dir, data)
    return data


def save_yf_symbols(data_dir: str, data: dict):
    """写入 yf_symbols.json（原子写入）。"""
    p = _path(data_dir)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def get_yf_symbol(data: dict, code: str) -> str | None:
    """从已加载的 data 中取 yf_symbol，兼容新旧格式。"""
    entry = data.get(code)
    if entry is None or str(code).startswith('_'):
        return None
    if isinstance(entry, dict):
        return entry.get('yf_symbol')
    return str(entry)  # 兜底兼容


def get_show_fundamentals(data: dict, code: str) -> bool:
    """返回该 code 是否在基本面面板展示。默认 True（向后兼容）。"""
    entry = data.get(code)
    if isinstance(entry, dict):
        return entry.get('show_fundamentals', True)
    return True


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
    yf_symbol = get_yf_symbol(data, code)
    if not yf_symbol:
        return None

    today = date.today().isoformat()
    cache = data.get('_cache', {})
    cached = cache.get(yf_symbol, {})

    if not force_refresh and cached.get('updated') == today:
        result = {k: v for k, v in cached.items() if k != 'updated'}
        return result if result else None

    fresh = fetch_fundamentals(yf_symbol)
    if fresh:
        fresh['updated'] = today
        if '_cache' not in data:
            data['_cache'] = {}
        data['_cache'][yf_symbol] = fresh
        save_yf_symbols(data_dir, data)
        return {k: v for k, v in fresh.items() if k != 'updated'}

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


def add_yf_symbol(data_dir: str, code: str, yf_symbol: str, show_fundamentals: bool = True):
    """新增/更新 Code 映射，清除该 symbol 的旧缓存。"""
    data = load_yf_symbols(data_dir)
    code = code.strip()
    yf_symbol = yf_symbol.strip()
    data[code] = {'yf_symbol': yf_symbol, 'show_fundamentals': show_fundamentals}
    cache = data.get('_cache', {})
    if yf_symbol in cache:
        del cache[yf_symbol]
        data['_cache'] = cache
    save_yf_symbols(data_dir, data)


def update_show_fundamentals(data_dir: str, code: str, show: bool):
    """更新 show_fundamentals 标志。"""
    data = load_yf_symbols(data_dir)
    if code in data and isinstance(data[code], dict):
        data[code]['show_fundamentals'] = show
        save_yf_symbols(data_dir, data)


def remove_yf_symbol(data_dir: str, code: str):
    """删除 Code 映射及其缓存。"""
    data = load_yf_symbols(data_dir)
    entry = data.pop(code, None)
    if entry:
        yf_symbol = entry.get('yf_symbol') if isinstance(entry, dict) else entry
        cache = data.get('_cache', {})
        cache.pop(yf_symbol, None)
        data['_cache'] = cache
    save_yf_symbols(data_dir, data)


# ── PE 历史分位 ────────────────────────────────────────────

def append_pe_snapshot(data_dir: str, yf_symbols: dict):
    """拉取各 YF Symbol 的当日 PE，追加到 pe_history_us.json（幂等）。

    只处理美股/ADR 和港股（非 .SS/.SZ），A股 用 akshare 实时拉取。
    """
    import yfinance as yf
    from datetime import date as _date

    p = os.path.join(data_dir, 'pe_history_us.json')
    history = {}
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            history = json.load(f)

    today = _date.today().isoformat()
    dirty = False

    for code, entry in yf_symbols.items():
        if code.startswith('_'):
            continue
        symbol = entry.get('yf_symbol') if isinstance(entry, dict) else str(entry)
        if not symbol:
            continue
        # A股排除
        if any(symbol.endswith(s) for s in ['.SS', '.SZ']):
            continue

        existing = history.get(symbol, [])
        if existing and existing[-1].get('date') == today:
            continue

        try:
            info = yf.Ticker(symbol).info
            pe = info.get('trailingPE')
            if pe and float(pe) > 0:
                history.setdefault(symbol, []).append({
                    'date': today,
                    'pe':   round(float(pe), 2),
                })
                dirty = True
        except Exception:
            pass

    if dirty:
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)


def get_pe_percentile_from_snapshot(data_dir: str, yf_symbol: str, current_pe: float | None) -> dict | None:
    """从 pe_history_us.json 快照计算历史分位（用于美股/ADR/港股）。"""
    if current_pe is None:
        return None

    p = os.path.join(data_dir, 'pe_history_us.json')
    if not os.path.exists(p):
        return None

    with open(p, encoding='utf-8') as f:
        history = json.load(f)

    records = history.get(yf_symbol, [])
    if len(records) < 10:
        return None

    values = [r['pe'] for r in records if r.get('pe')]
    if not values:
        return None

    pct = sum(1 for v in values if v <= current_pe) / len(values) * 100
    return {
        'percentile': round(pct, 1),
        'pe_min':     round(min(values), 2),
        'pe_max':     round(max(values), 2),
        'pe_median':  round(sorted(values)[len(values) // 2], 2),
        'days':       len(values),
    }
