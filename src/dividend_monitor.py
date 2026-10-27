"""dividend_monitor.py — 红利低波估值温度计。

数据源：理杏仁 Open API（H30269 中证红利低波动指数）
指标：股息率（dyr.mcw）、PB（pb.mcw），5年历史分位 + 股债利差

使用方式：
    from dividend_monitor import get_dividend_monitor_data
    report = get_dividend_monitor_data(data_dir)
"""

import json
import os
import datetime
import requests


# ── 常量 ──────────────────────────────────────────────────────
LIXINGER_API = 'https://open.lixinger.com/api/cn/index/fundamental'
CACHE_FILENAME = 'dividend_monitor_cache.json'
CACHE_TTL_HOURS = 12   # 缓存有效期，盘中不频繁刷新

INDEX_H30269 = 'H30269'
HISTORY_START = '2016-01-01'
PERCENTILE_YEARS = 5


# ── 配置加载 ──────────────────────────────────────────────────
def _load_lixinger_token(data_dir: str) -> str | None:
    path = os.path.join(data_dir, 'config', 'lixinger_config.json')
    try:
        with open(path, encoding='utf-8') as f:
            cfg = json.load(f)
        token = cfg.get('token', '').strip()
        return token if token else None
    except Exception:
        return None


def _cache_path(data_dir: str) -> str:
    return os.path.join(data_dir, 'cache', CACHE_FILENAME)


