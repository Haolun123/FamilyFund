"""market_monitor.py — 市场温度计：乖离率监测 + PE×VIX/QVIX 定投倍数矩阵

支持标的：
  - CSI 300 (沪深300)      — akshare 日频
  - 中证A500               — akshare 日频
  - 黄金 (USD/oz)          — yfinance GC=F
  - 纳指100                — yfinance ^NDX
  - 标普500                — yfinance ^GSPC
  - VIX 恐慌指数           — yfinance ^VIX
  - 标普500 PE             — yfinance VOO trailingPE
  - 纳指100 PE             — yfinance QQQ trailingPE
  - A股 QVIX              — akshare index_option_300etf_qvix（300ETF期权隐含波动率）
  - CSI300 PE             — akshare stock_index_pe_lg(symbol='沪深300') 滚动市盈率
  - 中证500 PE（A500代理）  — akshare stock_index_pe_lg(symbol='中证500') 滚动市盈率
    ⚠️  中证A500 于 2023-11-17 发布，历史数据不足以建立可靠的百分位分布。
        暂用中证500作为代理：两者同为大中盘均衡指数，行业权重结构相近，
        PE走势高度相关。待A500积累3年以上数据后切换。

均线参考规则：
  - A股（CSI300/中证A500）：MA60 为主要参考
  - 美股/黄金（纳指/标普/黄金）：MA200 为主要参考

A股定投倍数矩阵说明：
  - 美股用绝对PE×VIX（历史区间稳定）
  - A股用PE百分位×QVIX百分位（A股PE历史极差8x-51x，百分位法更稳健）
  - QVIX分档：<30th 低波, 30-60th 正常, 60-80th 偏高, >80th 恐慌
  - PE分档：<30th 低估, 30-60th 合理, 60-80th 偏贵, >80th 高估

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
    ['暂停',  '暂停',  '暂停',  '0.3x'],   # PE > 32
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
    ['暂停',  '暂停',  '暂停',  '0.3x'],  # PE > 37
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

# ── A股 QVIX 信号分档（基于历史百分位，2015年至今）─────────────
# 历史参考：中位数 18.9，25-75% 区间 16.7-21.7，90th 24.8，最高 45.9
QVIX_LEVELS = [
    (17.2,  None,   '低波/平静',   '🔴'),   # < 30th pct
    (19.8,  17.2,   '正常波动',    '⚪'),   # 30-60th pct
    (22.5,  19.8,   '偏高',        '🟡'),   # 60-80th pct
    (None,  22.5,   '恐慌',        '🟢'),   # > 80th pct
]

# ── CSI300 PE×QVIX 定投倍数矩阵（百分位法）──────────────────────
# PE百分位分档（沪深300 滚动PE，2005年至今）：
#   <30th: ≤11.5   30-60th: 11.5-13.2   60-80th: 13.2-15.6   >80th: ≥15.6
# QVIX百分位分档：
#   <30th: ≤17.2   30-60th: 17.2-19.8   60-80th: 19.8-22.5   >80th: ≥22.5
CSI300_PE_BANDS  = [15.6, 13.2, 11.5]   # PE 行分界（> 各值）
CSI300_QVIX_BANDS = [17.2, 19.8, 22.5]  # QVIX 列分界（< 各值）

CSI300_MATRIX = [
    # QVIX: <30th    30-60th  60-80th  >80th
    ['暂停',  '暂停',  '0.3x',  '0.5x'],  # PE > 80th (高估)
    ['0.5x',  '0.8x',  '1.2x',  '2.0x'],  # PE 60-80th (偏贵)
    ['1.0x',  '1.5x',  '2.5x',  '4.0x'],  # PE 30-60th (合理)
    ['2.0x',  '3.0x',  '5.0x',  '顶格'],  # PE < 30th (低估)
]

# ── 中证A500 PE×QVIX 定投倍数矩阵（百分位法，以中证500为代理）─────
# ⚠️  中证A500（000510）于2023-11-17发布，历史数据不足。
#     此处PE数据来自中证500（000905）作为临时代理。
#     原因：中证500与A500同为大中盘均衡指数，行业结构相近，
#     PE走势高度相关（当前A500≈21.8 vs 中证500≈28.5，绝对值有差异但
#     百分位分布特征相似）。待A500积累3年以上历史后切换。
# PE百分位分档（中证500 滚动PE，2005年至今）：
#   <30th: ≤21.2   30-60th: 21.2-27.3   60-80th: 27.3-37.4   >80th: ≥37.4
# QVIX共用同一指数（300ETF期权，代表A股整体隐含波动率）
CSI_A500_PE_BANDS  = [37.4, 27.3, 21.2]   # PE 行分界（> 各值）
CSI_A500_QVIX_BANDS = [17.2, 19.8, 22.5]  # 同 CSI300，共用 QVIX

CSI_A500_MATRIX = [
    # QVIX: <30th    30-60th  60-80th  >80th
    ['暂停',  '暂停',  '0.3x',  '0.5x'],  # PE > 80th (高估)
    ['0.5x',  '0.8x',  '1.2x',  '2.0x'],  # PE 60-80th (偏贵)
    ['1.0x',  '1.5x',  '2.5x',  '4.0x'],  # PE 30-60th (合理)
    ['2.0x',  '3.0x',  '5.0x',  '顶格'],  # PE < 30th (低估)
]

# ── 黄金 乖离率(MA200)×VIX 定投倍数矩阵 ──────────────────────
# 黄金无PE，用 MA200 乖离率作为估值锚（对冲/压舱石角色，顶格=5x）
# 乖离率行分界（从高到低）：> +20%, +10~+20%, -5~+10%, -10~-5%, < -10%
GOLD_BIAS_BANDS = [20.0, 10.0, -5.0, -10.0]   # 乖离率行分界（> 各值）
GOLD_VIX_BANDS  = [18, 25, 35]                 # VIX 列分界（< 各值）

GOLD_MATRIX = [
    # VIX: <18     18-25    25-35    >35
    ['暂停',  '暂停',  '暂停',  '0.5x'],   # 乖离 > +20%（严重超买）
    ['暂停',  '0.3x',  '0.5x',  '1.0x'],   # 乖离 +10% ~ +20%（偏高）
    ['0.5x',  '1.0x',  '1.5x',  '2.0x'],   # 乖离 -5% ~ +10%（正常）
    ['1.0x',  '1.5x',  '2.5x',  '3.5x'],   # 乖离 -10% ~ -5%（偏低）
    ['1.5x',  '2.5x',  '4.0x',  '顶格'],   # 乖离 < -10%（明显超卖）
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


def _fetch_akshare_pe(symbol: str) -> float | None:
    """拉取 akshare 指数滚动市盈率（最新值）。symbol: '沪深300' 或 '中证500'。"""
    try:
        import akshare as ak
        df = ak.stock_index_pe_lg(symbol=symbol)
        val = float(df['滚动市盈率'].iloc[-1])
        return round(val, 2) if val > 0 else None
    except Exception:
        return None


def _fetch_qvix() -> float | None:
    """拉取 A股 QVIX（300ETF期权隐含波动率指数，最新收盘值）。"""
    try:
        import akshare as ak
        df = ak.index_option_300etf_qvix()
        return round(float(df['close'].iloc[-1]), 2)
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
            'csi300':       {'price': ..., 'ma60': ..., 'ma200': ..., 'updated': '...'},
            'csi_a500':     {...},
            'gold':         {...},
            'ndx100':       {...},
            'sp500':        {...},
            'vix':          {'price': ..., 'updated': '...'},
            'qvix':         {'price': ..., 'updated': '...'},
            'pe_sp500':     {'value': ..., 'source': ..., 'manual_override': ..., 'updated': '...'},
            'pe_ndx100':    {...},
            'pe_csi300':    {'value': ..., 'source': 'akshare 沪深300 滚动PE', 'updated': '...'},
            'pe_csi_a500':  {'value': ..., 'source': 'akshare 中证500 滚动PE（A500代理）', 'updated': '...'},
            'meta':         {'<key>_updated': '...', ...}
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

    # ── QVIX（A股隐含波动率，300ETF期权）──
    if _should_fetch('qvix'):
        val = _fetch_qvix()
        if val:
            cache['qvix'] = {'price': val}
            cache['qvix_updated'] = today
            cache_dirty = True

    # ── 美股PE（仅在无 manual_override 时自动拉取）──
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

    # ── A股PE（akshare，不支持手动覆盖）──
    for pe_key, ak_symbol, source_note in [
        ('pe_csi300',   '沪深300', 'akshare 沪深300 滚动PE'),
        ('pe_csi_a500', '中证500', 'akshare 中证500 滚动PE（A500代理，待A500积累3年数据后切换）'),
    ]:
        if not _should_fetch(pe_key):
            continue
        val = _fetch_akshare_pe(ak_symbol)
        if val:
            cache[pe_key] = {'value': val, 'source': source_note}
            cache[f'{pe_key}_updated'] = today
            cache_dirty = True

    if cache_dirty:
        _save_cache(cache)

    # ── 组装结果 ──
    all_keys = list(TARGETS.keys()) + ['vix', 'qvix', 'pe_sp500', 'pe_ndx100', 'pe_csi300', 'pe_csi_a500']
    result = {}
    for key in all_keys:
        result[key] = cache.get(key)

    # meta：各项数据的更新时间
    meta = {}
    for key in all_keys:
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
        '暂停' / '0.3x' / ... / '顶格'
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


def compute_qvix_signal(qvix: float | None) -> tuple[str, str]:
    """返回 A股 QVIX 的 (信号文字, emoji)。"""
    if qvix is None:
        return '无数据', '—'
    for upper, lower, label, emoji in QVIX_LEVELS:
        above_lower = (lower is None) or (qvix > lower)
        below_upper = (upper is None) or (qvix <= upper)
        if above_lower and below_upper:
            return label, emoji
    return '—', '—'


def lookup_a_share_multiplier(pe: float | None, qvix: float | None, target: str) -> str:
    """查 A股 PE×QVIX 矩阵（百分位法），返回定投倍数建议。

    Args:
        pe:     PE 值（CSI300 用沪深300滚动PE；中证A500 用中证500滚动PE作代理）
        qvix:   QVIX 值（300ETF期权隐含波动率，CSI300/A500 共用）
        target: 'csi300' 或 'csi_a500'

    Returns:
        '暂停' / '0.3x' / ... / '顶格'
        若 pe 或 qvix 为 None，返回 '—'
    """
    if pe is None or qvix is None:
        return '—'

    if target == 'csi300':
        pe_bands   = CSI300_PE_BANDS
        qvix_bands = CSI300_QVIX_BANDS
        matrix     = CSI300_MATRIX
    else:
        pe_bands   = CSI_A500_PE_BANDS
        qvix_bands = CSI_A500_QVIX_BANDS
        matrix     = CSI_A500_MATRIX

    # PE 行定位（从高到低找第一个 pe > band）
    pe_row = len(pe_bands)
    for i, band in enumerate(pe_bands):
        if pe > band:
            pe_row = i
            break

    # QVIX 列定位（< 各分界值）
    qvix_col = len(qvix_bands)
    for j, band in enumerate(qvix_bands):
        if qvix < band:
            qvix_col = j
            break

    return matrix[pe_row][qvix_col]


def lookup_gold_multiplier(bias200: float | None, vix: float | None) -> str:
    """查黄金 乖离率(MA200)×VIX 矩阵，返回定投倍数建议。

    Args:
        bias200: 黄金 MA200 乖离率（%），如 +5.2 或 -8.1
        vix:     VIX 值

    Returns:
        '暂停' / '0.5x' / ... / '顶格'
        若任一为 None，返回 '—'
    """
    if bias200 is None or vix is None:
        return '—'

    # 乖离率行定位（从高到低找第一个 bias > band）
    bias_row = len(GOLD_BIAS_BANDS)  # 默认最后行（< 最小值）
    for i, band in enumerate(GOLD_BIAS_BANDS):
        if bias200 > band:
            bias_row = i
            break

    # VIX 列定位（< 各分界值）
    vix_col = len(GOLD_VIX_BANDS)
    for j, band in enumerate(GOLD_VIX_BANDS):
        if vix < band:
            vix_col = j
            break

    return GOLD_MATRIX[bias_row][vix_col]
