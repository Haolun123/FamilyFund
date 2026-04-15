"""market_monitor.py — 市场温度计：乖离率监测 + PE×VIX 定投倍数矩阵

支持标的：
  - CSI 300 (沪深300)      — akshare 日频
  - 中证A500               — akshare 日频
  - 黄金 (USD/oz)          — yfinance GC=F
  - 纳指100                — yfinance ^NDX
  - 标普500                — yfinance ^GSPC
  - VIX 恐慌指数           — yfinance ^VIX
  - 标普500 PE             — yfinance VOO trailingPE
  - 纳指100 PE             — yfinance QQQ trailingPE

均线参考规则：
  - A股（CSI300/中证A500）：MA60 为主要参考
  - 美股/黄金（纳指/标普/黄金）：MA200 为主要参考

缓存策略：
  - 缓存文件：$FAMILYFUND_DATA/market_cache.json（iCloud 目录）
  - 当天已更新则直接读缓存；过期则尝试拉新，失败时 fallback 到旧缓存
  - manual_override 非 null 时 PE 使用手动值
  - 各标的独立更新，某个接口失败不影响其他
"""

import json
import os
from datetime import date

import pandas as pd

# ── 缓存文件路径 ──────────────────────────────────────────────
_DATA_DIR = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'),
)
CACHE_PATH = os.path.join(_DATA_DIR, 'market_cache.json')

# ── 标的配置 ──────────────────────────────────────────────────
TARGETS = {
    'csi300':   {'name': 'CSI 300 沪深300', 'source': 'akshare', 'symbol': 'sh000300', 'primary_ma': 60},
    'csi_a500': {'name': '中证A500',         'source': 'akshare', 'symbol': 'sh000510', 'primary_ma': 60},
    'gold':     {'name': '黄金 (USD/oz)',    'source': 'yfinance', 'symbol': 'GC=F',    'primary_ma': 200},
    'ndx100':   {'name': '纳指100',          'source': 'yfinance', 'symbol': '^NDX',    'primary_ma': 200},
    'sp500':    {'name': '标普500',          'source': 'yfinance', 'symbol': '^GSPC',   'primary_ma': 200},
}

# ── 乖离率信号分档 ────────────────────────────────────────────
# 乖离率 = (price - MA) / MA * 100
BIAS_LEVELS = [
    (-10.0,  None,   '深度超卖', '🔵'),   # ≤ -10%
    (-5.0,  -10.0,   '超卖',     '🟢'),   # -10% ~ -5%
    (8.0,   -5.0,    '正常',     '⚪'),   # -5% ~ +8%
    (15.0,   8.0,    '偏高',     '🟡'),   # +8% ~ +15%
    (None,   15.0,   '超买',     '🔴'),   # > +15%
]

# ── 标普500 PE×VIX 定投倍数矩阵 ──────────────────────────────
# PE 分档边界（从高到低，含义：> 各值时对应行）
SP500_PE_BANDS  = [32, 29, 26, 23, 20, 17, 14]
SP500_VIX_BANDS = [18, 25, 35]   # VIX 列分界（< 各值）

SP500_MATRIX = [
    # VIX: <18      18-25    25-35    >35
    ['暂停',  '暂停',  '观望',  '0.3x'],   # PE > 32
    ['暂停',  '0.3x',  '0.5x',  '0.8x'],  # 29-32
    ['0.3x',  '0.5x',  '0.8x',  '1.2x'],  # 26-29
    ['0.6x',  '0.8x',  '1.5x',  '2.0x'],  # 23-26
    ['1.2x',  '2.0x',  '3.0x',  '4.5x'],  # 20-23
    ['2.5x',  '4.0x',  '7.0x', '10.0x'],  # 17-20
    ['5.0x',  '8.0x', '14.0x',  '顶格'],  # 14-17
    ['顶格',  '顶格',  '顶格',  '顶格'],  # < 14
]

# ── 纳指100 PE×VIX 定投倍数矩阵 ──────────────────────────────
NDX100_PE_BANDS  = [37, 35, 32, 28, 24, 20, 16]
NDX100_VIX_BANDS = [18, 24, 31]

NDX100_MATRIX = [
    # VIX: <18      18-24    24-31    >31
    ['暂停',  '暂停',  '观望',  '0.3x'],  # PE > 37
    ['暂停',  '0.2x',  '0.4x',  '0.6x'],  # 35-37
    ['0.3x',  '0.5x',  '0.8x',  '1.2x'],  # 32-35
    ['0.6x',  '0.8x',  '1.2x',  '2.0x'],  # 28-32
    ['1.0x',  '1.5x',  '2.5x',  '4.0x'],  # 24-28
    ['2.0x',  '3.5x',  '6.0x',  '9.0x'],  # 20-24
    ['4.0x',  '7.0x', '12.0x',  '顶格'],  # 16-20
    ['顶格',  '顶格',  '顶格',  '顶格'],  # < 16
]

