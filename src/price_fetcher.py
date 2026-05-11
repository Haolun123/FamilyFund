"""price_fetcher.py — 一键刷新净值，按 Code 格式路由到各数据源。

支持：
- 6位纯数字 → 天天基金 API（国内公募基金）
- *.SS / *.SZ → yfinance（A股个股）
- HK* → yfinance（港股，转换为 XXXX.HK 格式）
- GOLD / GOLD.P → yfinance GC=F + USD/CNY 换算（元/克）
- SAP.DE → yfinance SAP.DE（法兰克福，EUR）用于价格刷新；基本面另走 yf_symbols.json 中的 SAP ADR
- CASH → 固定 1.0
- 其他 → 无法自动，返回 manual 状态

返回结构：
    {
        code: {
            'price':  float | None,
            'date':   str | None,   # YYYY-MM-DD
            'status': 'ok' | 'manual' | 'error',
            'msg':    str,          # 状态说明
        }
    }
"""

import re
from datetime import date


# ── 天天基金 ──────────────────────────────────────────────

def _fetch_eastmoney(code: str) -> dict:
    """拉取天天基金最新净值。"""
    try:
        import requests
        url = (
            f'https://api.fund.eastmoney.com/f10/lsjz'
            f'?fundCode={code}&pageIndex=1&pageSize=1'
        )
        r = requests.get(
            url,
            headers={'Referer': 'https://fund.eastmoney.com/'},
            timeout=10,
        )
        if not r.ok:
            return {'price': None, 'date': None, 'status': 'error', 'msg': f'HTTP {r.status_code}'}
        lst = r.json().get('Data', {}).get('LSJZList', [])
        if not lst:
            return {'price': None, 'date': None, 'status': 'error', 'msg': '无净值数据'}
        item = lst[0]
        return {
            'price':  float(item['DWJZ']),
            'date':   item.get('FSRQ', ''),
            'status': 'ok',
            'msg':    '天天基金',
        }
    except Exception as e:
        return {'price': None, 'date': None, 'status': 'error', 'msg': str(e)[:50]}


# ── yfinance ──────────────────────────────────────────────

def _fetch_yf(symbol: str, label: str = '') -> dict:
    """拉取 yfinance 最新收盘价。"""
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from market_monitor import _fetch_yfinance
        series = _fetch_yfinance(symbol, period='5d')
        if series is None or series.empty:
            return {'price': None, 'date': None, 'status': 'error', 'msg': f'{symbol} 无数据'}
        price = float(series.iloc[-1])
        nav_date = str(series.index[-1])[:10]
        return {
            'price':  price,
            'date':   nav_date,
            'status': 'ok',
            'msg':    label or symbol,
        }
    except Exception as e:
        return {'price': None, 'date': None, 'status': 'error', 'msg': str(e)[:50]}


def _fetch_gold_cny() -> dict:
    """黄金：GC=F（USD/troy oz）× USD/CNY / 31.1035 → 元/克"""
    try:
        from market_monitor import _fetch_yfinance
        from fx_service import get_exchange_rate
        series = _fetch_yfinance('GC=F', period='5d')
        if series is None or series.empty:
            return {'price': None, 'date': None, 'status': 'error', 'msg': 'GC=F 无数据'}
        price_usd_oz = float(series.iloc[-1])
        nav_date = str(series.index[-1])[:10]
        usd_cny = get_exchange_rate('USD', 'CNY')
        if not usd_cny:
            return {'price': None, 'date': None, 'status': 'error', 'msg': '汇率获取失败'}
        price_cny_g = round(price_usd_oz * usd_cny / 31.1035, 4)
        return {
            'price':  price_cny_g,
            'date':   nav_date,
            'status': 'ok',
            'msg':    f'GC=F × USD/CNY / 31.1035',
        }
    except Exception as e:
        return {'price': None, 'date': None, 'status': 'error', 'msg': str(e)[:50]}


# ── 路由 ──────────────────────────────────────────────────

def _route(code: str) -> dict:
    """单个 Code 路由到对应数据源。"""
    # 现金：固定 1.0
    if code == 'CASH':
        return {'price': 1.0, 'date': date.today().isoformat(), 'status': 'ok', 'msg': '固定'}

    # 黄金
    if code in ('GOLD', 'GOLD.P'):
        return _fetch_gold_cny()

    # 6位纯数字 → 国内公募基金（天天基金）
    if re.match(r'^\d{6}$', code):
        return _fetch_eastmoney(code)

    # A股个股（6位数字.SS / .SZ）
    if re.match(r'^\d{6}\.(SS|SZ)$', code, re.IGNORECASE):
        return _fetch_yf(code, f'A股 {code}')

    # 港股（HK + 数字 → 0700.HK 格式）
    if re.match(r'^HK\d+$', code, re.IGNORECASE):
        hk_num = code[2:].lstrip('0') or '0'
        hk_sym = hk_num.zfill(4) + '.HK'
        return _fetch_yf(hk_sym, f'港股 {hk_sym}')

    # SAP：价格用法兰克福 SAP.DE（EUR），基本面另走 yf_symbols.json 中的 SAP ADR
    if code == 'SAP.DE':
        return _fetch_yf('SAP.DE', 'SAP 法兰克福 (EUR)')

    # 无法自动拉取（固定收益等）
    return {
        'price':  None,
        'date':   None,
        'status': 'manual',
        'msg':    '需手动填写',
    }


# ── 公开 API ──────────────────────────────────────────────

def fetch_latest_prices(raw_df, data_dir: str = None) -> dict:
    """批量拉取持仓最新价格。

    Args:
        raw_df:   portfolio.csv 的 DataFrame
        data_dir: $FAMILYFUND_DATA 路径，用于读取 yf_symbols.json 区分基金/股票

    Returns:
        {code: {'price', 'date', 'status', 'msg'}, ...}
        status: 'ok' | 'manual' | 'error'
    """
    # 读取 yf_symbols 映射，有映射的 6位数字代码视为股票而非基金
    yf_map = {}
    if data_dir:
        try:
            from fundamentals import load_yf_symbols, get_yf_symbol
            raw = load_yf_symbols(data_dir)
            # 提取 code → yf_symbol 映射（排除内部 key）
            yf_map = {
                k: get_yf_symbol(raw, k)
                for k in raw
                if not k.startswith('_') and get_yf_symbol(raw, k)
            }
        except Exception:
            pass

    latest_date = raw_df['Date'].max()
    holdings = raw_df[raw_df['Date'] == latest_date]
    codes = holdings['Code'].unique().tolist()

    results = {}
    for code in codes:
        # 6位数字且在 yf_symbols 中 → A股个股，走 yfinance
        if re.match(r'^\d{6}$', code) and code in yf_map:
            results[code] = _fetch_yf(yf_map[code], f'A股 {code}')
        else:
            results[code] = _route(code)
    return results
