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


def get_fundamentals_by_yf_symbol(data_dir: str, yf_symbol: str,
                                  force_refresh: bool = False) -> dict | None:
    """通过 yf_symbol 直接获取基本面（带当日缓存）。

    与 get_fundamentals 的区别：不需要先在 yf_symbols.json 注册 code，
    可以传任意 yf_symbol（如 ticker_map.json 里的）。
    缓存仍写入 yf_symbols.json _cache 字段。

    适用场景：Research Tab 芒格面板需要 A 股 ROE/增长率等，
    但 ticker_map 已经有 yf_symbol 了，不需要再走 code 中介。

    Args:
        yf_symbol: yfinance 代码（如 '600309.SS', '0700.HK', 'SAP'）
        force_refresh: True 时绕过缓存

    Returns:
        基本面字典或 None
    """
    if not yf_symbol:
        return None

    data = load_yf_symbols(data_dir)
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


# ── 历史快照：PE / PB / ROE 等全字段 ────────────────────────

def _watch_symbols_path(data_dir: str) -> str:
    return os.path.join(data_dir, 'watch_symbols.json')


def load_watch_symbols(data_dir: str) -> dict:
    """读 watch_symbols.json（与 yf_symbols 互补，存宽基 ETF 等需采集历史但不在持仓中的标的）。

    Returns:
        {'watch': {symbol: {...}}, ...}; 文件不存在返回空 watch
    """
    p = _watch_symbols_path(data_dir)
    if not os.path.exists(p):
        return {'watch': {}}
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'watch': {}}


def _collect_yf_symbols_for_history(data_dir: str) -> list[str]:
    """合并 yf_symbols + watch_symbols，去重，排除 A 股（.SS/.SZ 走 akshare 实时）。

    Returns:
        list of yf_symbol 字符串
    """
    seen: set[str] = set()
    result: list[str] = []

    yf_data = load_yf_symbols(data_dir)
    for code, entry in yf_data.items():
        if code.startswith('_'):
            continue
        sym = entry.get('yf_symbol') if isinstance(entry, dict) else str(entry)
        if not sym or sym in seen:
            continue
        if any(sym.endswith(s) for s in ['.SS', '.SZ']):
            continue
        seen.add(sym)
        result.append(sym)

    watch_data = load_watch_symbols(data_dir).get('watch', {})
    for sym, _meta in watch_data.items():
        if not sym or sym in seen:
            continue
        if any(sym.endswith(s) for s in ['.SS', '.SZ']):
            continue
        seen.add(sym)
        result.append(sym)

    return result


def append_fundamentals_snapshot(data_dir: str) -> dict:
    """拉取所有监控 symbol 的当日全部基本面字段，追加到 fundamentals_history.json（幂等）。

    采集范围：
    - yf_symbols.json 的持仓个股（不含 A 股）
    - watch_symbols.json 的宽基 ETF / ADR

    数据结构：
        {
            symbol: [
                {'date': 'YYYY-MM-DD', 'pe': float, 'pb': float, 'roe': float, ...},
                ...
            ]
        }
        字段名为 FIELDS 中的简洁键（trailingPE → pe, priceToBook → pb 等）

    Args:
        data_dir: $FAMILYFUND_DATA 目录

    Returns:
        {'updated': int, 'skipped_today': int, 'errors': int}
    """
    import yfinance as yf
    from datetime import date as _date

    p = os.path.join(data_dir, 'fundamentals_history.json')
    history: dict[str, list] = {}
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            history = json.load(f)

    today = _date.today().isoformat()
    symbols = _collect_yf_symbols_for_history(data_dir)
    stats = {'updated': 0, 'skipped_today': 0, 'errors': 0}

    # FIELDS → 历史记录中的简短键名
    FIELD_TO_KEY = {
        'currentPrice':    'price',
        'trailingPE':      'pe',
        'forwardPE':       'forward_pe',
        'priceToBook':     'pb',
        'returnOnEquity':  'roe',
        'dividendYield':   'div_yield',
        'trailingEps':     'eps',
        'forwardEps':      'forward_eps',
        'revenueGrowth':   'rev_growth',
        'earningsGrowth':  'earn_growth',
        'marketCap':       'mkt_cap',
    }

    for symbol in symbols:
        existing = history.get(symbol, [])
        if existing and existing[-1].get('date') == today:
            stats['skipped_today'] += 1
            continue
        try:
            info = yf.Ticker(symbol).info
            entry = {'date': today}
            for raw_field, short_key in FIELD_TO_KEY.items():
                v = info.get(raw_field)
                if v is None:
                    continue
                try:
                    entry[short_key] = round(float(v), 6)
                except (TypeError, ValueError):
                    pass
            # 至少有一个字段成功才追加
            if len(entry) > 1:
                history.setdefault(symbol, []).append(entry)
                stats['updated'] += 1
            else:
                stats['errors'] += 1
        except Exception:
            stats['errors'] += 1

    if stats['updated'] > 0:
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    return stats


def append_pe_snapshot(data_dir: str, yf_symbols: dict = None):
    """[DEPRECATED 2026-05-23] 已被 append_fundamentals_snapshot 取代（采集全部基本面字段）。

    此函数保留向后兼容：现在内部调用新函数，忽略 yf_symbols 参数（新函数自动从文件加载）。
    """
    return append_fundamentals_snapshot(data_dir)


def get_fundamentals_history(data_dir: str, symbol: str = None) -> dict | list:
    """读取 fundamentals_history.json。

    Args:
        symbol: None 时返回完整 dict {symbol: [...]}; 指定时返回该 symbol 的列表

    Returns:
        dict 或 list；symbol 不存在返回空列表
    """
    p = os.path.join(data_dir, 'fundamentals_history.json')
    if not os.path.exists(p):
        return {} if symbol is None else []
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {} if symbol is None else []
    if symbol is None:
        return data
    return data.get(symbol, [])


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
