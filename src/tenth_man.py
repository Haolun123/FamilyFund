"""tenth_man.py — 第十人系统：调仓决策前强制反对审查。

三个独立 Agent（价值陷阱/宏观压力/流动性）从不同维度审查决策。
Python 侧预组装所有数据，GLM-5.1 只做推理输出。
"""

import json
import os
from datetime import date


# ── 模型配置（硬编码推理模型，不受 config model 字段影响）──
_TENTH_MAN_MODEL = 'glm-5.1'
_MAX_TOKENS = 5000
_TEMPERATURE = 0.3


# ── Agent System Prompts ──────────────────────────────────────

_PROMPT_A = """你是一个极度悲观的价值投资审问官。你的唯一任务是找出用户投资决策中的价值陷阱。
你必须质疑：Forward PE 的盈利预测是否过于乐观？护城河是否真实存在且可持续？
股息能否维持？增长逻辑是否有循环论证？你绝对不允许说任何正面评价。
用中文输出，严格按以下结构：

## 致命假设
（最容易断裂的逻辑环节，列举2-3条）

## 价值陷阱风险
（具体论据，引用提供的数据）

## 三年后亏损50%的场景
（用第一人称写："现在是三年后，这笔投资亏损了50%。以下是当年我被蒙蔽的原因……"）"""

_PROMPT_B = """你是一个宏观对冲基金的压力测试专员。你的任务是把用户的标的放入极端宏观情景测试。
你必须构建至少两种极端情景（从滞胀/通缩/汇率剧烈波动/利率急升中选最相关的），
测试该资产在这些情景下的抗压能力。评估用户的宏观假设是否站得住脚。
用中文输出，严格按以下结构：

## 最脆弱的宏观假设
（用户假设中最容易被打破的那个）

## 极端情景压测
（至少两种情景，每种说明：触发条件 → 对该标的的影响 → 估计下跌幅度）

## 组合层面的系统性风险
（这笔交易如何放大整体组合在极端情景下的脆弱性）"""

_PROMPT_C = """你是一个只关心资产负债表健康度的风控审计员。你不评价标的好坏，只看数字。
你必须评估：这笔交易后集中度是否过高？现金/流动资产是否充足？
SAP RSU Cliff 期内流动性是否安全？DSCR 是否下降到危险水位？
用中文输出，严格按以下结构：

## 集中度风险
（交易后各类别权重变化，哪些超过警戒线 >20%）

## 流动性压力
（Cash 余额 / 月度刚性支出估算 / 可支撑月数）

## 强制安全条件
（列出必须满足才能放行的条件；不满足则明确建议放弃或缩减规模）"""


# ── 数据预组装 ────────────────────────────────────────────────

def _build_decision_section(decision: dict) -> str:
    direction = decision.get('direction', '买入')
    amount = decision.get('amount_cny', 0)
    name = decision.get('asset_name', '未知标的')
    code = decision.get('yf_symbol') or decision.get('code', '')
    logic = decision.get('core_logic', '（未填写）')
    macro = decision.get('macro_assumption', '（未填写）')

    lines = [
        "## 决策概要",
        f"- 标的：{name}（{code}）",
        f"- 方向：{direction}　金额：¥{amount:,.0f}",
        f"- 核心逻辑：{logic}",
        f"- 宏观假设：{macro}",
    ]
    return '\n'.join(lines)


def _build_portfolio_section(raw_df, fund_nav_df, allocation_df) -> str:
    import pandas as pd
    latest = fund_nav_df.iloc[-1]
    nav = float(latest['NAV'])
    tv = float(latest['Total_Value'])
    ann = latest.get('Annualized_Return(%)')
    mdd = latest.get('Max_Drawdown(%)')

    lines = [
        "\n## 当前组合状态",
        f"- 单位净值：{nav:.4f}　总资产：¥{tv:,.0f}",
    ]
    if ann is not None and not pd.isna(ann):
        lines.append(f"- 年化收益率(TWR)：{float(ann):+.2f}%")
    if mdd is not None and not pd.isna(mdd):
        lines.append(f"- 最大回撤：{float(mdd):.2f}%")

    lines.append("\n资产配置（不含现金）：")
    alloc_sorted = allocation_df[allocation_df['Asset_Class'] != 'Cash'].sort_values(
        'Allocation_Percent', ascending=False
    )
    for _, row in alloc_sorted.iterrows():
        pct = float(row['Allocation_Percent']) * 100
        tv_row = float(row['Total_Value'])
        lines.append(f"- {row['Display_Name']}：{pct:.1f}%  ¥{tv_row:,.0f}")

    # Cash
    cash_row = allocation_df[allocation_df['Asset_Class'] == 'Cash']
    if not cash_row.empty:
        cash_tv = float(cash_row.iloc[0]['Total_Value'])
        cash_pct = float(cash_row.iloc[0]['Allocation_Percent']) * 100
        lines.append(f"- 现金：{cash_pct:.1f}%  ¥{cash_tv:,.0f}")

    return '\n'.join(lines)


