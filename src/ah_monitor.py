"""ah_monitor.py — AH 股溢价率监测。

配置文件：$FAMILYFUND_DATA/ah_config.json
  {
    "stocks": [
      {
        "name":    "中海油",
        "a_code":  "600938.SS",
        "h_code":  "0883.HK"
      },
      ...
    ],
    "_cache": {
      "600938.SS": {"price": 38.28, "updated": "2026-05-07"},
      "0883.HK":   {"price": 27.28, "updated": "2026-05-07"},
      ...
    },
    "_history": {
      "600938.SS/0883.HK": [
        {"date": "2026-05-07", "premium": 132.5},
        ...
      ]
    }
  }

溢价率 = A股价格 / (H股价格 × HKD/CNY汇率) × 100
  > 100: A股贵（港股有折价）
  < 100: 港股贵（A股有折价，罕见）
"""

import json
import os
from datetime import date, timedelta


_DEFAULT_STOCKS = [
    {"name": "中海油",   "a_code": "600938.SS", "h_code": "0883.HK"},
    {"name": "招商银行", "a_code": "600036.SS", "h_code": "3968.HK"},
    {"name": "中芯国际", "a_code": "688981.SS", "h_code": "0981.HK"},
    {"name": "重庆银行", "a_code": "601963.SS", "h_code": "1963.HK"},
]

_HISTORY_DAYS = 365  # 保留近1年历史用于分位数计算


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, 'ah_config.json')


def load_ah_config(data_dir: str) -> dict:
    p = _path(data_dir)
    if not os.path.exists(p):
        return {"stocks": list(_DEFAULT_STOCKS), "_cache": {}, "_history": {}}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save_ah_config(data_dir: str, config: dict):
    p = _path(data_dir)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def add_ah_stock(data_dir: str, name: str, a_code: str, h_code: str):
    config = load_ah_config(data_dir)
    # 避免重复
    for s in config['stocks']:
        if s['a_code'] == a_code.strip():
            return
    config['stocks'].append({
        'name': name.strip(),
        'a_code': a_code.strip(),
        'h_code': h_code.strip(),
    })
    save_ah_config(data_dir, config)


def remove_ah_stock(data_dir: str, a_code: str):
    config = load_ah_config(data_dir)
    config['stocks'] = [s for s in config['stocks'] if s['a_code'] != a_code]
    save_ah_config(data_dir, config)


def _fetch_price(ticker: str) -> float | None:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        p = info.last_price
        return float(p) if p and p > 0 else None
    except Exception:
        return None


def _get_hkd_cny(data_dir: str) -> float:
    """获取 HKD/CNY 汇率，失败时用固定近似值 0.924。"""
    try:
        from fx_service import get_rate
        r = get_rate('HKD', 'CNY')
        if r and r > 0:
            return float(r)
    except Exception:
        pass
    return 0.924


def _cache_fresh(cache: dict, ticker: str) -> bool:
    entry = cache.get(ticker, {})
    return entry.get('updated') == date.today().isoformat()


def get_ah_data(data_dir: str, force_refresh: bool = False) -> list[dict]:
    """获取所有关注 AH 股的当日溢价率。

    Returns:
        list of {
            'name':       str,
            'a_code':     str,
            'h_code':     str,
            'a_price':    float | None,
            'h_price':    float | None,   # HKD
            'h_price_cny':float | None,   # 换算后 CNY
            'hkd_cny':    float,
            'premium':    float | None,   # 溢价率，100=平价
            'pct_1y':     float | None,   # 近1年历史分位数（0-100）
            'signal':     str,            # '港股便宜'/'接近平价'/'港股贵'/'无数据'
        }
    """
    config = load_ah_config(data_dir)
    cache = config.setdefault('_cache', {})
    history = config.setdefault('_history', {})
    today = date.today().isoformat()
    dirty = False

    hkd_cny = _get_hkd_cny(data_dir)

    results = []
    for stock in config.get('stocks', []):
        a_code = stock['a_code']
        h_code = stock['h_code']
        name   = stock['name']

        # 拉取或读缓存
        for ticker in [a_code, h_code]:
            if force_refresh or not _cache_fresh(cache, ticker):
                price = _fetch_price(ticker)
                if price is not None:
                    cache[ticker] = {'price': price, 'updated': today}
                    dirty = True

        a_price = (cache.get(a_code) or {}).get('price')
        h_price = (cache.get(h_code) or {}).get('price')
        h_price_cny = h_price * hkd_cny if h_price else None

        premium = None
        if a_price and h_price_cny and h_price_cny > 0:
            premium = round(a_price / h_price_cny * 100, 1)

        # 更新历史（每日一条）
        hist_key = f'{a_code}/{h_code}'
        hist_list = history.setdefault(hist_key, [])
        if premium is not None:
            if not hist_list or hist_list[-1]['date'] != today:
                hist_list.append({'date': today, 'premium': premium})
                # 只保留近 _HISTORY_DAYS 天
                cutoff = (date.today() - timedelta(days=_HISTORY_DAYS)).isoformat()
                history[hist_key] = [h for h in hist_list if h['date'] >= cutoff]
                dirty = True

        # 历史分位数
        pct_1y = None
        if premium is not None and len(hist_list) >= 10:
            premiums = [h['premium'] for h in hist_list]
            below = sum(1 for p in premiums if p <= premium)
            pct_1y = round(below / len(premiums) * 100, 1)

        # 信号
        if premium is None:
            signal = '无数据'
        elif premium > 120:
            signal = '港股便宜'
        elif premium > 90:
            signal = '接近平价'
        else:
            signal = '港股贵'

        results.append({
            'name':        name,
            'a_code':      a_code,
            'h_code':      h_code,
            'a_price':     a_price,
            'h_price':     h_price,
            'h_price_cny': round(h_price_cny, 3) if h_price_cny else None,
            'hkd_cny':     hkd_cny,
            'premium':     premium,
            'pct_1y':      pct_1y,
            'signal':      signal,
            'history':     history.get(hist_key, []),
        })

    if dirty:
        config['_cache'] = cache
        config['_history'] = history
        save_ah_config(data_dir, config)

    return results
