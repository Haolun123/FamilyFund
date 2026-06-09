"""mcp_server.py 回归测试

确保 MCP tool 输出中各字段格式正确,特别是百分比口径
(避免再次出现 "0.692 渲染成 0.7%" 而非 "69.2%" 的 bug)。
"""
import os
import sys
import re
import tempfile
import pytest
import pandas as pd

# 让 mcp_server 与 src 在 import path 上
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'src'))


@pytest.fixture
def mock_portfolio_csv(tmp_path, monkeypatch):
    """创建一份小型 portfolio.csv,主要包含 Fixed_Income 大头 + 几个小头持仓。

    占比预期:
      - Fixed_Income: ~70%(应渲染 70.0% 而非 0.7%)
      - Company_Stock: ~14%(应渲染 14.0% 而非 0.1%)
      - ETF_Stock: ~7%
      - 其他小头
    """
    csv_path = tmp_path / "portfolio.csv"

    # 建仓日 + 当前日,两个日期足以让 nav_engine 计算
    rows = []
    for date in ['2026-04-10', '2026-06-08']:
        rows.extend([
            # Fixed_Income 大头(占 70%)
            {'Date': date, 'Asset_Class': 'Fixed_Income', 'Platform': '招商', 'Name': '月月宝',
             'Code': 'JY001', 'Currency': 'CNY', 'Exchange_Rate': 1.0,
             'Shares': 700000, 'Current_Price': 1.0, 'Total_Value': 700000,
             'Net_Cash_Flow': 700000 if date == '2026-04-10' else 0},
            # Company_Stock(占 14%)
            {'Date': date, 'Asset_Class': 'Company_Stock', 'Platform': 'SAP', 'Name': 'SAP',
             'Code': 'SAP.DE', 'Currency': 'EUR', 'Exchange_Rate': 7.85,
             'Shares': 70, 'Current_Price': 254.55, 'Total_Value': 140000,
             'Net_Cash_Flow': 140000 if date == '2026-04-10' else 0},
            # ETF_Stock(占 7%)
            {'Date': date, 'Asset_Class': 'ETF_Stock', 'Platform': '中信', 'Name': '红利低波',
             'Code': '512890', 'Currency': 'CNY', 'Exchange_Rate': 1.0,
             'Shares': 50000, 'Current_Price': 1.4, 'Total_Value': 70000,
             'Net_Cash_Flow': 70000 if date == '2026-04-10' else 0},
            # Gold(占 5%)
            {'Date': date, 'Asset_Class': 'Gold', 'Platform': '招行', 'Name': '积存金',
             'Code': 'GOLD', 'Currency': 'CNY', 'Exchange_Rate': 1.0,
             'Shares': 100, 'Current_Price': 500, 'Total_Value': 50000,
             'Net_Cash_Flow': 50000 if date == '2026-04-10' else 0},
            # US_Blend_Fund(占 2%)
            {'Date': date, 'Asset_Class': 'US_Blend_Fund', 'Platform': '博时', 'Name': '博时标普500 E类',
             'Code': '018738', 'Currency': 'CNY', 'Exchange_Rate': 1.0,
             'Shares': 10000, 'Current_Price': 2.0, 'Total_Value': 20000,
             'Net_Cash_Flow': 20000 if date == '2026-04-10' else 0},
            # Cash(占 2%,应被排除在 ratio table 外但显示在 Cash 单独行)
            {'Date': date, 'Asset_Class': 'Cash', 'Platform': '招行', 'Name': '现金',
             'Code': 'CASH', 'Currency': 'CNY', 'Exchange_Rate': 1.0,
             'Shares': 20000, 'Current_Price': 1.0, 'Total_Value': 20000,
             'Net_Cash_Flow': 20000 if date == '2026-04-10' else 0},
        ])

    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)

    # 把 CSV_PATH 替换成测试用的
    monkeypatch.setenv('FAMILYFUND_DATA', str(tmp_path))
    return str(csv_path)


def test_portfolio_snapshot_percent_format(mock_portfolio_csv, monkeypatch):
    """回归测试:确保占比字段渲染为 X%(不是 0.X%)。

    之前的 bug:nav_engine.compute_allocation 返回 Allocation_Percent
    为 0-1 区间小数(如 0.7 = 70%),mcp_server 用 `:.1f%` 直接渲染,
    导致 70% 显示成 0.7%。修复后应乘 100。
    """
    # 强制重新加载 mcp_server,让它读到新的 FAMILYFUND_DATA env
    import importlib
    if 'mcp_server' in sys.modules:
        del sys.modules['mcp_server']
    import mcp_server
    importlib.reload(mcp_server)

    # 直接调底层函数(不需要起 MCP HTTP server)
    output = mcp_server.get_portfolio_snapshot()

    # 输出应当是 markdown,不是错误信息
    assert '错误' not in output, f"Tool 调用失败: {output}"
    assert '总资产' in output, "输出缺失总资产字段"

    # 提取所有 X.X% 形式的数字
    percents = [float(m) for m in re.findall(r'\| ([\d.]+)% \|', output)]

    # 至少要有 2 个非零占比(测试数据里 Fixed_Income/Company_Stock 都是大头)
    nonzero = [p for p in percents if p > 0]
    assert len(nonzero) >= 2, f"占比字段应至少有 2 个非零,实际: {percents}"

    # 关键断言:Fixed_Income 占比应该 > 50%(测试数据里是 70%)
    # 如果 bug 在,这个值会显示为 0.7
    assert max(percents) > 50, (
        f"最大占比应该 > 50%(测试数据 Fixed_Income 是 70%),"
        f"如果显示为 < 1 说明百分比口径错(bug 复现)。实际值: {percents}"
    )

    # 占比之和应该约等于 100(允许 1% 误差,因为 Cash 被排除)
    # 5 个非 Cash 类别: 70 + 14 + 7 + 5 + 2 = 98%
    total_pct = sum(percents)
    assert 95 < total_pct < 100, (
        f"非 Cash 类别占比之和应在 95-100% 之间,实际: {total_pct:.1f}%"
    )


def test_portfolio_snapshot_no_decimal_dust(mock_portfolio_csv, monkeypatch):
    """额外断言:测试数据里没有任何 < 1% 的真实仓位,
    所以输出里不应该出现 0.X% 形式的占比(那是 bug 的特征)。"""
    import importlib
    if 'mcp_server' in sys.modules:
        del sys.modules['mcp_server']
    import mcp_server
    importlib.reload(mcp_server)

    output = mcp_server.get_portfolio_snapshot()

    # 找所有 0.X% 模式(0.0% - 0.9%)
    decimal_dust = re.findall(r'\| (0\.\d)% \|', output)
    assert not decimal_dust, (
        f"测试数据中没有 < 1% 的真实仓位,但输出含 {decimal_dust} 这种"
        "0.X% 占比,说明百分比口径错(bug 复现)。完整输出:\n{output}"
    )
