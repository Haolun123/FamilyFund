# DESIGN: 第十人系统（Anti-Fragile Allocation Red Teamer）

> **状态**：已实现
> **日期**：2026-04-29（更新 2026-04-30）

---

## 功能定位

调仓决策**前**的强制反对审查，对抗确认偏误。与调仓辅助器完全分离——辅助器用于录入已发生的交易，第十人系统用于决策前的压力测试。

灵感来源：《僵尸世界大战》中的"第十人原则"——当所有人都同意一件事，必须有一个人站出来反对，强制寻找被忽视的风险。

**核心设计原则：Agent 永远反对用户的决策方向。**
- 买入 → 三个 Agent 各从不同角度反对买入
- 卖出/减仓 → 三个 Agent 各从不同角度反对卖出

---

## 三个独立 Agent

| Agent | 买入时角色 | 卖出时角色 | 审查维度 |
|-------|-----------|-----------|---------|
| Agent A | 价值陷阱审问官（极度悲观） | 逆向价值辩护律师（极度乐观） | 估值逻辑、护城河、盈利可持续性 |
| Agent B | 宏观压测机（构建不利情景） | 宏观压测机（压测持有不卖的下行风险） | 宏观情景、用户宏观假设的盲点 |
| Agent C | 买入后集中度/流动性审计 | 卖出后配置失衡/机会成本审计 | 集中度、DSCR、RSU Cliff 期流动性 |

---

## 技术架构

### 模型选择

- **模型**：`glm-5.1`（智谱推理模型，硬编码，不受 config 的 model 字段影响）
- **max_tokens**：5000（推理模型需要大 budget：推理过程 ~1200 tokens + 输出 ~600 tokens/Agent）
- **temperature**：0.3
- **注意**：`reasoning_content` 为内部思考过程，`content` 字段才是最终输出

### 数据流

```
用户输入决策（含方向：买入/卖出）
  ↓
Python 侧预组装（不让 LLM 做任何计算）
  ├── 持仓快照（raw_df, fund_nav_df, allocation_df）
  ├── 个股基本面（fundamentals.get_fundamentals()，按需）
  ├── 市场温度计信号（market_monitor.get_market_data()）
  └── 交易后仓位变化估算
  ↓
run_tenth_man() 根据 direction 动态生成三个 Agent 的 system prompt
  ↓
三次独立 API 调用（各 Agent system prompt 完全隔离）
  ↓
三列 Markdown 展示 + 可选 PDF 导出
```

### API 配置

- API key：`$FAMILYFUND_DATA/tenth_man_config.json`（与 AI 周度评估共用）
- 费用：约 ¥0.10-0.15/次（glm-5.1 推理模型）

---

## Agent System Prompts（方向感知，动态生成）

Prompts 通过 `_make_prompt_a/b/c(is_buy: bool)` 函数生成，不使用静态字符串。

### Agent A

**买入方向**（`is_buy=True`）：
```
你是一个极度悲观的价值投资审问官。用户想买入某标的，你的唯一任务是反对这次买入。
论证这是价值陷阱，Forward PE 过乐观，护城河不可持续，增长逻辑循环论证。
输出结构：
## 致命假设 / ## 价值陷阱风险 / ## 三年后亏损50%的场景（第一人称）
```

**卖出方向**（`is_buy=False`）：
```
你是一个极度乐观的逆向投资辩护律师。用户想卖出/减仓，你的唯一任务是反对这次卖出。
论证这是底部恐慌性抛售，资产被严重低估，减仓后很可能踏空反弹。
输出结构：
## 卖出逻辑的致命缺陷 / ## 你正在错过的价值 / ## 三年后后悔卖出的场景（第一人称）
```

### Agent B

**买入方向**（`is_buy=True`）：
```
你是宏观压力测试专员。用构建≥2种极端不利情景，反对现在买入时机。
输出结构：
## 买入时机的宏观致命伤 / ## 极端情景压测（触发条件→影响→跌幅）/ ## 买入后组合的系统性脆弱性
```

