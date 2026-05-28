"""position_percentile.py — 个股 PB/PE 历史分位计算（F4 实施，2026-05-23）。

支持范围：
- A 股（.SS / .SZ）：akshare stock_value_em，5 年 PE(TTM) / 市净率分位
- 港股（.HK）：yfinance 当前 PE/PB + 价格 5 年分位（PB 历史不精确，用价格分位代理）

数据缓存：
- iCloud `position_percentile_cache.json`，TTL 7 天（周频更新与调仓周期对齐）

公开 API：
- get_position_data(symbol, force_refresh=False) → dict
- get_pool_position_table(reports_dir, force_refresh=False) → list[dict]（个股池所有标的）
"""

import json
import os
import re
from datetime import datetime, timedelta


# ── 缓存路径 ──────────────────────────────────────────────

def _cache_path() -> str:
    """位置数据缓存路径（iCloud 同步）。"""
    data_dir = os.environ.get('FAMILYFUND_DATA', '/app/data')
    return os.path.join(data_dir, 'position_percentile_cache.json')


CACHE_TTL_DAYS = 7


def _load_cache() -> dict:
    path = _cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _is_fresh(entry: dict) -> bool:
    """缓存条目是否在 TTL 内。"""
    updated = entry.get('updated', '')
    if not updated:
        return False
    try:
        ts = datetime.fromisoformat(updated)
    except Exception:
        return False
    return (datetime.now() - ts) < timedelta(days=CACHE_TTL_DAYS)


# ── A 股：akshare stock_value_em ─────────────────────────

def _fetch_a_share(symbol: str) -> dict | None:
    """A 股 PE/PB 5 年历史分位。

    Args:
        symbol: 6 位数字代码（如 '600309'），不含 .SS / .SZ 后缀

    Returns:
        {
            'symbol': str,
            'market': 'A',
            'current_pe_ttm': float,
            'current_pb': float,
            'pe_pct_5y': float,  # 0-100
            'pb_pct_5y': float,  # 0-100
            'pe_min_5y': float, 'pe_max_5y': float,
            'pb_min_5y': float, 'pb_max_5y': float,
            'data_start': str (YYYY-MM-DD),
            'data_end': str,
            'updated': str (ISO),
        }
        或 None（拉取失败）
    """
    try:
        import akshare as ak
        import pandas as pd
        df = ak.stock_value_em(symbol=symbol)
        if df is None or len(df) == 0:
            return None

        df['数据日期'] = pd.to_datetime(df['数据日期'])
        df = df.sort_values('数据日期').reset_index(drop=True)

        # 5 年窗口
        cutoff = pd.Timestamp.today() - pd.Timedelta(days=365 * 5)
        df_5y = df[df['数据日期'] >= cutoff].copy()
        if len(df_5y) < 30:
            df_5y = df  # 数据不足 5 年时用全量

        # 过滤异常值（PE 负值、极端值）
        df_5y_pe = df_5y[df_5y['PE(TTM)'].notna() & (df_5y['PE(TTM)'] > 0) & (df_5y['PE(TTM)'] < 1000)]
        df_5y_pb = df_5y[df_5y['市净率'].notna() & (df_5y['市净率'] > 0) & (df_5y['市净率'] < 100)]

        cur_pe = float(df['PE(TTM)'].iloc[-1]) if pd.notna(df['PE(TTM)'].iloc[-1]) else None
        cur_pb = float(df['市净率'].iloc[-1]) if pd.notna(df['市净率'].iloc[-1]) else None
        cur_price = float(df['当日收盘价'].iloc[-1]) if pd.notna(df['当日收盘价'].iloc[-1]) else None

        pe_pct = float((df_5y_pe['PE(TTM)'] <= cur_pe).mean() * 100) if cur_pe and len(df_5y_pe) > 0 else None
        pb_pct = float((df_5y_pb['市净率'] <= cur_pb).mean() * 100) if cur_pb and len(df_5y_pb) > 0 else None

        return {
            'symbol': symbol,
            'market': 'A',
            'current_pe_ttm': cur_pe,
            'current_pb': cur_pb,
            'current_price': cur_price,
            'currency': 'CNY',
            'pe_pct_5y': pe_pct,
            'pb_pct_5y': pb_pct,
            'pe_min_5y': float(df_5y_pe['PE(TTM)'].min()) if len(df_5y_pe) > 0 else None,
            'pe_max_5y': float(df_5y_pe['PE(TTM)'].max()) if len(df_5y_pe) > 0 else None,
            'pb_min_5y': float(df_5y_pb['市净率'].min()) if len(df_5y_pb) > 0 else None,
            'pb_max_5y': float(df_5y_pb['市净率'].max()) if len(df_5y_pb) > 0 else None,
            'data_start': df_5y['数据日期'].iloc[0].strftime('%Y-%m-%d'),
            'data_end': df_5y['数据日期'].iloc[-1].strftime('%Y-%m-%d'),
            'updated': datetime.now().isoformat(),
        }
    except Exception as e:
        print(f'[position_percentile] A 股 {symbol} 拉取失败: {e}')
        return None


