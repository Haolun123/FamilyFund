"""dividend_monitor.py — 红利低波估值温度计。

数据源：
  - 理杏仁 Open API：H30269 历史 dyr / pb / cp（收盘价）
  - akshare bond_zh_us_rate：中国10Y国债历史收益率

指标：
  - 股息率5年历史分位（主展示）
  - 股息率-国债利差5年历史分位（相对价值）
  - PB5年历史分位（交叉验证）
  - 3年年化波动率（风险背景）
  - 历史最大回撤（风险背景）

使用方式：
    from dividend_monitor import get_dividend_monitor_data
    report = get_dividend_monitor_data(data_dir)
"""

import json
import os
import datetime
import math
import requests


# ── 常量 ──────────────────────────────────────────────────────
LIXINGER_API   = 'https://open.lixinger.com/api/cn/index/fundamental'
CACHE_FILENAME = 'dividend_monitor_cache.json'
BOND_CACHE_FILENAME = 'bond_yield_cache.json'
CACHE_TTL_HOURS      = 12
BOND_CACHE_TTL_HOURS = 24   # 国债数据每日更新一次即可

INDEX_H30269    = 'H30269'
HISTORY_START   = '2016-01-01'
PERCENTILE_YEARS = 5
VOL_YEARS        = 3    # 年化波动率计算窗口


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


def _bond_cache_path(data_dir: str) -> str:
    return os.path.join(data_dir, 'cache', BOND_CACHE_FILENAME)


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


# ── 国债历史收益率 ─────────────────────────────────────────────
def _fetch_bond_yield_history(data_dir: str) -> dict[str, float]:
    """
    返回 {日期str: 10Y国债收益率(小数)} 字典，有本地缓存则优先读缓存。
    使用 akshare bond_zh_us_rate。
    """
    cache_path = _bond_cache_path(data_dir)
    # 读缓存
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as f:
                c = json.load(f)
            updated = c.get('updated', '')
            age = (datetime.datetime.now() - datetime.datetime.fromisoformat(updated)).total_seconds()
            if age < BOND_CACHE_TTL_HOURS * 3600:
                return c.get('data', {})
        except Exception:
            pass

    # 重新拉取
    import akshare as ak
    df = ak.bond_zh_us_rate(start_date=HISTORY_START.replace('-', ''))
    if df.empty:
        return {}
    col = '中国国债收益率10年'
    if col not in df.columns:
        return {}
    result = {}
    for _, row in df.iterrows():
        date_str = str(row['日期'])[:10]
        val = row[col]
        if val is not None and str(val) not in ('nan', 'None', ''):
            try:
                result[date_str] = float(val) / 100  # 转为小数
            except (ValueError, TypeError):
                pass

    # 保存缓存
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump({'updated': datetime.datetime.now().isoformat(), 'data': result},
                  f, ensure_ascii=False, indent=2)
    return result


