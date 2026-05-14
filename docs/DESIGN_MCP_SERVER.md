# DESIGN: FamilyFund MCP Server

> **状态**：待实现（P2）
> **日期**：2026-05-14

---

## 定位

在本地 Mac 上跑一个 MCP Server，让 Claude Code、WorkBuddy 等 Agent 工具直接访问家庭财务数据，实现对话式查阅和分析。

**不需要任何网络暴露**：MCP Server 只监听 localhost，数据直读本地 iCloud 挂载路径。

---

## 架构

```
iCloud Drive（数据源，自动同步）
        ↓                          ↓
Mac A（家）                   Mac B（公司）
  mcp_server.py（本地进程）      mcp_server.py（本地进程）
  读 $FAMILYFUND_DATA             读 $FAMILYFUND_DATA
        ↑                               ↑
  Claude Code                     Claude Code
  WorkBuddy                       WorkBuddy
  企业微信机器人（通过本机转发）
```

两台 Mac 配置完全对称，同一份代码，`launchd` 开机自启。

---

## 技术选型

| 项目 | 选择 | 理由 |
|------|------|------|
| MCP SDK | `mcp[cli]`（官方 Python SDK） | Claude Code 原生支持；`FastMCP` 装饰器极简 |
| 传输协议 | `stdio`（默认）| Claude Code / Claude Desktop 均支持 stdio，无需监听端口 |
| 进程管理 | `launchd` plist | Mac 原生，开机自启，崩溃自动重启 |
| 依赖隔离 | 复用现有 venv | 不新建环境，直接 `pip install mcp[cli]` |

> **注**：stdio 模式下 MCP server 不监听任何端口，Claude Code 通过 subprocess 启动它，更简单也更安全。

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `mcp_server.py` | MCP Server 主文件（项目根目录） |
| `requirements.txt` | 新增 `mcp[cli]>=1.0` |
| `claude_desktop_config_example.json` | Claude Desktop 配置示例（不入 Git，只做参考） |
| `.claude/mcp_config.json` | Claude Code MCP 配置（本地，不入 Git） |

不新增 `src/` 模块，`mcp_server.py` 直接 import 现有模块。

---

## Tool 清单

共 **7 个 tool**，按使用频率排序：

### T1 `get_portfolio_snapshot`
**用途**：查当前净值、各类别权重、盈亏概况

```python
@mcp.tool()
def get_portfolio_snapshot() -> str:
    """返回当前组合快照：总资产、单位净值、各类别市值/权重、XIRR、夏普、最大回撤。"""
```

**实现**：调 `nav_engine.load_portfolio()` + `compute_fund_nav()` + `compute_allocation()` + `compute_xirr()` + `compute_sharpe()`

**返回**：格式化 Markdown 文本（LLM 直接可读）

---

### T2 `get_market_signals`
**用途**：查当前市场温度计信号、DCA 本周建议

```python
@mcp.tool()
def get_market_signals() -> str:
    """返回当前 PE/VIX/QVIX 信号、各标的矩阵倍数、本周 DCA 建议金额。"""
```

**实现**：`market_monitor.get_market_data()` + `dca_manager.compute_all_suggestions()`

---

### T3 `get_ammo_status`
**用途**：查弹药池健康度

```python
@mcp.tool()
def get_ammo_status() -> str:
    """返回弹药池余额、当前信号可支撑周数、全部顶格可支撑周数。"""
```

**实现**：读 `portfolio.csv` 最新 Cash/Fixed_Income + `fi_config.json`，复用弹药池计算逻辑

---

### T4 `get_fi_status`
**用途**：查 FI 进度

```python
@mcp.tool()
def get_fi_status() -> str:
    """返回 FI 目标、当前进度百分比、预计达成年数、当前储蓄率。"""
```

**实现**：`fi_engine.load_fi_config()` + `compute_fi_target()` + `compute_years_to_fi()`

---

### T5 `get_cost_basis`
**用途**：查各持仓成本/盈亏明细

```python
@mcp.tool()
def get_cost_basis() -> str:
    """返回各持仓的成本基准、当前市值、盈亏金额和盈亏率。"""
```

**实现**：`nav_engine.compute_cost_basis()`

---

### T6 `run_backtest`
**用途**：临时回测某标的