def _build_fundamentals_section(yf_symbol: str) -> str:
    """临时拉取目标标的基本面，不写入缓存。"""
    if not yf_symbol:
        return ''
    try:
        from fundamentals import fetch_fundamentals, FIELDS
        data = fetch_fundamentals(yf_symbol)
        if not data:
            return f"\n## 目标标的基本面（{yf_symbol}）\n- 数据不可用（yfinance 拉取失败）"

        label_map = {
            'trailingPE':    'PE (TTM)',
            'forwardPE':     'Forward PE',
            'priceToBook':   'PB',
            'returnOnEquity':'ROE',
            'dividendYield': '股息率',
            'trailingEps':   'EPS (TTM)',
            'forwardEps':    'Forward EPS',
            'revenueGrowth': '营收增长 YoY',
            'earningsGrowth':'盈利增长 YoY',
        }
        lines = [f"\n## 目标标的基本面（{yf_symbol}）"]
        for field, label in label_map.items():
            v = data.get(field)
            if v is None:
                continue
            if field == 'dividendYield':
                lines.append(f"- {label}：{float(v):.2f}%")
            elif field in ('returnOnEquity', 'revenueGrowth', 'earningsGrowth'):
                lines.append(f"- {label}：{float(v)*100:+.1f}%")
            elif field in ('trailingEps', 'forwardEps'):
                lines.append(f"- {label}：{float(v):.2f}")
            else:
                lines.append(f"- {label}：{float(v):.2f}x")
        return '\n'.join(lines)
    except Exception as e:
        return f"\n## 目标标的基本面\n- 拉取失败：{e}"


def _build_market_section(market_data: dict) -> str:
    from market_monitor import (
        lookup_multiplier, lookup_a_share_multiplier, lookup_gold_multiplier,
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

    lines = ["\n## 当前市场信号"]
    if vix:      lines.append(f"- VIX：{vix:.1f}")
    if qvix:     lines.append(f"- QVIX：{qvix:.1f}")
    if treasury: lines.append(f"- 美债10Y：{treasury:.2f}%")

    signals = {
        '标普500':  lookup_multiplier(pe_sp,  vix, 'sp500'),
        '纳指100':  lookup_multiplier(pe_ndx, vix, 'ndx100'),
        '沪深300':  lookup_a_share_multiplier(pe_csi300,   qvix, 'csi300'),
        '中证A500': lookup_a_share_multiplier(pe_csi_a500, qvix, 'csi_a500'),
        '黄金':     lookup_gold_multiplier(gold_bias, vix),
    }
    sig_str = '　'.join(f"{k} {v}" for k, v in signals.items() if v != '—')
    if sig_str:
        lines.append(f"- 定投信号：{sig_str}")
    return '\n'.join(lines)


def _build_post_trade_section(decision: dict, allocation_df, fund_nav_df) -> str:
    """计算交易后的仓位变化，供 Agent C 使用。"""
    direction = decision.get('direction', '买入')
    amount = float(decision.get('amount_cny', 0))
    asset_class = decision.get('asset_class', '')

    total_tv = float(fund_nav_df.iloc[-1]['Total_Value'])
    sign = 1 if direction == '买入' else -1
    new_total = total_tv + sign * amount

    lines = ["\n## 交易后仓位变化（估算）"]
    lines.append(f"- 当前总资产：¥{total_tv:,.0f}　交易后：¥{new_total:,.0f}")

    if asset_class:
        row = allocation_df[allocation_df['Asset_Class'] == asset_class]
        if not row.empty:
            cur_tv = float(row.iloc[0]['Total_Value'])
            new_tv = cur_tv + sign * amount
            cur_pct = cur_tv / total_tv * 100
            new_pct = new_tv / new_total * 100
            from nav_engine import CLASS_DISPLAY_NAMES
            display = CLASS_DISPLAY_NAMES.get(asset_class, asset_class)
            lines.append(f"- {display}：{cur_pct:.1f}% → {new_pct:.1f}%")

    return '\n'.join(lines)


# ── 单次 Agent 调用 ───────────────────────────────────────────

def _run_agent(system_prompt: str, context: str, cfg: dict) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'])
        resp = client.chat.completions.create(
            model=_TENTH_MAN_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': context},
            ],
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
        )
        content = resp.choices[0].message.content
        return content.strip() if content else '[模型未返回内容，请重试]'
    except Exception as e:
        return f'[调用失败：{e}]'


# ── 主入口 ────────────────────────────────────────────────────

def run_tenth_man(
    decision: dict,
    raw_df,
    fund_nav_df,
    allocation_df,
    market_data: dict,
    data_dir: str,
) -> dict:
    """
    运行第十人系统，三次独立 Agent 调用。

    decision 结构：
        asset_name: str
        yf_symbol:  str   # yfinance symbol，直接拉取，不经持仓缓存
        asset_class: str  # 可选，用于计算交易后仓位
        direction:  '买入' | '卖出'
        amount_cny: float
        core_logic: str
        macro_assumption: str

    Returns:
        agent_a, agent_b, agent_c: str (Markdown)
        context: str  # 注入的完整 context
        error: str | None
    """
    # 加载 config
    cfg_path = os.path.join(data_dir, 'tenth_man_config.json')
    if not os.path.exists(cfg_path):
        return {'agent_a': '', 'agent_b': '', 'agent_c': '',
                'context': '', 'error': '未找到 tenth_man_config.json'}
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)

    # 组装 context
    yf_symbol = decision.get('yf_symbol', '')
    sections = [
        _build_decision_section(decision),
        _build_portfolio_section(raw_df, fund_nav_df, allocation_df),
        _build_fundamentals_section(yf_symbol),
        _build_market_section(market_data),
        _build_post_trade_section(decision, allocation_df, fund_nav_df),
    ]
    context = '\n'.join(s for s in sections if s)

    # 三次独立调用
    agent_a = _run_agent(_PROMPT_A, context, cfg)
    agent_b = _run_agent(_PROMPT_B, context, cfg)
    agent_c = _run_agent(_PROMPT_C, context, cfg)

    return {
        'agent_a': agent_a,
        'agent_b': agent_b,
        'agent_c': agent_c,
        'context': context,
        'error': None,
    }