# ── 理杏仁数据拉取 ────────────────────────────────────────────
def _fetch_lixinger(token: str) -> list[dict]:
    """拉取 H30269 全部历史 dyr + pb + cp，返回按日期升序的 list。"""
    today = datetime.date.today().isoformat()
    payload = {
        'token': token,
        'stockCodes': [INDEX_H30269],
        'startDate': HISTORY_START,
        'endDate': today,
        'metricsList': ['dyr.mcw', 'pb.mcw', 'cp'],
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
    parsed = []
    for row in rows:
        dyr = row.get('dyr.mcw')
        pb  = row.get('pb.mcw')
        cp  = row.get('cp')
        if dyr is None or pb is None:
            continue
        parsed.append({
            'date': row['date'][:10],
            'dyr':  float(dyr),
            'pb':   float(pb),
            'cp':   float(cp) if cp is not None else None,
        })
    parsed.sort(key=lambda x: x['date'])
    return parsed


# ── 风险指标计算 ──────────────────────────────────────────────
def _calc_volatility(prices: list[float], years: int = VOL_YEARS) -> float | None:
    """年化波动率（日收益率标准差 × √252），取最近 years 年。"""
    if len(prices) < 2:
        return None
    n = min(len(prices), years * 252)
    recent = prices[-n:]
    returns = [(recent[i] - recent[i-1]) / recent[i-1]
               for i in range(1, len(recent))
               if recent[i-1] > 0]
    if len(returns) < 10:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _calc_max_drawdown(prices: list[float]) -> float | None:
    """历史最大回撤（负数表示下跌幅度）。"""
    if len(prices) < 2:
        return None
    peak = prices[0]
    max_dd = 0.0
    for p in prices[1:]:
        if p > peak:
            peak = p
        dd = (p - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


# ── 分位数计算 ────────────────────────────────────────────────
def _percentile_rank(series: list[float], value: float) -> float:
    """经验分位数（严格小于）。"""
    if not series:
        return float('nan')
    return sum(1 for v in series if v < value) / len(series)


def _window(rows_or_dict: list | dict, cutoff: str) -> list:
    """从 list[dict] 或 {date: val} 中过滤出 date >= cutoff 的数据。"""
    if isinstance(rows_or_dict, dict):
        return [v for k, v in rows_or_dict.items() if k >= cutoff]
    return [r for r in rows_or_dict if r['date'] >= cutoff]


# ── 主计算 ────────────────────────────────────────────────────
def _compute_metrics(rows: list[dict], bond_history: dict[str, float]) -> dict:
    """计算全部指标。"""
    if not rows:
        return {}

    latest      = rows[-1]
    latest_date = latest['date']
    today       = datetime.date.fromisoformat(latest_date)
    cutoff5     = (today - datetime.timedelta(days=PERCENTILE_YEARS * 365)).isoformat()

    # ── 基础序列 ──
    window5    = [r for r in rows if r['date'] >= cutoff5]
    dyr_all    = [r['dyr'] for r in rows]
    pb_all     = [r['pb']  for r in rows]
    dyr_win5   = [r['dyr'] for r in window5]
    pb_win5    = [r['pb']  for r in window5]

    # ── 利差序列（逐日对齐国债） ──
    spread_all  = {}
    spread_win5 = {}
    for r in rows:
        bond = bond_history.get(r['date'])
        if bond is not None:
            s = r['dyr'] - bond
            spread_all[r['date']] = s
            if r['date'] >= cutoff5:
                spread_win5[r['date']] = s

    current_bond = bond_history.get(latest_date)
    # 当天国债数据缺失时（T-1延迟），向前最多找5个交易日
    if current_bond is None and bond_history:
        for i in range(1, 6):
            fallback = (datetime.date.fromisoformat(latest_date) - datetime.timedelta(days=i)).isoformat()
            current_bond = bond_history.get(fallback)
            if current_bond is not None:
                break
    current_spread = (latest['dyr'] - current_bond) if current_bond is not None else None

    # 利差分位（5年）
    spread_win5_vals = list(spread_win5.values())
    spread_all_vals  = list(spread_all.values())
    spread_pct5  = _percentile_rank(spread_win5_vals, current_spread) if current_spread is not None and spread_win5_vals else None
    spread_pct_all = _percentile_rank(spread_all_vals, current_spread) if current_spread is not None and spread_all_vals else None

    # ── 波动率 & 最大回撤 ──
    prices = [r['cp'] for r in rows if r.get('cp') is not None]
    vol3y    = _calc_volatility(prices, years=VOL_YEARS)
    max_dd   = _calc_max_drawdown(prices)

    return {
        'date':    latest_date,
        'dyr':     latest['dyr'],
        'pb':      latest['pb'],
        # 5年分位（主展示）
        'dyr_pct5':    _percentile_rank(dyr_win5, latest['dyr']),
        'pb_pct5':     _percentile_rank(pb_win5,  latest['pb']),
        'spread':      current_spread,
        'spread_pct5': spread_pct5,
        'window5_start': window5[0]['date'] if window5 else None,
        'window5_n':   len(window5),
        # 全历史分位
        'dyr_pct_all':    _percentile_rank(dyr_all, latest['dyr']),
        'pb_pct_all':     _percentile_rank(pb_all,  latest['pb']),
        'spread_pct_all': spread_pct_all,
        'history_start':  rows[0]['date'],
        'history_n':      len(rows),
        # 全历史统计
        'dyr_mean_all': sum(dyr_all) / len(dyr_all),
        'dyr_min_all':  min(dyr_all),
        'dyr_max_all':  max(dyr_all),
        'pb_mean_all':  sum(pb_all) / len(pb_all),
        'pb_min_all':   min(pb_all),
        'pb_max_all':   max(pb_all),
        'spread_mean_all': sum(spread_all_vals) / len(spread_all_vals) if spread_all_vals else None,
        'spread_min_all':  min(spread_all_vals) if spread_all_vals else None,
        'spread_max_all':  max(spread_all_vals) if spread_all_vals else None,
        # 风险指标
        'vol3y':    vol3y,
        'max_dd':   max_dd,
        # 国债参考
        'risk_free_rate': current_bond,
    }


# ── 对外接口 ──────────────────────────────────────────────────

def _inject_risk_free(result: dict, data_dir: str):
    """从nav_engine实时读取无风险利率，注入result['index']，无论缓存里是否有旧值。"""
    try:
        from nav_engine import _load_risk_free_rate
        rfr = _load_risk_free_rate(data_dir)
        idx = result.get('index', {})
        idx['risk_free_rate'] = rfr
        dyr = idx.get('dyr')
        if dyr is not None and rfr is not None:
            idx['spread'] = dyr - rfr
        else:
            idx['spread'] = None
    except Exception:
        pass
def get_dividend_monitor_data(data_dir: str, force_refresh: bool = False) -> dict:
    """
    获取红利低波估值温度计数据。有缓存则直接返回，否则调用 API。

    返回结构：
    {
        'status': 'ok' | 'no_token' | 'error',
        'error_msg': str,
        'index': {
            date, dyr, pb,
            dyr_pct5, pb_pct5, spread, spread_pct5,
            dyr_pct_all, pb_pct_all, spread_pct_all,
            dyr_mean/min/max_all, pb_mean/min/max_all, spread_mean/min/max_all,
            vol3y, max_dd, risk_free_rate,
            window5_start, window5_n, history_start, history_n,
        },
        'updated': str,
    }
    """
    if not force_refresh:
        cache = _load_cache(data_dir)
        if cache and cache.get('status') == 'ok':
            # 国债利率实时更新，不依赖缓存里的旧值
            _inject_risk_free(cache, data_dir)
            return cache

    token = _load_lixinger_token(data_dir)
    if not token:
        return {'status': 'no_token',
                'error_msg': '未配置理杏仁 token，请在 data/config/lixinger_config.json 填入 token'}

    try:
        rows = _fetch_lixinger(token)
    except Exception as e:
        return {'status': 'error', 'error_msg': f'理杏仁 API 请求失败：{e}'}

    try:
        bond_history = _fetch_bond_yield_history(data_dir)
    except Exception:
        bond_history = {}

    metrics = _compute_metrics(rows, bond_history)
    if not metrics:
        return {'status': 'error', 'error_msg': '理杏仁返回空数据'}

    result = {
        'status':    'ok',
        'error_msg': '',
        'index':     metrics,
    }
    _inject_risk_free(result, data_dir)
    _save_cache(data_dir, result)
    return result


def signal_label(pct: float, invert: bool = False) -> tuple[str, str]:
    """
    根据分位返回 (标签, 颜色)。
    invert=True：低分位=贵（用于dyr；spread分位高=便宜，不需要invert）
    invert=False：低分位=便宜（用于pb）
    spread 分位：高=便宜（spread高=股票相对债券越有吸引力），与dyr方向一致，也用invert=True
    """
    if invert:
        cheap = pct >= 0.8
        dear  = pct <= 0.2
    else:
        cheap = pct <= 0.2
        dear  = pct >= 0.8

    if cheap:
        return '低位 · 相对便宜', '#2e7d32'
    elif dear:
        return '高位 · 相对偏贵', '#c62828'
    else:
        return '中位', '#f57f17'
