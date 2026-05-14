#!/usr/bin/env python3
"""FamilyFund MCP Server

SSE 模式运行在 Docker container 内，通过 http://localhost:5174/sse 对外暴露。
所有 tool 返回 Markdown 格式字符串，供 LLM agent 直接阅读。
"""
import os
import sys
from datetime import date

# 容器内路径；本地开发时自动 fallback 到 src/
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

DATA_DIR = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.join(_ROOT, 'data'),
)
CSV_PATH = os.path.join(DATA_DIR, 'portfolio.csv')

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("FamilyFund", host='0.0.0.0', port=5174)


# ─────────────────────────────────────────────
# T1: 组合快照
# ─────────────────────────────────────────────
@mcp.tool()
def get_portfolio_snapshot() -> str:
    """当前组合快照：总资产、单位净值、各类别权重、XIRR、夏普、最大回撤。"""
    try:
        import nav_engine
        raw_df = nav_engine.load_portfolio(CSV_PATH)
        if raw_df is None or raw_df.empty:
            return "错误：无法加载 portfolio.csv"

        fund_nav_df  = nav_engine.compute_fund_nav(raw_df)
        allocation_df = nav_engine.compute_allocation(raw_df)
        xirr   = nav_engine.compute_xirr(raw_df)
        sharpe = nav_engine.compute_sharpe(fund_nav_df)
        calmar = nav_engine.compute_calmar(fund_nav_df)

        latest = fund_nav_df.iloc[-1]
        total_value  = latest['Total_Value']
        nav_val      = latest['NAV']
        cum_ret      = latest.get('Cumulative_Return(%)', 0) or 0
        ann_ret      = latest.get('Annualized_Return(%)', 0) or 0
        max_dd       = latest.get('Max_Drawdown(%)', 0) or 0
        snap_date    = str(latest['Date'])[:10]

        lines = [
            f"## 组合快照 {snap_date}",
            "",
            f"**总资产**：¥{total_value:,.0f}",
            f"**单位净值**：{nav_val:.4f}（累计 {cum_ret:+.2f}%）",
            f"**年化收益（TWR）**：{ann_ret:.2f}%",
            f"**XIRR**：{xirr*100:.2f}%" if xirr else "**XIRR**：计算中",
            f"**夏普比率**：{sharpe:.2f}" if sharpe else "**夏普比率**：数据不足",
            f"**卡尔马比率**：{calmar:.2f}" if calmar else "**卡尔马比率**：数据不足",
            f"**最大回撤**：{max_dd:.2f}%",
            "",
            "### 各类别配置",
            "| 类别 | 市值（¥） | 占比 |",
            "|------|----------|------|",
        ]
        for _, row in allocation_df.iterrows():
            if row['Asset_Class'] == 'Cash':
                continue
            lines.append(
                f"| {row.get('Display_Name', row['Asset_Class'])} "
                f"| ¥{row['Total_Value']:,.0f} "
                f"| {row['Allocation_Percent']:.1f}% |"
            )
        # Cash 单独列出
        cash_rows = allocation_df[allocation_df['Asset_Class'] == 'Cash']
        if not cash_rows.empty:
            cash_val = cash_rows['Total_Value'].sum()
            lines.append(f"\n**现金（Cash）**：¥{cash_val:,.0f}（不计入配置比例）")

        return "\n".join(lines)
    except Exception as e:
        return f"错误：{e}"


