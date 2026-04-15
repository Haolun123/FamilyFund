"""notifier.py — 企业微信群机器人推送

职责：
  1. 判断今天是否为 A 股交易日
  2. 将 market_monitor.get_market_data() 的结果格式化为 Markdown 消息
  3. POST 到企业微信群机器人 Webhook（带重试）

环境变量：
  WXWORK_WEBHOOK_URL  企业微信群机器人 Webhook 完整 URL（必填）
"""

import json
import logging
import os
import time
from datetime import date

import requests

from market_monitor import (
    TARGETS,
    compute_bias,
    compute_pe_signal,
    compute_qvix_signal,
    compute_vix_signal,
    lookup_a_share_multiplier,
    lookup_multiplier,
)

logger = logging.getLogger(__name__)

_WEBHOOK_URL = os.environ.get('WXWORK_WEBHOOK_URL', '')
_TIMEOUT     = 10   # HTTP 请求超时秒数
_MAX_RETRIES = 3
_RETRY_DELAY = 5    # 重试间隔秒数


# ══════════════════════════════════════════════════════════════
# 交易日判断
# ══════════════════════════════════════════════════════════════

def _is_trading_day(today: date | None = None) -> bool:
    """判断指定日期（默认今天）是否为 A 股交易日。

    使用 akshare.tool_trade_date_hist_sina() 获取完整交易日历。
    失败时保守返回 True（宁可多推送，不漏推送）。
    """
    if today is None:
        today = date.today()

    # 快速排除周末
    if today.weekday() >= 5:
        return False

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        # 列名通常为 'trade_date'，值为 datetime.date 或字符串
        col = df.columns[0]
        dates = set(str(d)[:10] for d in df[col])
        return today.isoformat() in dates
    except Exception as e:
        logger.warning(f"交易日历获取失败，保守返回 True: {e}")
        return True


# ══════════════════════════════════════════════════════════════
# 消息格式化
# ══════════════════════════════════════════════════════════════

def _fmt_bias_cell(bias_val: float | None, signal: str, emoji: str) -> str:
    """格式化单个乖离率单元格，如 '⚪ 正常 (+2.1%)'。"""
    if bias_val is None:
        return '⚠️ 无数据'
    sign = '+' if bias_val >= 0 else ''
    return f'{emoji} {signal} ({sign}{bias_val:.1f}%)'


