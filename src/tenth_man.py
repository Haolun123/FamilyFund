"""tenth_man.py — 第十人系统：调仓决策前强制反对审查。

三个独立 Agent（价值陷阱/宏观压力/流动性）从不同维度审查决策。
Python 侧预组装所有数据，LLM 只做推理输出。支持 GLM / DeepSeek 切换。
"""

import json
import os
from datetime import date


# ── 默认参数 ──────────────────────────────────────────────────
_MAX_TOKENS = 5000
_TEMPERATURE = 0.3


# ── Agent System Prompts（按方向动态生成）────────────────────

def _make_prompt_a(is_buy: bool) -> str:
    if is_buy:
        return """你是一个极度悲观的价值投资审问官。用户想买入某标的，你的唯一任务是反对这次买入。
你必须论证：这是价值陷阱，Forward PE 的盈利预测过于乐观，护城河不可持续，
股息难以维持，增长逻辑循环论证。你绝对不允许说任何支持买入的话。
用中文输出，严格按以下结构：

## 致命假设
（买入逻辑中最容易断裂的2-3个环节）

## 价值陷阱风险
（具体论据，引用提供的数据，说明为什么现价买入是错的）

## 三年后亏损50%的场景
（用第一人称写："现在是三年后，这笔买入亏损了50%。以下是当年我被蒙蔽的原因……"）"""
    else:
        return """你是一个极度乐观的逆向投资辩护律师。用户想卖出/减仓某标的，你的唯一任务是反对这次卖出。
你必须论证：这是典型的底部恐慌性抛售，该资产被严重低估，卖出时机极差，
持有才是正确选择，减仓后很可能踏空反弹。你绝对不允许说任何支持卖出的话。
用中文输出，严格按以下结构：

## 卖出逻辑的致命缺陷
（减仓/卖出理由中最站不住脚的2-3个环节）

## 你正在错过的价值
（具体论据，引用提供的数据，说明为什么现价卖出是错的）

## 三年后后悔卖出的场景
（用第一人称写："现在是三年后，这笔卖出让我少赚了50%。以下是当年我犯的错误……"）"""


def _make_prompt_b(is_buy: bool) -> str:
    if is_buy:
        return """你是一个宏观对冲基金的压力测试专员。用户想买入某标的，你的任务是用极端宏观情景反对这次买入。
构建至少两种极端不利情景（从滞胀/通缩/汇率剧烈波动/利率急升中选最相关的），
论证为什么现在买入时机极差，用户的宏观假设在这些情景下会崩溃。
用中文输出，严格按以下结构：

## 买入时机的宏观致命伤
（用户宏观假设中最容易被打破的那个，说明为什么现在不是买入时机）

## 极端情景压测
（至少两种不利情景，每种说明：触发条件 → 对该标的的影响 → 估计下跌幅度）

## 买入后组合的系统性脆弱性
（这笔买入如何放大整体组合在极端情景下的损失）"""
    else:
        return """你是一个宏观对冲基金的压力测试专员。用户想卖出/减仓某标的，你的任务是压测"如果不卖、继续持有"的下行风险。
你不是在反对卖出——你是在帮用户审查：如果继续持有，哪些宏观情景会让损失更大？
构建至少两种极端不利情景，论证在这些情景下持有该资产的代价，从而客观评估卖出决策的合理性。
用中文输出，严格按以下结构：

## 继续持有的宏观致命伤
（用户持有该资产隐含的宏观假设，哪个最容易被打破）

## 持有不卖的极端情景压测
（至少两种不利情景，每种说明：触发条件 → 对该标的的影响 → 估计下跌幅度）

## 卖出决策的宏观合理性评估
（综合以上，卖出时机是否合理？如果减仓比例不足，是否需要更大幅度调整？）"""