# ── 港股：yfinance 当前 + 价格分位代理 ──────────────────────

def _fetch_eniu_reference(symbol: str) -> dict:
    """从 eniu.com 拉港股 PE/PB 历史，返回长期参考统计（不算分位）。

    eniu 数据更新已停在 2022-07-13，作为"长期估值参考"使用，不作精确分位。

    Args:
        symbol: '0700.HK' / '00700.HK' 等格式

    Returns:
        {
            'eniu_pe_min': float, 'eniu_pe_max': float, 'eniu_pe_median': float,
            'eniu_pb_min': float, 'eniu_pb_max': float, 'eniu_pb_median': float,
            'eniu_data_start': 'YYYY-MM-DD', 'eniu_data_end': 'YYYY-MM-DD',
        }
        失败时返回 {}（不阻断主流程）
    """
    try:
        import akshare as ak
        # eniu 用 'hk00700' 格式（hk 前缀 + 5 位数字代码）
        m = re.match(r'^(\d+)(?:\.HK)?$', symbol, re.IGNORECASE)
        if not m:
            return {}
        num = m.group(1).lstrip('0') or '0'
        eniu_code = 'hk' + num.zfill(5)

        out = {}
        try:
            pb = ak.stock_hk_indicator_eniu(symbol=eniu_code, indicator='市净率')
            if pb is not None and len(pb) > 0 and 'pb' in pb.columns:
                out['eniu_pb_min'] = round(float(pb['pb'].min()), 2)
                out['eniu_pb_max'] = round(float(pb['pb'].max()), 2)
                out['eniu_pb_median'] = round(float(pb['pb'].median()), 2)
                out['eniu_data_start'] = str(pb['date'].iloc[0])[:10]
                out['eniu_data_end'] = str(pb['date'].iloc[-1])[:10]
        except Exception:
            pass

        try:
            pe = ak.stock_hk_indicator_eniu(symbol=eniu_code, indicator='市盈率')
            if pe is not None and len(pe) > 0 and 'pe' in pe.columns:
                # 过滤负 PE
                pe_pos = pe[pe['pe'] > 0]
                if len(pe_pos) > 0:
                    out['eniu_pe_min'] = round(float(pe_pos['pe'].min()), 2)
                    out['eniu_pe_max'] = round(float(pe_pos['pe'].max()), 2)
                    out['eniu_pe_median'] = round(float(pe_pos['pe'].median()), 2)
        except Exception:
            pass

        return out
    except Exception:
        return {}