# ─────────────────────────────────────────────
# T2: 市场信号 & DCA 建议
# ─────────────────────────────────────────────
@mcp.tool()
def get_market_signals() -> str:
    """当前 PE/VIX/QVIX 市场信号、各标的矩阵倍数、本周 DCA 建议金额。"""
    try:
        import market_monitor
        import dca_manager
        md = market_monitor.get_market_data(DATA_DIR)
        plans = dca_manager.load_dca_config(DATA_DIR).get('plans', [])
        suggestions = dca_manager.compute_all_suggestions(plans, md)

        def _v(d, key, fmt='.2f'):
            val = (d or {}).get(key)
            return f"{val:{fmt}}" if val is not None else 'N/A'

        lines = [
            f"## 市场信号 {date.today()}",
            "",
            "### 估值与波动率",
            f"- 标普500 PE：{_v(md.get('pe_sp500'), 'value')}",
            f"- 纳指100 PE：{_v(md.get('pe_ndx100'), 'value')}",
            f"- 沪深300 PE：{_v(md.get('pe_csi300'), 'value')}",
            f"- 中证A500 PE：{_v(md.get('pe_csi_a500'), 'value')}",
            f"- VIX：{_v(md.get('vix'), 'price')}",
            f"- VXN：{_v(md.get('vxn'), 'price')}",
            f"- QVIX：{_v(md.get('qvix'), 'price')}",
            f"- 美债10Y：{_v(md.get('treasury_10y'), 'price')}%",
            f"- 中债10Y：{_v(md.get('cn_treasury_10y'), 'price')}%",
            "",
            "### 本周 DCA 建议",
            "| 标的 | 模式 | 信号倍数 | 建议金额 |",
            "|------|------|---------|---------|",
        ]

        total = 0
        for plan, sug in suggestions:
            mode = '固定' if plan.get('mode') == 'fixed' else '矩阵'
            mult = '固定 →' if plan.get('mode') == 'fixed' else f"{sug['multiplier_str']} {sug['arrow']}"
            if sug['unit'] == 'gram':
                amt = f"{sug['suggested_unit']:g}g（≈¥{sug['suggested_cny']:,}）" if sug['suggested_cny'] else f"{sug['suggested_unit']:g}g"
            else:
                amt = f"¥{sug['suggested_cny']:,}"
            lines.append(f"| {plan['name']} | {mode} | {mult} | {amt} |")
            if sug.get('suggested_cny'):
                total += sug['suggested_cny']

        lines.append(f"\n**本周建议总投入：¥{total:,}**")
        return "\n".join(lines)
    except Exception as e:
        return f"错误：{e}"


# ─────────────────────────────────────────────
# T3: 弹药池状态
# ─────────────────────────────────────────────
@mcp.tool()
def get_ammo_status() -> str:
    """弹药池余额、当前信号可支撑周数、全部顶格可支撑周数。"""
    try:
        import nav_engine
        import fi_engine
        import dca_manager
        import market_monitor

        raw_df = nav_engine.load_portfolio(CSV_PATH)
        if raw_df is None or raw_df.empty:
            return "错误：无法加载 portfolio.csv"

        fi_cfg = fi_engine.load_fi_config(DATA_DIR)
        emergency = float(fi_cfg.get('emergency_reserve_cny', 200000))
        top_equity = float(fi_cfg.get('top_multiplier_equity', 10.0))
        top_gold   = float(fi_cfg.get('top_multiplier_gold', 5.0))
        monthly_savings = (float(fi_cfg.get('monthly_income_cny', 0))
                           * float(fi_cfg.get('monthly_savings_target_pct', 0)))

        latest_date = raw_df['Date'].max()
        latest = raw_df[raw_df['Date'] == latest_date]
        cash_val = float(latest[latest['Asset_Class'] == 'Cash']['Total_Value'].sum())
        fi_val   = float(latest[latest['Asset_Class'] == 'Fixed_Income']['Total_Value'].sum())
        ammo_pool = cash_val + fi_val - emergency

        md = market_monitor.get_market_data(DATA_DIR)
        plans = dca_manager.load_dca_config(DATA_DIR).get('plans', [])
        suggestions = dca_manager.compute_all_suggestions(plans, md)

        weekly_cost = sum(s.get('suggested_cny') or 0 for _, s in suggestions)
        monthly_cost = 0.0
        for plan, sug in suggestions:
            cny = sug.get('suggested_cny') or 0
            freq = plan.get('frequency', 'weekly')
            monthly_cost += cny * {'weekly': 4.33, 'biweekly': 2.17, 'monthly': 1.0}.get(freq, 4.33)

        gold_price = (md.get('gold') or {}).get('price') or 0
        max_weekly = 0.0
        for p in plans:
            if not p.get('enabled', True):
                continue
            if p.get('asset_class') == 'Gold':
                from dca_manager import _estimate_gold_price_cny
                gprice = _estimate_gold_price_cny(md) or 0
                max_weekly += float(p.get('base_amount_unit', 0)) * gprice * top_gold
            else:
                max_weekly += float(p.get('base_amount_cny', 0)) * top_equity

        weeks_cur = (ammo_pool / weekly_cost) if weekly_cost > 0 else float('inf')
        weeks_ext = (ammo_pool / max_weekly)  if max_weekly > 0  else float('inf')

        def _status(w):
            if w == float('inf'): return '🟢 无限'
            if w > 8:  return f'🟢 {w:.1f} 周'
            if w >= 4: return f'🟡 {w:.1f} 周（关注）'
            return f'🔴 {w:.1f} 周（警告）'

        lines = [
            f"## 弹药池状态 {date.today()}",
            "",
            f"**弹药池余额**：¥{ammo_pool:,.0f}",
            f"  = Cash ¥{cash_val:,.0f} + 固收 ¥{fi_val:,.0f} - 备用金 ¥{emergency:,.0f}",
            "",
            f"**可支撑（当前信号）**：{_status(weeks_cur)}",
            f"**可支撑（全部顶格）**：{_status(weeks_ext)}",
            "",
            f"**本周消耗**：¥{weekly_cost:,.0f}",
            f"**月消耗速率**：¥{monthly_cost:,.0f}（当前信号持续）",
            f"**月新增储蓄**：¥{monthly_savings:,.0f}",
        ]
        if weeks_ext < 4:
            lines.append("\n⚠️ **弹药不足警告**：建议补充现金或降低基础金额。")
        elif weeks_ext < 8:
            lines.append("\n⚠️ 弹药偏低，建议关注现金储备。")

        return "\n".join(lines)
    except Exception as e:
        return f"错误：{e}"