# VIX 信号分档
VIX_LEVELS = [
    (15.0,  None,   '贪婪/低波',   '🔴'),
    (20.0,  15.0,   '正常波动',    '⚪'),
    (30.0,  20.0,   '警觉',        '🟡'),
    (40.0,  30.0,   '恐慌',        '🟢'),
    (None,  40.0,   '极端恐慌',    '🔵'),
]

# 标普PE信号分档
SP500_PE_SIGNAL = [
    (18.0,  None,   '低估',   '🟢'),
    (22.0,  18.0,   '合理',   '⚪'),
    (28.0,  22.0,   '偏贵',   '🟡'),
    (None,  28.0,   '高估',   '🔴'),
]

# 纳指PE信号分档（历史中枢更高）
NDX100_PE_SIGNAL = [
    (25.0,  None,   '低估',   '🟢'),
    (35.0,  25.0,   '合理',   '⚪'),
    (45.0,  35.0,   '偏贵',   '🟡'),
    (None,  45.0,   '高估',   '🔴'),
]


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
    ts = cache.get(f'{key}_updated')
    if not ts:
        return False
    return ts == date.today().isoformat()


# ══════════════════════════════════════════════════════════════
# Fetchers
# ══════════════════════════════════════════════════════════════

def _fetch_akshare(symbol: str) -> pd.Series | None:
    """拉取 akshare 日线，返回 close 序列（index=date str）。"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=symbol)
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df = df.sort_values('date').set_index('date')
        return df['close'].astype(float)
    except Exception:
        return None


def _fetch_yfinance(symbol: str, period: str = '1y') -> pd.Series | None:
    """拉取 yfinance 日线，返回 close 序列（index=date str）。"""
    try:
        import yfinance as yf
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index).strftime('%Y-%m-%d')
        close = df['Close'].squeeze()
        return close.astype(float)
    except Exception:
        return None


def _fetch_pe(symbol: str) -> float | None:
    """拉取 ETF 的 trailingPE。"""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        pe = info.get('trailingPE')
        if pe and float(pe) > 0:
            return round(float(pe), 2)
        return None
    except Exception:
        return None


def _compute_ma(series: pd.Series, window: int) -> float | None:
    """计算序列最新 MA 值，不足窗口期返回 None。"""
    if series is None or len(series) < window:
        return None
    return round(float(series.iloc[-window:].mean()), 4)


def _entry_from_series(series: pd.Series) -> dict | None:
    """从价格序列提取 price/ma60/ma200。"""
    if series is None or len(series) == 0:
        return None
    price = round(float(series.iloc[-1]), 4)
    ma60  = _compute_ma(series, 60)
    ma200 = _compute_ma(series, 200)
    return {'price': price, 'ma60': ma60, 'ma200': ma200}


# ══════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════

def get_market_data(force_refresh: bool = False) -> dict:
    """获取所有市场数据（带缓存和 fallback）。

    Returns:
        dict: {
            'csi300':    {'price': ..., 'ma60': ..., 'ma200': ..., 'updated': '...'},
            'csi_a500':  {...},
            'gold':      {...},
            'ndx100':    {...},
            'sp500':     {...},
            'vix':       {'price': ..., 'updated': '...'},
            'pe_sp500':  {'value': ..., 'source': ..., 'manual_override': ..., 'updated': '...'},
            'pe_ndx100': {...},
            'meta':      {'<key>_updated': '...', ...}
        }
        各字段为 None 表示完全不可用（无缓存且拉取失败）
    """
    cache = _load_cache()
    cache_dirty = False
    today = date.today().isoformat()

    def _should_fetch(key: str) -> bool:
        return force_refresh or not _cache_is_fresh(cache, key)

    # ── 价格标的 ──
    for key, cfg in TARGETS.items():
        if not _should_fetch(key):
            continue
        if cfg['source'] == 'akshare':
            series = _fetch_akshare(cfg['symbol'])
        else:
            series = _fetch_yfinance(cfg['symbol'], period='400d')
        entry = _entry_from_series(series)
        if entry:
            cache[key] = entry
            cache[f'{key}_updated'] = today
            cache_dirty = True

    # ── VIX ──
    if _should_fetch('vix'):
        series = _fetch_yfinance('^VIX', period='5d')
        if series is not None and len(series) > 0:
            cache['vix'] = {'price': round(float(series.iloc[-1]), 2)}
            cache['vix_updated'] = today
            cache_dirty = True

    # ── PE（仅在无 manual_override 时自动拉取）──
    for pe_key, ticker in [('pe_sp500', 'VOO'), ('pe_ndx100', 'QQQ')]:
        existing = cache.get(pe_key, {})
        if existing.get('manual_override') is not None:
            continue  # 手动值优先，不覆盖
        if not _should_fetch(pe_key):
            continue
        val = _fetch_pe(ticker)
        if val:
            cache[pe_key] = {
                'value': val,
                'source': f'{ticker} trailingPE',
                'manual_override': existing.get('manual_override'),
                'updated': today,
            }
            cache[f'{pe_key}_updated'] = today
            cache_dirty = True

    if cache_dirty:
        _save_cache(cache)

    # ── 组装结果 ──
    result = {}
    for key in list(TARGETS.keys()) + ['vix', 'pe_sp500', 'pe_ndx100']:
        result[key] = cache.get(key)

    # meta：各项数据的更新时间
    meta = {}
    for key in list(TARGETS.keys()) + ['vix', 'pe_sp500', 'pe_ndx100']:
        meta[f'{key}_updated'] = cache.get(f'{key}_updated', '未知')
    result['meta'] = meta

    return result


def set_pe_override(target: str, value: float | None):
    """设置 PE 手动覆盖值。target: 'sp500' 或 'ndx100'。value=None 清除覆盖。"""
    key = f'pe_{target}'
    cache = _load_cache()
    entry = cache.get(key, {})
    entry['manual_override'] = value
    if value is not None:
        entry['source'] = '手动输入'
        entry['value'] = value
    cache[key] = entry
    _save_cache(cache)


def compute_bias(entry: dict) -> dict:
    """计算单个标的的乖离率和信号。

    Args:
        entry: {'price': float, 'ma60': float|None, 'ma200': float|None}

    Returns:
        {
            'bias60':   float|None,   # MA60 乖离率（%）
            'bias200':  float|None,   # MA200 乖离率（%）
            'signal60': str,          # MA60 信号文字
            'signal200': str,         # MA200 信号文字
            'emoji60':  str,          # MA60 emoji
            'emoji200': str,          # MA200 emoji
        }
    """
    price = entry.get('price')
    ma60  = entry.get('ma60')
    ma200 = entry.get('ma200')

    def _bias(ma):
        if price is None or ma is None or ma == 0:
            return None
        return round((price - ma) / ma * 100, 2)

    def _signal(bias, levels):
        if bias is None:
            return '无数据', '—'
        for upper, lower, label, emoji in levels:
            above_lower = (lower is None) or (bias > lower)
            below_upper = (upper is None) or (bias <= upper)
            if above_lower and below_upper:
                return label, emoji
        return '—', '—'

    b60  = _bias(ma60)
    b200 = _bias(ma200)
    s60,  e60  = _signal(b60,  BIAS_LEVELS)
    s200, e200 = _signal(b200, BIAS_LEVELS)

    return {
        'bias60':    b60,
        'bias200':   b200,
        'signal60':  s60,
        'signal200': s200,
        'emoji60':   e60,
        'emoji200':  e200,
    }


def compute_vix_signal(vix: float | None) -> tuple[str, str]:
    """返回 VIX 的 (信号文字, emoji)。"""
    if vix is None:
        return '无数据', '—'
    for upper, lower, label, emoji in VIX_LEVELS:
        above_lower = (lower is None) or (vix > lower)
        below_upper = (upper is None) or (vix <= upper)
        if above_lower and below_upper:
            return label, emoji
    return '—', '—'


def compute_pe_signal(pe: float | None, target: str) -> tuple[str, str]:
    """返回 PE 的 (信号文字, emoji)。target: 'sp500' 或 'ndx100'。"""
    if pe is None:
        return '无数据', '—'
    levels = SP500_PE_SIGNAL if target == 'sp500' else NDX100_PE_SIGNAL
    for upper, lower, label, emoji in levels:
        above_lower = (lower is None) or (pe > lower)
        below_upper = (upper is None) or (pe <= upper)
        if above_lower and below_upper:
            return label, emoji
    return '—', '—'


def lookup_multiplier(pe: float, vix: float, target: str) -> str:
    """查 PE×VIX 矩阵，返回定投倍数建议字符串。

    Args:
        pe:     PE 值
        vix:    VIX 值
        target: 'sp500' 或 'ndx100'

    Returns:
        '暂停' / '观望' / '0.3x' / ... / '顶格'
        若 pe 或 vix 为 None，返回 '—'
    """
    if pe is None or vix is None:
        return '—'

    if target == 'sp500':
        pe_bands  = SP500_PE_BANDS
        vix_bands = SP500_VIX_BANDS
        matrix    = SP500_MATRIX
    else:
        pe_bands  = NDX100_PE_BANDS
        vix_bands = NDX100_VIX_BANDS
        matrix    = NDX100_MATRIX

    # PE 行定位（从高到低找第一个 pe > band）
    pe_row = len(pe_bands)  # 默认最后行（< 最小值）
    for i, band in enumerate(pe_bands):
        if pe > band:
            pe_row = i
            break

    # VIX 列定位（< 各分界值）
    vix_col = len(vix_bands)  # 默认最后列（> 最大值）
    for j, band in enumerate(vix_bands):
        if vix < band:
            vix_col = j
            break

    return matrix[pe_row][vix_col]