def _fetch_hk_share(symbol: str) -> dict | None:
    """港股位置数据。

    数据组合：
    - 当前 PE/PB（yfinance info）
    - 价格 5 年分位（作为短期位置代理；yfinance 5y history）
    - eniu 长期 PE/PB 区间和中位数（不算分位，作为"历史估值参考轴"）
      注：eniu 数据更新停在 2022-07，仅用于看"当前值是历史的什么位置"

    Args:
        symbol: yfinance 港股代码（如 '0700.HK', '00883.HK'）
                注意 yfinance 实际识别 4 位数字格式（'0700.HK' 而非 '00700.HK'），
                本函数会自动归一化前导 0。

    Returns:
        dict 或 None
    """
    try:
        import yfinance as yf
        import pandas as pd

        # 归一化港股代码：'00700.HK' → '0700.HK'（yfinance 要求 4 位数字 + .HK）
        # 兼容 5 位（00700）/ 4 位（0700）/ 不带 0 的（700）
        m = re.match(r'^(\d+)\.HK$', symbol, re.IGNORECASE)
        if m:
            num = m.group(1).lstrip('0') or '0'
            yf_code = num.zfill(4) + '.HK'
        else:
            yf_code = symbol  # 兜底：原样使用

        t = yf.Ticker(yf_code)
        info = t.info or {}
        cur_pe = info.get('trailingPE')
        cur_pb = info.get('priceToBook')

        # 价格 5 年历史 → 价格分位（作为 PB 历史的代理）
        hist = t.history(period='5y')
        if hist is None or len(hist) == 0:
            return None
        cur_price = float(hist['Close'].iloc[-1])
        price_pct = float((hist['Close'] <= cur_price).mean() * 100)
        price_min = float(hist['Close'].min())
        price_max = float(hist['Close'].max())

        # 52 周高低（更近期）
        hist_52w = hist.iloc[-min(252, len(hist)):]
        year_high = float(hist_52w['Close'].max())
        year_low = float(hist_52w['Close'].min())
        pct_from_high = (cur_price / year_high - 1) * 100 if year_high else None
        pct_from_low = (cur_price / year_low - 1) * 100 if year_low else None

        # eniu 长期参考（PE/PB 区间 + 中位数，不算分位）
        eniu_ref = _fetch_eniu_reference(symbol)

        result = {
            'symbol': symbol,
            'yf_code': yf_code,
            'market': 'HK',
            'current_pe_ttm': cur_pe,
            'current_pb': cur_pb,
            'pe_pct_5y': None,   # 港股不可得（不做伪精确分位）
            'pb_pct_5y': None,
            'price_pct_5y': price_pct,  # 价格分位代理（短期位置）
            'price_min_5y': price_min,
            'price_max_5y': price_max,
            'current_price': cur_price,
            'year_high': year_high,
            'year_low': year_low,
            'pct_from_high': pct_from_high,
            'pct_from_low': pct_from_low,
            'data_start': hist.index[0].strftime('%Y-%m-%d'),
            'data_end': hist.index[-1].strftime('%Y-%m-%d'),
            'updated': datetime.now().isoformat(),
            'note': '港股 PE/PB 5 年分位不可得；价格 5y 分位作短期位置；eniu 长期 PE/PB 区间作估值参考（数据停在 2022-07）',
        }
        # 合并 eniu 字段（如果拉到）
        result.update(eniu_ref)
        return result
    except Exception as e:
        print(f'[position_percentile] 港股 {symbol} 拉取失败: {e}')
        return None


# ── 公开 API ────────────────────────────────────────────

def _normalize_symbol(symbol: str) -> tuple[str, str]:
    """把 ticker_map 的 yf_symbol 归一化为 (market, akshare_or_yf_symbol)。

    Returns:
        ('A', '600309') for A 股 - 6 位代码
        ('HK', '0700.HK') for 港股 - yfinance 格式
        ('OTHER', 原 symbol) for 其他（如 SAP）
    """
    s = (symbol or '').upper().strip()
    if re.match(r'^\d{6}\.(SS|SZ)$', s):
        return 'A', s.split('.')[0]
    if re.match(r'^\d+\.HK$', s):
        return 'HK', s
    if re.match(r'^\d+$', s):
        return 'A', s  # 6 位数字默认 A 股
    return 'OTHER', s


def get_position_data(yf_symbol: str, force_refresh: bool = False) -> dict | None:
    """获取单个标的的位置数据（含缓存）。

    Args:
        yf_symbol: ticker_map 的 yf_symbol 字段（如 '600309.SS', '00700.HK', 'SAP'）
        force_refresh: True 时绕过缓存

    Returns:
        dict 或 None（OTHER 类型 / 拉取失败）
    """
    market, normalized = _normalize_symbol(yf_symbol)
    if market == 'OTHER':
        return None

    cache = _load_cache()
    cache_key = yf_symbol
    if not force_refresh:
        entry = cache.get(cache_key)
        if entry and _is_fresh(entry):
            return entry

    if market == 'A':
        result = _fetch_a_share(normalized)
    else:  # HK
        result = _fetch_hk_share(normalized)

    if result:
        cache[cache_key] = result
        _save_cache(cache)
    return result


def get_pool_position_table(reports_dir: str, force_refresh: bool = False) -> list[dict]:
    """返回个股池内所有标的的位置数据。

    Args:
        reports_dir: Finance Reports 目录（用于读 ticker_map.json）
        force_refresh: True 时绕过缓存

    Returns:
        list of dict，每条含 folder + position 数据
    """
    from research_library import load_ticker_map
    tm = load_ticker_map(reports_dir)
    rows = []
    for group in ('持仓', '观察'):
        for folder, info in tm.get(group, {}).items():
            yf_symbol = info.get('yf_symbol', '')
            if not yf_symbol:
                continue
            data = get_position_data(yf_symbol, force_refresh=force_refresh)
            rows.append({
                'folder': folder,
                'group': group,
                'yf_symbol': yf_symbol,
                'data': data,  # None 时表示拉取失败或不支持
            })
    return rows