def _format_message(market_data: dict) -> str:
    """将 market_data 渲染为企业微信 Markdown 字符串。"""
    today = date.today().isoformat()
    lines = [f'📊 **市场温度计** {today}', '']

    # ── 乖离率 ──
    lines.append('**乖离率**')
    for key, cfg in TARGETS.items():
        entry = market_data.get(key)
        if entry is None:
            lines.append(f'> {cfg["name"]}　⚠️ 数据不可用')
            continue

        bias   = compute_bias(entry)
        p_ma   = cfg['primary_ma']
        signal = bias[f'signal{p_ma}']
        emoji  = bias[f'emoji{p_ma}']
        b_val  = bias[f'bias{p_ma}']
        price  = entry.get('price', 0)

        cell = _fmt_bias_cell(b_val, signal, emoji)
        lines.append(f'> {cfg["name"]}　{price:,.0f}　MA{p_ma} {cell}')

    lines.append('')

    # ── 恐慌指数 ──
    lines.append('**恐慌指数**')
    vix_entry  = market_data.get('vix')
    qvix_entry = market_data.get('qvix')
    vix_val    = vix_entry.get('price')  if vix_entry  else None
    qvix_val   = qvix_entry.get('price') if qvix_entry else None

    vix_label,  vix_emoji  = compute_vix_signal(vix_val)
    qvix_label, qvix_emoji = compute_qvix_signal(qvix_val)

    vix_str  = f'{vix_val:.1f} {vix_emoji} {vix_label}'   if vix_val  else '⚠️ 无数据'
    qvix_str = f'{qvix_val:.1f} {qvix_emoji} {qvix_label}' if qvix_val else '⚠️ 无数据'
    lines.append(f'> VIX {vix_str}　｜　QVIX {qvix_str}')
    lines.append('')

    # ── 定投倍数 ──
    lines.append('**定投倍数建议**')

    # 美股
    pe_sp  = _get_pe(market_data, 'pe_sp500')
    pe_ndx = _get_pe(market_data, 'pe_ndx100')
    mult_sp  = lookup_multiplier(pe_sp,  vix_val, 'sp500')
    mult_ndx = lookup_multiplier(pe_ndx, vix_val, 'ndx100')

    sp_str  = f'PE {pe_sp:.1f} × VIX {vix_val:.1f} → **{mult_sp}**' if pe_sp and vix_val else '⚠️ 数据不完整'
    ndx_str = f'PE {pe_ndx:.1f} × VIX {vix_val:.1f} → **{mult_ndx}**' if pe_ndx and vix_val else '⚠️ 数据不完整'
    lines.append(f'> 标普500　{sp_str}')
    lines.append(f'> 纳指100　{ndx_str}')

    # A股
    pe_csi300   = _get_pe(market_data, 'pe_csi300')
    pe_csi_a500 = _get_pe(market_data, 'pe_csi_a500')
    mult_csi300   = lookup_a_share_multiplier(pe_csi300,   qvix_val, 'csi300')
    mult_csi_a500 = lookup_a_share_multiplier(pe_csi_a500, qvix_val, 'csi_a500')

    c300_str  = f'PE {pe_csi300:.1f} × QVIX {qvix_val:.1f} → **{mult_csi300}**'   if pe_csi300  and qvix_val else '⚠️ 数据不完整'
    a500_str  = f'PE {pe_csi_a500:.1f} × QVIX {qvix_val:.1f} → **{mult_csi_a500}**' if pe_csi_a500 and qvix_val else '⚠️ 数据不完整'
    lines.append(f'> CSI300　　{c300_str}')
    lines.append(f'> 中证A500　{a500_str} _(PE代理:中证500)_')

    lines.append('')
    lines.append('> 数据为前一交易日收盘，仅供参考，不构成投资建议。')

    return '\n'.join(lines)


def _get_pe(market_data: dict, key: str) -> float | None:
    """从 market_data 取 PE 值，优先 manual_override。"""
    entry = market_data.get(key)
    if entry is None:
        return None
    return entry.get('manual_override') or entry.get('value')


# ══════════════════════════════════════════════════════════════
# Webhook 发送
# ══════════════════════════════════════════════════════════════

def _send_webhook(text: str, url: str | None = None) -> bool:
    """POST Markdown 消息到企业微信 Webhook，失败时重试最多3次。"""
    webhook_url = url or _WEBHOOK_URL
    if not webhook_url:
        logger.error("WXWORK_WEBHOOK_URL 未设置")
        return False

    payload = {'msgtype': 'markdown', 'markdown': {'content': text}}

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=_TIMEOUT,
            )
            body = resp.json()
            if body.get('errcode') == 0:
                logger.info(f"推送成功 (attempt {attempt})")
                return True
            logger.warning(f"企业微信返回错误 (attempt {attempt}): {body}")
        except Exception as e:
            logger.warning(f"推送失败 (attempt {attempt}): {e}")

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY)

    return False


# ══════════════════════════════════════════════════════════════
# 公开入口
# ══════════════════════════════════════════════════════════════

def send_market_summary(market_data: dict) -> bool:
    """判断交易日，格式化并推送市场温度计摘要。

    非交易日直接返回 True（跳过，不算失败）。
    推送失败时发送一条简短错误通知。
    """
    today = date.today()

    if not _is_trading_day(today):
        logger.info(f"{today} 非交易日，跳过推送")
        print(f"{today} 非交易日，跳过推送")
        return True

    # 检查是否有任何数据
    has_data = any(
        market_data.get(k) is not None
        for k in list(TARGETS.keys()) + ['vix', 'qvix']
    )

    if not has_data:
        error_msg = f'⚠️ **市场温度计** {today.isoformat()}\n\n所有数据源均不可用，请检查 EC2 网络或数据接口。'
        logger.error("所有数据不可用，发送错误通知")
        return _send_webhook(error_msg)

    text = _format_message(market_data)
    print(text)  # 同时输出到日志
    return _send_webhook(text)