def _load_cache(data_dir: str) -> dict | None:
    path = _cache_path(data_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            cache = json.load(f)
        updated = cache.get('updated')
        if not updated:
            return None
        age = (datetime.datetime.now() - datetime.datetime.fromisoformat(updated)).total_seconds()
        if age > CACHE_TTL_HOURS * 3600:
            return None
        return cache
    except Exception:
        return None


def _save_cache(data_dir: str, data: dict):
    path = _cache_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data['updated'] = datetime.datetime.now().isoformat()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ── 理杏仁数据拉取 ────────────────────────────────────────────
def _fetch_lixinger(token: str) -> list[dict]:
    """拉取 H30269 全部历史 dyr + pb，返回按日期升序的 list。"""
    today = datetime.date.today().isoformat()
    payload = {
        'token': token,
        'stockCodes': [INDEX_H30269],
        'startDate': HISTORY_START,
        'endDate': today,
        'metricsList': ['dyr.mcw', 'pb.mcw'],
    }
    headers = {
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip',
    }
    r = requests.post(LIXINGER_API, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    resp = r.json()
    if resp.get('code') != 1:
        raise ValueError(f"理杏仁 API 返回错误：{resp.get('message', resp.get('error'))}")
    rows = resp.get('data', [])
    # 解析日期，升序排列，过滤缺失值
    parsed = []
    for row in rows:
        dyr = row.get('dyr.mcw')
        pb  = row.get('pb.mcw')
        if dyr is None or pb is None:
            continue
        parsed.append({
            'date': row['date'][:10],
            'dyr':  float(dyr),
            'pb':   float(pb),
        })
    parsed.sort(key=lambda x: x['date'])
    return parsed


# ── 分位数计算 ────────────────────────────────────────────────
def _percentile_rank(series: list[float], value: float) -> float:
    """经验分位数（严格小于）：value 在 series 中排在第几百分位。"""
    if not series:
        return float('nan')
    return sum(1 for v in series if v < value) / len(series)


def _compute_metrics(rows: list[dict]) -> dict:
    """基于全量历史数据，计算当前值 + 5年分位 + 全量统计。"""
    if not rows:
        return {}

    latest = rows[-1]
    today  = datetime.date.fromisoformat(latest['date'])
    cutoff = (today - datetime.timedelta(days=PERCENTILE_YEARS * 365)).isoformat()

    window = [r for r in rows if r['date'] >= cutoff]
    dyr_window = [r['dyr'] for r in window]
    pb_window  = [r['pb']  for r in window]
    dyr_all    = [r['dyr'] for r in rows]
    pb_all     = [r['pb']  for r in rows]

    dyr_pct5 = _percentile_rank(dyr_window, latest['dyr'])
    pb_pct5  = _percentile_rank(pb_window,  latest['pb'])
    dyr_pct_all = _percentile_rank(dyr_all, latest['dyr'])
    pb_pct_all  = _percentile_rank(pb_all,  latest['pb'])

    return {
        'date':         latest['date'],
        'dyr':          latest['dyr'],
        'pb':           latest['pb'],
        # 5年分位（主展示）
        'dyr_pct5':     dyr_pct5,
        'pb_pct5':      pb_pct5,
        'window5_start': window[0]['date'] if window else None,
        'window5_n':    len(window),
        # 全历史分位（展开详情用）
        'dyr_pct_all':  dyr_pct_all,
        'pb_pct_all':   pb_pct_all,
        'history_start': rows[0]['date'],
        'history_n':    len(rows),
        # 全历史统计（10年均值参考）
        'dyr_mean_all': sum(dyr_all) / len(dyr_all),
        'dyr_min_all':  min(dyr_all),
        'dyr_max_all':  max(dyr_all),
        'pb_mean_all':  sum(pb_all) / len(pb_all),
        'pb_min_all':   min(pb_all),
        'pb_max_all':   max(pb_all),
    }


# ── 对外接口 ──────────────────────────────────────────────────
def get_dividend_monitor_data(data_dir: str, force_refresh: bool = False) -> dict:
    """
    获取红利低波估值温度计数据。有缓存则直接返回，否则调用理杏仁 API。

    返回结构：
    {
        'status': 'ok' | 'no_token' | 'error',
        'error_msg': str,          # status != ok 时
        'index': {                 # H30269 指标
            'date', 'dyr', 'pb',
            'dyr_pct5', 'pb_pct5',   # 5年分位（主展示）
            'dyr_pct_all', 'pb_pct_all',
            'dyr_mean_all', 'dyr_min_all', 'dyr_max_all',
            'pb_mean_all',  'pb_min_all',  'pb_max_all',
            'window5_start', 'window5_n',
            'history_start', 'history_n',
        },
        'spread': float | None,    # dyr - 10Y国债
        'risk_free_rate': float | None,
        'updated': str,            # 缓存时间
    }
    """
    if not force_refresh:
        cache = _load_cache(data_dir)
        if cache and cache.get('status') == 'ok':
            return cache

    token = _load_lixinger_token(data_dir)
    if not token:
        return {'status': 'no_token', 'error_msg': '未配置理杏仁 token，请在 data/config/lixinger_config.json 填入 token'}

    try:
        rows = _fetch_lixinger(token)
    except Exception as e:
        result = {'status': 'error', 'error_msg': f'理杏仁 API 请求失败：{e}'}
        return result

    metrics = _compute_metrics(rows)
    if not metrics:
        return {'status': 'error', 'error_msg': '理杏仁返回空数据'}

    # 读国债收益率
    risk_free = None
    spread    = None
    try:
        from nav_engine import _load_risk_free_rate
        risk_free = _load_risk_free_rate(data_dir)
        spread    = metrics['dyr'] - risk_free
    except Exception:
        pass

    result = {
        'status':          'ok',
        'error_msg':       '',
        'index':           metrics,
        'spread':          spread,
        'risk_free_rate':  risk_free,
    }
    _save_cache(data_dir, result)
    return result


def signal_label(pct: float, invert: bool = False) -> tuple[str, str]:
    """
    根据分位返回 (标签, 颜色)。
    invert=True 用于 dyr（分位低=贵，与PB相反）。
    """
    # dyr: 低分位=低股息率=贵；高分位=高股息率=便宜
    # pb:  低分位=低PB=便宜；高分位=高PB=贵
    # invert=True 时翻转信号（用于dyr）
    if invert:
        cheap = pct >= 0.8    # dyr高分位=便宜
        dear  = pct <= 0.2    # dyr低分位=贵
    else:
        cheap = pct <= 0.2    # pb低分位=便宜
        dear  = pct >= 0.8    # pb高分位=贵

    if cheap:
        return '低位 · 相对便宜', '#2e7d32'
    elif dear:
        return '高位 · 相对偏贵', '#c62828'
    else:
        return '中位', '#f57f17'
