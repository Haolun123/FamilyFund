"""ai_weekly.py — AI 周度评估。

Python 侧预组装所有数据摘要，GLM 只负责生成中文周报文字。
"""

import json
import os
from datetime import date


def _load_config(data_dir: str) -> dict | None:
    path = os.path.join(data_dir, 'tenth_man_config.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def build_weekly_context(
    fund_nav_df,
    raw_df,
    allocation_df,
    class_nav_dict,
    market_data: dict,
    xirr_value,
    sharpe_value,
    data_dir: str = '',
) -> str:
    """组装周报 context，全部由 Python 计算，GLM 只收到文字摘要。"""
    import pandas as pd

    lines = []

    # ── 基金总览 ──
    latest = fund_nav_df.iloc[-1]
    prev   = fund_nav_df.iloc[-2] if len(fund_nav_df) >= 2 else None

    nav_now  = float(latest['NAV'])
    tv_now   = float(latest['Total_Value'])
    date_now = latest['Date']

    weekly_return = None
    if prev is not None:
        nav_prev = float(prev['NAV'])
        weekly_return = (nav_now - nav_prev) / nav_prev * 100

    ann = latest.get('Annualized_Return(%)')
    mdd = latest.get('Max_Drawdown(%)')

    lines.append(f"## 基金总览（{date_now}）")
    lines.append(f"- 单位净值：{nav_now:.4f}")
    lines.append(f"- 总资产：¥{tv_now:,.0f}")
    if weekly_return is not None:
        lines.append(f"- 本周净值变化：{weekly_return:+.2f}%")
    if ann is not None and not pd.isna(ann):
        lines.append(f"- 年化收益率(TWR)：{float(ann):+.2f}%")
    if mdd is not None and not pd.isna(mdd):
        lines.append(f"- 最大回撤：{float(mdd):.2f}%")
    if xirr_value is not None:
        lines.append(f"- XIRR：{float(xirr_value)*100:+.2f}%")
    if sharpe_value is not None:
        lines.append(f"- 夏普比率：{float(sharpe_value):.2f}")

    # ── 资产配置 ──
    lines.append("\n## 当前资产配置")
    alloc_sorted = allocation_df[allocation_df['Asset_Class'] != 'Cash'].sort_values(
        'Allocation_Percent', ascending=False
    )
    for _, row in alloc_sorted.iterrows():
        lines.append(f"- {row['Display_Name']}：{float(row['Allocation_Percent'])*100:.1f}%  ¥{float(row['Total_Value']):,.0f}")

    # ── 本周各类别涨跌 ──
    if prev is not None:
        lines.append("\n## 本周各类别净值表现")
        prev_date = prev['Date']
        for cls, cls_df in class_nav_dict.items():
            if cls == 'Cash':
                continue
            rows_now  = cls_df[cls_df['Date'] == date_now]
            rows_prev = cls_df[cls_df['Date'] == prev_date]
            if rows_now.empty or rows_prev.empty:
                continue
            n_now  = float(rows_now.iloc[-1]['NAV'])
            n_prev = float(rows_prev.iloc[-1]['NAV'])
            chg    = (n_now - n_prev) / n_prev * 100
            from nav_engine import CLASS_DISPLAY_NAMES
            display = CLASS_DISPLAY_NAMES.get(cls, cls)
            lines.append(f"- {display}：{chg:+.2f}%")

    # ── 市场温度计信号 ──
    lines.append("\n## 市场温度计信号")
    from market_monitor import (
        lookup_multiplier, lookup_a_share_multiplier, lookup_gold_multiplier,
        compute_bias,
    )
    vix  = (market_data.get('vix') or {}).get('price')
    qvix = (market_data.get('qvix') or {}).get('price')
    pe_sp  = ((market_data.get('pe_sp500') or {}).get('manual_override')
               or (market_data.get('pe_sp500') or {}).get('value'))
    pe_ndx = ((market_data.get('pe_ndx100') or {}).get('manual_override')
               or (market_data.get('pe_ndx100') or {}).get('value'))
    pe_csi300   = (market_data.get('pe_csi300') or {}).get('value')
    pe_csi_a500 = (market_data.get('pe_csi_a500') or {}).get('value')
    gold_entry  = market_data.get('gold')
    gold_bias   = None
    if gold_entry and gold_entry.get('ma200'):
        gold_bias = (gold_entry['price'] - gold_entry['ma200']) / gold_entry['ma200'] * 100
    treasury = (market_data.get('treasury_10y') or {}).get('price')

    if vix:  lines.append(f"- VIX：{vix:.1f}")
    if qvix: lines.append(f"- QVIX：{qvix:.1f}")
    if treasury: lines.append(f"- 美债10Y收益率：{treasury:.2f}%")

    signals = {
        '标普500':  lookup_multiplier(pe_sp,  vix, 'sp500'),
        '纳指100':  lookup_multiplier(pe_ndx, vix, 'ndx100'),
        '沪深300':  lookup_a_share_multiplier(pe_csi300,   qvix, 'csi300'),
        '中证A500': lookup_a_share_multiplier(pe_csi_a500, qvix, 'csi_a500'),
        '黄金':     lookup_gold_multiplier(gold_bias, vix),
    }
    lines.append("- 定投倍数建议：" + "　".join(f"{k} {v}" for k, v in signals.items() if v != '—'))

    # ── 再平衡提示 ──
    if data_dir:
        try:
            from nav_engine import load_target_allocation, CLASS_DISPLAY_NAMES
            target = load_target_allocation(data_dir)
            fund_tv = float(allocation_df['Total_Value'].sum())
            alerts = []
            for _, row in allocation_df.iterrows():
                cls = row['Asset_Class']
                if cls == 'Cash':
                    continue
                cur = float(row['Allocation_Percent'])
                tgt = target.get(cls, 0.0)
                dev = (cur - tgt) * 100
                if abs(dev) >= 5:
                    direction = '超配' if dev > 0 else '低配'
                    alerts.append(f"{CLASS_DISPLAY_NAMES.get(cls, cls)} {direction} {abs(dev):.1f}%")
            if alerts:
                lines.append("\n## 再平衡提示")
                for a in alerts:
                    lines.append(f"- {a}")
        except Exception:
            pass

    return '\n'.join(lines)


def generate_weekly_summary(context: str, data_dir: str) -> str | None:
    """调用 GLM 生成周报文字。失败返回 None。"""
    cfg = _load_config(data_dir)
    if not cfg:
        return None

    system_prompt = (
        "你是一位家庭基金的投资顾问助理。"
        "基于用户提供的基金数据摘要，用简洁的中文写一段3-5句的周报。\n"
        "要求：\n"
        "1. 点评本周净值表现的主要驱动因素（哪个类别贡献最大）\n"
        "2. 结合温度计信号，给出下周定投方向建议\n"
        "3. 如有明显超配/低配，提醒关注再平衡\n"
        "4. 语气专业简洁，有具体数字支撑，不废话\n"
        "5. 直接输出周报正文，不需要标题"
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'])
        resp = client.chat.completions.create(
            model=cfg['model'],
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': context},
            ],
            max_tokens=400,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"[GLM 调用失败: {e}]"