# ─────────────────────────────────────────────
# T4: FI 进度
# ─────────────────────────────────────────────
@mcp.tool()
def get_fi_status() -> str:
    """财务独立进度：FI 目标、当前进度、预计达成年数、储蓄率。"""
    try:
        import nav_engine
        import fi_engine

        raw_df = nav_engine.load_portfolio(CSV_PATH)
        if raw_df is None or raw_df.empty:
            return "错误：无法加载 portfolio.csv"

        fund_nav_df = nav_engine.compute_fund_nav(raw_df)
        fi_cfg = fi_engine.load_fi_config(DATA_DIR)

        annual_expense = float(fi_cfg.get('annual_expense_target_cny', 0))
        withdrawal_rate = float(fi_cfg.get('withdrawal_rate', 0.04))
        monthly_income  = float(fi_cfg.get('monthly_income_cny', 0))
        savings_pct     = float(fi_cfg.get('monthly_savings_target_pct', 0))
        annual_return   = float(fi_cfg.get('expected_annual_return', 0.04))

        fi_target = fi_engine.compute_fi_target(annual_expense, withdrawal_rate)
        current_assets = float(fund_nav_df.iloc[-1]['Total_Value']) if not fund_nav_df.empty else 0
        monthly_savings = monthly_income * savings_pct
        years = fi_engine.compute_years_to_fi(current_assets, fi_target, monthly_savings, annual_return)
        progress_pct = (current_assets / fi_target * 100) if fi_target > 0 else 0

        monthly_sav_dict = fi_engine.compute_monthly_savings(raw_df)
        recent_months = sorted(monthly_sav_dict.keys())[-3:]
        recent_avg = sum(monthly_sav_dict[m] for m in recent_months) / len(recent_months) if recent_months else 0
        recent_rate = (recent_avg / monthly_income * 100) if monthly_income > 0 else 0

        lines = [
            f"## 财务独立进度 {date.today()}",
            "",
            f"**FI 目标**：¥{fi_target:,.0f}（年支出 ¥{annual_expense:,.0f} ÷ {withdrawal_rate:.0%} 提取率）",
            f"**当前总资产**：¥{current_assets:,.0f}",
            f"**进度**：{progress_pct:.1f}%",
            f"**预计达成**：{years:.1f} 年后" if years is not None else "**预计达成**：超过 100 年（需提高储蓄率）",
            "",
            f"**目标月储蓄**：¥{monthly_savings:,.0f}（月收入 ¥{monthly_income:,.0f} × {savings_pct:.0%}）",
            f"**近3个月实际平均储蓄**：¥{recent_avg:,.0f}（储蓄率 {recent_rate:.1f}%）",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"错误：{e}"


# ─────────────────────────────────────────────
# T5: 持仓成本/盈亏明细
# ─────────────────────────────────────────────
@mcp.tool()
def get_cost_basis() -> str:
    """各持仓成本基准、当前市值、盈亏金额和盈亏率。"""
    try:
        import nav_engine
        raw_df = nav_engine.load_portfolio(CSV_PATH)
        if raw_df is None or raw_df.empty:
            return "错误：无法加载 portfolio.csv"

        cost_df = nav_engine.compute_cost_basis(raw_df)
        if cost_df is None or cost_df.empty:
            return "暂无成本数据"

        lines = [
            f"## 持仓盈亏明细 {date.today()}",
            "",
            "| 标的 | 类别 | 成本（¥） | 市值（¥） | 盈亏（¥） | 盈亏率 |",
            "|------|------|---------|---------|---------|------|",
        ]
        total_cost = total_value = total_pl = 0.0
        for _, row in cost_df.iterrows():
            cost  = row.get('Cost_Basis', 0) or 0
            value = row.get('Market_Value', 0) or 0
            pl    = row.get('Profit_Loss', 0) or 0
            rate  = row.get('Profit_Loss_Rate', 0) or 0
            total_cost  += cost
            total_value += value
            total_pl    += pl
            lines.append(
                f"| {row.get('Name','—')} | {row.get('Asset_Class','—')} "
                f"| ¥{cost:,.0f} | ¥{value:,.0f} "
                f"| ¥{pl:+,.0f} | {rate*100:+.2f}% |"
            )
        total_rate = (total_pl / total_cost * 100) if total_cost > 0 else 0
        lines += [
            "| | | | | | |",
            f"| **合计** | | **¥{total_cost:,.0f}** | **¥{total_value:,.0f}** "
            f"| **¥{total_pl:+,.0f}** | **{total_rate:+.2f}%** |",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"错误：{e}"


# ─────────────────────────────────────────────
# T6: 策略回测
# ─────────────────────────────────────────────
@mcp.tool()
def run_backtest(
    target: str,
    start_date: str,
    base_amount: float = 1000.0,
    freq: str = 'M',
) -> str:
    """回测矩阵策略 vs 固定定投，返回 XIRR 超额、绝对盈亏超额。

    target: csi300 | csi_a500 | sp500 | ndx100 | gold
    start_date: YYYY-MM-DD（如 2015-01-01）
    base_amount: 每期基准金额（默认 1000 元）
    freq: M（月频）| W（周频）
    注意：首次运行需拉取历史数据，耗时约 30 秒。
    """
    try:
        import backtest as bt
        result = bt.run_backtest(
            target=target,
            start_date=start_date,
            base_amount=base_amount,
            freq=freq,
        )
        fixed  = result['fixed']
        matrix = result['matrix']
        actual_start = result.get('actual_start', start_date)
        end_date     = result.get('end_date', str(date.today()))

        xirr_excess = ((matrix['xirr'] or 0) - (fixed['xirr'] or 0)) * 100
        pl_excess   = (matrix['profit_loss'] or 0) - (fixed['profit_loss'] or 0)

        lines = [
            f"## 回测结果：{result.get('label', target)}",
            f"回测区间：{actual_start} ~ {end_date}　频率：{'月频' if freq=='M' else '周频'}　基准金额：¥{base_amount:,.0f}",
            "",
            "| 指标 | 固定定投 | 矩阵策略 | 超额 |",
            "|------|---------|---------|------|",
            f"| 总投入 | ¥{fixed['total_cost']:,.0f} | ¥{matrix['total_cost']:,.0f} | ¥{matrix['total_cost']-fixed['total_cost']:+,.0f} |",
            f"| 最终市值 | ¥{fixed['final_value']:,.0f} | ¥{matrix['final_value']:,.0f} | ¥{matrix['final_value']-fixed['final_value']:+,.0f} |",
            f"| 绝对盈亏 | ¥{fixed['profit_loss']:,.0f} | ¥{matrix['profit_loss']:,.0f} | ¥{pl_excess:+,.0f} |",
            f"| XIRR | {(fixed['xirr'] or 0)*100:.2f}% | {(matrix['xirr'] or 0)*100:.2f}% | {xirr_excess:+.2f}% |",
            f"| 最大回撤 | {fixed['max_drawdown']*100:.2f}% | {matrix['max_drawdown']*100:.2f}% | {(matrix['max_drawdown']-fixed['max_drawdown'])*100:+.2f}% |",
            f"| 定投次数 | {fixed['periods']} | {matrix['periods']} | — |",
            "",
        ]
        if xirr_excess > 0 and pl_excess > 0:
            lines.append("**结论**：第一象限（多投多赚），矩阵策略全面跑赢。")
        elif xirr_excess <= 0 and pl_excess > 0:
            lines.append("**结论**：第二象限（少投高效），XIRR 略低但绝对盈利更多。")
        elif xirr_excess > 0 and pl_excess <= 0:
            lines.append("**结论**：第四象限（多投无超额），矩阵选时未带来绝对收益。")
        else:
            lines.append("**结论**：第三象限（两者均输），矩阵策略在此区间失效。")

        return "\n".join(lines)
    except Exception as e:
        return f"错误：{e}"


# ─────────────────────────────────────────────
# T7: 第十人审查
# ─────────────────────────────────────────────
@mcp.tool()
def ask_tenth_man(
    asset_name: str,
    direction: str,
    amount_cny: float,
    core_logic: str,
    macro_assumption: str = '',
    code: str = '',
) -> str:
    """调仓前三角度强制反对审查：价值陷阱 / 宏观压测 / 流动性审计。

    direction: 买入 | 卖出
    需要 data 目录下存在 tenth_man_config_*.json（GLM/DeepSeek API key）。
    耗时约 30-60 秒。
    """
    try:
        import tenth_man
        import nav_engine
        import market_monitor

        raw_df = nav_engine.load_portfolio(CSV_PATH)
        if raw_df is None or raw_df.empty:
            return "错误：无法加载 portfolio.csv"

        fund_nav_df  = nav_engine.compute_fund_nav(raw_df)
        allocation_df = nav_engine.compute_allocation(raw_df)
        md = market_monitor.get_market_data(DATA_DIR)

        decision = {
            'asset_name':      asset_name,
            'code':            code,
            'direction':       direction,
            'amount_cny':      amount_cny,
            'core_logic':      core_logic,
            'macro_assumption': macro_assumption,
        }

        result = tenth_man.run_tenth_man(
            decision, raw_df, fund_nav_df, allocation_df, md, DATA_DIR
        )

        if result.get('error'):
            return f"第十人审查失败：{result['error']}"

        lines = [
            f"## 第十人审查报告",
            f"**标的**：{asset_name}　**方向**：{direction}　**金额**：¥{amount_cny:,.0f}",
            f"**核心逻辑**：{core_logic}",
            "",
            "---",
            "### Agent A：价值陷阱审问官",
            result.get('agent_a', '无输出'),
            "",
            "---",
            "### Agent B：宏观末日推演机",
            result.get('agent_b', '无输出'),
            "",
            "---",
            "### Agent C：流动性审计员",
            result.get('agent_c', '无输出'),
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"错误：{e}"


# ─────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────
if __name__ == '__main__':
    mcp.run(transport='sse')
