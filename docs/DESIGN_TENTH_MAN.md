# DESIGN: 第十人系统（Anti-Fragile Allocation Red Teamer）

> **状态**：待实现
> **日期**：2026-04-29（更新 2026-04-30）

---

## 功能定位

调仓决策**前**的强制反对审查，对抗确认偏误。与调仓辅助器完全分离——辅助器用于录入已发生的交易，第十人系统用于决策前的压力测试。

灵感来源：《僵尸世界大战》中的"第十人原则"——当所有人都同意一件事，必须有一个人站出来反对，强制寻找被忽视的风险。

---

## 三个独立 Agent

| Agent | 角色 | 审查维度 |
|-------|------|---------|
| Agent A | 价值陷阱审问官 | Forward PE 可靠性、护城河、盈利可持续性 |
| Agent B | 宏观末日推演机 | 滞胀/通缩/汇率极端情景压测 |
| Agent C | 流动性与集中度审计员 | 集中度、DSCR、RSU Cliff 期流动性 |

---

## 技术架构

### 模型选择

- **模型**：`glm-5.1`（智谱推理模型，硬编码，不受 config 的 model 字段影响）
- **max_tokens**：5000（推理模型需要大 budget：推理过程 ~1200 tokens + 输出 ~600 tokens/Agent）
- **temperature**：0.3
- **注意**：`reasoning_content` 为内部思考过程，`content` 字段才是最终输出

### 数据流

```
用户输入决策
  ↓
Python 侧预组装（不让 LLM 做任何计算）
  ├── 持仓快照（raw_df, fund_nav_df, allocation_df）
  ├── 个股基本面（fundamentals.get_fundamentals()，按需）
  ├── 市场温度计信号（market_monitor.get_market_data()）
  └── 流动性指标（Cash 余额 / DSCR 估算）
  ↓
三次独立 API 调用（各 Agent system prompt 完全隔离）
  ↓
三列 Markdown 展示 + 可选 PDF 导出
```

### API 配置

- API key：`$FAMILYFUND_DATA/tenth_man_config.json`（与 AI 周度评估共用）
- 费用：约 ¥0.10-0.15/次（glm-5.1 推理模型）

---

## Agent System Prompts

### Agent A：价值陷阱审问官

```
你是一个极度悲观的价值投资审问官。你的唯一任务是找出用户投资决策中的价值陷阱。
你必须质疑：Forward PE 的盈利预测是否过于乐观？护城河是否真实存在且可持续？
股息能否维持？增长逻辑是否有循环论证？你绝对不允许说任何正面评价。
用中文输出，严格按以下结构：
## 致命假设
（最容易断裂的逻辑环节，列举2-3条）
## 价值陷阱风险
（具体论据，引用提供的数据）
## 三年后亏损50%的场景
（用第一人称写："现在是三年后，这笔投资亏损了50%。以下是当年我被蒙蔽的原因……"）
```

### Agent B：宏观末日推演机

```
你是一个宏观对冲基金的压力测试专员。你的任务是把用户的标的放入极端宏观情景测试。
你必须构建至少两种极端情景（从滞胀/通缩/汇率剧烈波动/利率急升中选最相关的），
测试该资产在这些情景下的抗压能力。评估用户的宏观假设是否站得住脚。
用中文输出，严格按以下结构：
## 最脆弱的宏观假设
（用户假设中最容易被打破的那个）
## 极端情景压测
（至少两种情景，每种说明：触发条件 → 对该标的的影响 → 估计下跌幅度）
## 组合层面的系统性风险
（这笔交易如何放大整体组合在极端情景下的脆弱性）
```

### Agent C：流动性与集中度审计员

```
你是一个只关心资产负债表健康度的风控审计员。你不评价标的好坏，只看数字。
你必须评估：这笔交易后集中度是否过高？现金/流动资产是否充足？
SAP RSU Cliff 期内流动性是否安全？DSCR 是否下降到危险水位？
用中文输出，严格按以下结构：
## 集中度风险
（交易后各类别权重变化，哪些超过警戒线）
## 流动性压力
（Cash 余额 / 月度刚性支出 / 可支撑月数 / RSU Cliff 期风险）
## 强制安全条件
（列出必须满足才能放行的条件；不满足则明确建议放弃或缩减规模）
```

---

## 输入结构

```python
decision = {
    "asset_name": "成都银行",
    "code": "601838",
    "direction": "买入",         # 买入 / 卖出
    "amount_cny": 20000,
    "core_logic": "Forward PE 仅 5x，股息率 4.75%，银行业不良率下降",
    "macro_assumption": "利率维持低位，成都经济持续增长",
}
```

---

## 输出结构

```python
{
    "agent_a": str,    # Markdown，价值陷阱报告
    "agent_b": str,    # Markdown，宏观压力报告
    "agent_c": str,    # Markdown，流动性报告
    "context": str,    # 注入的 context（供调试/PDF 导出）
    "error": str | None,
}
```

---

## Dashboard UI（第8个 Tab）

```
st.header("第十人系统")
st.caption("调仓决策前的强制反对审查，对抗确认偏误。")

─── 决策输入区 ───
[从持仓选择标的（下拉）] 或手动填写：[标的名称] [Code]
[方向: 买入/卖出] [金额 CNY]
[核心逻辑（text_area）]
[宏观假设（text_area）]
[🔍 启动第十人审查] 按钮  ← 触发三次 GLM 调用

─── 审查报告（三列）───
Agent A               Agent B               Agent C
价值陷阱审问官        宏观末日推演机        流动性审计员
[Markdown 展示]       [Markdown 展示]       [Markdown 展示]

─── 底部 ───
[📄 导出 PDF] ← 保存至 $FAMILYFUND_DATA/tenth_man_reports/
费用提示：本次约 ¥0.10-0.15
```

---

## PDF 导出

路径：`$FAMILYFUND_DATA/tenth_man_reports/YYYY-MM-DD_标的名称.pdf`

4页（A4 横版，复用 `src/pdf_report.py` 的 matplotlib PdfPages 模式）：
- Page 1：决策摘要 + 注入 context
- Page 2：Agent A 报告
- Page 3：Agent B 报告
- Page 4：Agent C 报告

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `src/tenth_man.py` | 数据预组装 + 三个 Agent 调用逻辑 |
| `dashboard/app.py` | 新增「第十人」Tab（第8个） |
| `$FAMILYFUND_DATA/tenth_man_config.json` | API key（已配置，.gitignore 保护） |
| `$FAMILYFUND_DATA/tenth_man_reports/` | PDF 报告存储目录 |

---

## 前置条件

- [x] ZhipuAI API key 已购买并验证（glm-5.1 直连公司网络可用）
- [x] `openai>=1.0.0` 已在 requirements.txt
- [x] `tenth_man_config.json` 已配置
- [ ] 实现 `src/tenth_man.py`
- [ ] Dashboard 新增 Tab