def _make_prompt_c(is_buy: bool) -> str:
    if is_buy:
        return """你是一个只关心资产负债表健康度的风控审计员。用户想买入某标的，你不评价标的好坏，只看数字。
你必须评估：这笔买入后集中度是否过高？现金/流动资产是否还充足？
SAP RSU Cliff 期内流动性是否安全？这笔资金是否应该留作应急备用？
用中文输出，严格按以下结构：

## 买入后集中度风险
（交易后各类别权重变化，哪些超过警戒线 >20%，是否出现单一标的过度集中）

## 买入后流动性压力
（Cash 余额减少后 / 月度刚性支出估算 / 剩余现金可支撑月数）

## 强制安全条件
（列出必须满足才能放行买入的条件；不满足则明确建议放弃或缩减规模）"""
    else:
        return """你是一个只关心资产负债表健康度的风控审计员。用户想卖出/减仓某标的，你不评价标的好坏，只看数字。
你必须评估：卖出后资产配置是否失衡？回收的现金是否有更紧迫的用途？
减仓后是否造成新的集中度问题（其他类别权重被动升高）？税务成本是否合理？
用中文输出，严格按以下结构：

## 卖出后配置失衡风险
（减仓后各类别权重变化，是否造成其他类别占比过高或某类别过低于目标配置）

## 卖出的成本与时机
（估算税务/手续费成本；如果只是短期回调，卖出再买回的摩擦成本是否值得）

## 强制安全条件
（列出卖出前必须确认的条件；如果没有明确资金用途，建议保持持仓）"""


# ── 数据预组装 ────────────────────────────────────────────────

def _build_decision_section(decision: dict) -> str:
    direction = decision.get('direction', 'Buy')
    direction_cn = '买入' if direction in ('Buy', '买入') else '卖出'
    amount = decision.get('amount_cny', 0)
    name = decision.get('asset_name', '未知标的')
    code = decision.get('yf_symbol') or decision.get('code', '')
    logic = decision.get('core_logic', '（未填写）')
    macro = decision.get('macro_assumption', '（未填写）')

    lines = [
        "## 决策概要",
        f"- 标的：{name}（{code}）",
        f"- 方向：{direction_cn}　金额：¥{amount:,.0f}",
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
    direction = decision.get('direction', 'Buy')
    amount = float(decision.get('amount_cny', 0))
    asset_class = decision.get('asset_class', '')

    total_tv = float(fund_nav_df.iloc[-1]['Total_Value'])
    sign = 1 if direction in ('Buy', '买入') else -1
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

def _strip_think_tags(text: str) -> str:
    """剔除推理模型输出中的 <think>...</think> 内部思考块（MiniMax M2.x 格式）。"""
    import re
    return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()


def _run_agent(system_prompt: str, context: str, cfg: dict) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg['api_key'], base_url=cfg['base_url'])
        resp = client.chat.completions.create(
            model=cfg['model'],
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': context},
            ],
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
        )
        content = resp.choices[0].message.content
        if not content:
            return '[模型未返回内容，请重试]'
        return _strip_think_tags(content)
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
    config_file: str = 'tenth_man_config_GLM.json',
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

    config_file: iCloud data 目录下的配置文件名，默认 GLM。
        可选值：'tenth_man_config_GLM.json' | 'tenth_man_config_deepseek.json'
        config 结构：{"provider": str, "api_key": str, "model": str, "base_url": str}

    Returns:
        agent_a, agent_b, agent_c: str (Markdown)
        context: str  # 注入的完整 context
        error: str | None
    """
    # 加载 config
    cfg_path = os.path.join(data_dir, config_file)
    if not os.path.exists(cfg_path):
        return {'agent_a': '', 'agent_b': '', 'agent_c': '',
                'context': '', 'error': f'未找到 {config_file}'}
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)

    # 方向判断
    direction = decision.get('direction', 'Buy')
    is_buy = direction in ('Buy', '买入')

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

    # 三次独立调用（每个 Agent 根据买入/卖出使用不同 prompt）
    agent_a = _run_agent(_make_prompt_a(is_buy), context, cfg)
    agent_b = _run_agent(_make_prompt_b(is_buy), context, cfg)
    agent_c = _run_agent(_make_prompt_c(is_buy), context, cfg)

    return {
        'agent_a': agent_a,
        'agent_b': agent_b,
        'agent_c': agent_c,
        'context': context,
        'error': None,
    }