```python
@mcp.tool()
def run_backtest(
    target: str,           # 'csi300' | 'csi_a500' | 'sp500' | 'ndx100' | 'gold'
    start_date: str,       # 'YYYY-MM-DD'
    base_amount: float = 1000.0,
    freq: str = 'M',
) -> str:
    """回测指定标的的矩阵策略 vs 固定定投，返回 XIRR 超额、绝对盈亏超额。"""
```

**实现**：`backtest.run_backtest()`，耗时较长（10-30s），正常 MCP 调用即可

---

### T7 `ask_tenth_man`
**用途**：调仓前强制反对审查

```python
@mcp.tool()
def ask_tenth_man(
    asset_name: str,
    direction: str,        # '买入' | '卖出'
    amount_cny: float,
    core_logic: str,
    macro_assumption: str = '',
    code: str = '',
) -> str:
    """对调仓决策进行三角度反对审查：价值陷阱 / 宏观压测 / 流动性审计。"""
```

**实现**：`tenth_man.run_tenth_man()`，需要 `tenth_man_config_*.json` 存在

---

## `mcp_server.py` 骨架

```python
#!/usr/bin/env python3
"""FamilyFund MCP Server — 本地运行，直读 iCloud 数据"""
import os, sys

# 让 MCP server 找到 src/ 模块
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

DATA_DIR = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.join(_ROOT, 'data'),
)
CSV_PATH = os.path.join(DATA_DIR, 'portfolio.csv')

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("FamilyFund")

@mcp.tool()
def get_portfolio_snapshot() -> str:
    """当前组合快照：总资产、净值、各类别权重、XIRR、夏普、最大回撤。"""
    import nav_engine, market_monitor
    raw_df = nav_engine.load_portfolio(CSV_PATH)
    if raw_df is None:
        return "无法加载 portfolio.csv"
    fund_nav_df = nav_engine.compute_fund_nav(raw_df)
    allocation_df = nav_engine.compute_allocation(raw_df)
    xirr   = nav_engine.compute_xirr(raw_df)
    sharpe = nav_engine.compute_sharpe(fund_nav_df)
    calmar = nav_engine.compute_calmar(fund_nav_df)
    # 格式化为 Markdown 返回
    ...

# 其余 tool 类似
if __name__ == '__main__':
    mcp.run()  # stdio 模式，Claude Code 直接 subprocess 调用
```

---

## Claude Code 配置

在 `.claude/mcp_config.json`（不入 Git）中添加：

```json
{
  "mcpServers": {
    "familyfund": {
      "command": "/path/to/python",
      "args": ["/Users/I849833/Side_Project/FamilyFund/mcp_server.py"],
      "env": {
        "FAMILYFUND_DATA": "/Users/I849833/Library/Mobile Documents/com~apple~CloudDocs/Project_shared_files/FamilyFund/data"
      }
    }
  }
}
```

两台 Mac 配置相同，路径相同（iCloud 同步保证）。

---

## launchd 自启（可选）

如果需要后台常驻（给 WorkBuddy 等非 Claude Code 工具用），可以写一个 plist：

```xml
<!-- ~/Library/LaunchAgents/com.familyfund.mcp.plist -->
<key>ProgramArguments</key>
<array>
  <string>/path/to/python</string>
  <string>/Users/I849833/Side_Project/FamilyFund/mcp_server.py</string>
</array>
```

但 Claude Code 用 stdio 模式不需要常驻进程，按需启动即可。

---

## 依赖

`requirements.txt` 新增：
```
mcp[cli]>=1.0
```

其余依赖（pandas、yfinance、akshare、openai）已存在。

---

## 实现顺序

1. `pip install mcp[cli]`，更新 `requirements.txt`
2. 实现 `mcp_server.py`，先做 T1-T4（纯读，不涉及 LLM）
3. 配置 `.claude/mcp_config.json`，在 Claude Code 里验证 `/mcp` 工具可用
4. 验证 WorkBuddy 接入方式（stdio vs SSE，取决于 WorkBuddy 支持的协议）
5. T6 `run_backtest`、T7 `ask_tenth_man`（后者依赖 LLM config）
6. launchd 自启（如需）

---

## 已知限制

- **WorkBuddy 协议待确认**：如果 WorkBuddy 不支持 stdio，需改为 SSE 模式（`mcp.run(transport='sse', port=5174)`），仍然只监听 localhost
- **akshare 在公司网络**：`get_market_signals` 拉 A 股数据可能超时（已知问题），降级返回缓存值
- **第十人需要 LLM config**：两台 Mac 都需要有 `tenth_man_config_*.json`（iCloud 同步自动解决）