**卖出方向**（`is_buy=False`）：
```
你是宏观压力测试专员。压测"如果不卖、继续持有"的下行风险。
不是反对卖出，而是帮用户审查持有的代价，客观评估卖出的合理性。
输出结构：
## 继续持有的宏观致命伤 / ## 持有不卖的极端情景压测（触发条件→影响→跌幅）/ ## 卖出决策的宏观合理性评估
```

### Agent C

**买入方向**（`is_buy=True`）：
```
评估买入后：集中度是否过高、Cash 是否还充足、RSU Cliff 期流动性是否安全。
输出结构：
## 买入后集中度风险 / ## 买入后流动性压力 / ## 强制安全条件
```

**卖出方向**（`is_buy=False`）：
```
评估卖出后：配置是否失衡、税务/手续费成本是否合理、是否有明确资金用途。
输出结构：
## 卖出后配置失衡风险 / ## 卖出的成本与时机 / ## 强制安全条件
```

---

## 输入结构

```python
decision = {
    "asset_name": "成都银行",
    "yf_symbol": "601838.SS",     # yfinance symbol，直接拉取，不经持仓缓存
    "asset_class": "ETF_Stock",   # 可选，用于计算交易后仓位
    "direction": "买入",          # 买入 / 卖出
    "amount_cny": 20000,
    "core_logic": "Forward PE 仅 5x，股息率 4.75%，银行业不良率下降",
    "macro_assumption": "利率维持低位，成都经济持续增长",
}
```

---

## 输出结构

```python
{
    "agent_a": str,    # Markdown，反对买入 or 反对卖出
    "agent_b": str,    # Markdown，宏观不利情景 or 宏观有利情景
    "agent_c": str,    # Markdown，集中度/流动性审计
    "context": str,    # 注入的 context（供调试/PDF 导出）
    "error": str | None,
}
```

---

## Dashboard UI（第8个 Tab：10th Man）

```
st.header("10th Man System")

─── 决策输入区 ───
[从持仓选择标的（下拉）] → 自动填入 Asset Name / YF Symbol / Asset Class
[Asset Name] [YF Symbol] [Direction: Buy/Sell] [Amount CNY]
[核心逻辑（text_area）]
[宏观假设（text_area）]
[🔍 启动第十人审查] 按钮

─── 审查报告（三列）───
买入时：                   卖出时：
Agent A: 价值陷阱审问官    Agent A: 逆向价值辩护律师
Agent B: 宏观压测机        Agent B: 宏观反转研究员
Agent C: 集中度/流动性审计（方向感知标题）

─── 底部 ───
[📄 导出 PDF] ← 保存至 $FAMILYFUND_DATA/tenth_man_reports/
```

---

## PDF 导出

路径：`$FAMILYFUND_DATA/tenth_man_reports/YYYY-MM-DD_标的名称.pdf`

4页（A4，matplotlib PdfPages 模式，CJK 字体，Markdown 符号已剥离）：
- Page 1：决策摘要 + 注入 context
- Page 2：Agent A 报告（标题随方向变化）
- Page 3：Agent B 报告（标题随方向变化）
- Page 4：Agent C 报告

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `src/tenth_man.py` | 数据预组装 + 方向感知 prompt 生成 + 三个 Agent 调用逻辑 |
| `dashboard/app.py` | 第8个 Tab（10th Man），含方向感知列标题和 PDF 导出 |
| `$FAMILYFUND_DATA/tenth_man_config.json` | API key（.gitignore 保护） |
| `$FAMILYFUND_DATA/tenth_man_reports/` | PDF 报告存储目录 |

---

## 实现状态

- [x] ZhipuAI API key 已验证（glm-5.1 直连公司网络可用）
- [x] `src/tenth_man.py` 实现（方向感知 prompt，2026-04-30 修复）
- [x] Dashboard 第8个 Tab 实现
- [x] 持仓下拉快速填入（session_state 驱动）
- [x] PDF 导出（Markdown 剥离，CJK 字体修复）
- [x] 单元测试（`tests/test_new_features.py` TestTenthManContextBuilders）
