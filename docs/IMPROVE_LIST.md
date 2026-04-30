## 对 FamilyFund 当前功能的评价与改进建议

### 整体评价：⭐⭐⭐⭐ 非常扎实

项目的核心功能已经相当完善，代码质量高，架构清晰，文档详尽。作为一个个人/家庭使用的投资管理工具，已经完全可用。以下是具体评价和建议：

---

### ✅ 做得好的地方

1. **核心算法正确且优雅** — `_run_nav_calculation()` 单一函数复用于基金整体和每个资产类别，份额净值法实现准确
2. **数据安全** — `_atomic_write_csv()` 用文件锁 + 原子替换 + 自动备份（保留最近 30 份），防止写入中断导致数据损坏
3. **Dashboard 完整度高** — 6 个 Tab（总览/周更/历史/SAP/市场温度计/定投回测）覆盖了日常所有操作场景
4. **输入校验严格** — `validate_snapshot()` 对空值、异常波动、NCF 一致性等都有检查
5. **批量导入** — CSV/TSV 粘贴导入很实用，自动补全缺失字段
6. **PDF 报告** — 4 页 A4 横版报告，零额外依赖，可直接下载
7. **54 项测试** — 测试覆盖面广
8. **Docker 化** — 一键部署，健康检查也有
9. **每日企业微信推送** — EC2 cron 自动推送市场温度计信号

---

### ✅ 已完成功能清单（已实现，可直接使用）

| 功能 | 实现位置 | 说明 |
|------|------|------|
| 年化收益率（TWR） | `nav_engine._run_nav_calculation` | Dashboard KPI 展示 |
| 最大回撤（MDD） | `nav_engine._compute_max_drawdown_series` | Dashboard KPI 展示 |
| 数据自动备份 | `nav_engine._atomic_write_csv` | 保存时备份，保留最近 30 份 |
| 基准对比（CSI300/S&P500/CPI/M2） | `src/benchmark.py` | Dashboard NAV 图叠加 |
| XIRR（资金加权收益率） | `nav_engine.compute_xirr` | Dashboard KPI 展示 |
| 夏普比率 | `nav_engine.compute_sharpe` | Dashboard KPI 展示，无风险利率默认 2.5% |
| 卡尔马比率 | `nav_engine.compute_calmar` | Dashboard KPI 展示，年化收益 / 最大回撤 |
| 风险集中度警示 | `dashboard/app.py` Tab1 | 类别>40% / 单持仓>20% 自动警示 + 柱状图 |
| 货币敞口可视化 | `dashboard/app.py` Tab1 | CNY/USD/EUR/HKD 分布 metrics + 圆环图 |
| SAP 盈亏百分比 | `dashboard/app.py` Tab4 | Own/Move/Combined 各自显示盈亏% |
| 标普/纳指分类拆分 | `nav_engine.py` + `portfolio.csv` | `US_Blend_Fund`（标普）+ `US_Growth_Fund`（纳指）独立 NAV 追踪 |
| 市场温度计 + 定投矩阵 | `src/market_monitor.py` | Tab 5，PE×VIX/QVIX 矩阵 |
| 每日企业微信推送 | `src/notifier.py` + `scripts/daily_push.py` | EC2 cron，北京时间 8:30 |
| 累计收益金额 + 简单收益率 | `dashboard/app.py` Tab1 | KPI 显示绝对盈亏金额，delta 显示 (当前总资产-初始投入)/初始投入 |
| Cash 从分类对比/盈亏中排除 | `dashboard/app.py` Tab1 | Cash 仅在基金总览体现，不参与分类 NAV、饼图、盈亏分析 |
| NCF 全资产写入调仓辅助器 | `dashboard/app.py` Tab2 | 买入/卖出自动写对应资产行 NCF，支持新增标的追加行 |
| Docker 代码热重载 | `docker-compose.yml` | src/ 和 dashboard/ 挂载为 volume，代码改动 restart 即生效 |
| Weekly Update 重算市值 | `dashboard/app.py` Tab2 | 「🔄 重算市值」按钮，批量更新 Total_Value = Shares × Price × Rate，Cash 跳过，手动值可覆盖 |
| 黄金定投矩阵 | `src/market_monitor.py` + `dashboard/app.py` Tab5 | MA200乖离率×VIX 矩阵，顶格=5x，对冲仓角色；企业微信推送已包含黄金信号 |
| 完整矩阵表格展示（当前位置高亮）| `dashboard/app.py` Tab5 | 5个标的矩阵全部展示，黄色高亮当前所处格子 |
| 定投策略回测 | `src/backtest.py` + `dashboard/app.py` Tab6 | 固定策略 vs 矩阵策略历史对比，支持CSI300/A500/SP500/NDX100/黄金，月频/周频，XIRR+最大回撤指标 |
| transaction.csv 基础设施 | `dashboard/app.py` Tab2 调仓辅助器 | Apply 时自动写入成交记录；成交价/手续费可选填；历史首笔数据已手动补录（2026-04-17） |

---

### 🔧 待实现功能（按优先级排序）

#### **P2 — 中等复杂度**

1. **调仓决策复盘**（原"资金效率分析"）— 回答"这笔买入/卖出决策好不好"，通过对比成交后该资产的后续表现来评估。

   **`transaction.csv` 基础设施已完成**：
   - 文件已创建（iCloud 同步路径），含 2026-04-17 首次调仓的 11 笔历史记录
   - 调仓辅助器每条买入/卖出条目已新增**成交价**（可选，不填用快照价代替）和**手续费**（可选，默认 0）输入
   - Apply 时自动追加写入 `transaction.csv`，从此每周调仓自动积累数据

   **schema**：`Date, Asset_Class, Platform, Name, Code, Type, Amount_CNY, Price, Fee_CNY`

   **下一步**（P3，等待数据积累）：
   - 积累 3-6 个月数据后，建复盘分析 UI
   - 分析维度：按资产类别/买卖方向，统计各笔交易的后续 1M/3M/6M 收益

2. **FIFO 已实现盈亏**（基于 `transaction.csv`）— 用先进先出批次匹配，计算每次卖出的**已实现盈亏**（区别于目前只有未实现盈亏）。前置条件：transaction.csv 积累 3-6 个月数据。

3. **美债收益率加入市场温度计** ✅ 已实现 — 拉取 ^TNX（yfinance），在温度计 Tab 展示为第5个 KPI 卡片（偏高/中性/偏低，不参与矩阵计算），企业微信推送同步包含。

4. **AI 周度评估**（详见下方专项分析）— 基于持仓结构、温度计信号、NAV 变化，由智谱 GLM 生成中文周报。**架构已确定**：公司设备可直连智谱 GLM（`open.bigmodel.cn`），OpenAI SDK 兼容，`tenth_man_config.json` 已配置，随时可实现。

5. **鲨鱼记账解析 + 季度现金流分析**（详见下方专项分析）— 解析鲨鱼记账导出 CSV，在季度财报 Tab 展示消费结构、自由现金流、DSCR 等指标。前置条件：鲨鱼记账建立「债务还本」分类并从 2026Q2 起持续记录。

6. **第十人系统**（详见下方专项分析）— 调仓决策前的强制反对审查，三个独立 LLM Agent 从价值陷阱/宏观压力/流动性三维度审查。**同样使用 GLM 直连**，无需 EC2 中转。

#### **P3 — 高复杂度 / 需前置工作**

5. **季度财报（Tab 7）** ✅ 已实现 — `src/quarterly_engine.py` + `dashboard/app.py` Tab 7；`balance_sheet.csv`（25Q4+26Q1）和 `cashflow_log.csv` 已初始化。每季末手动更新 CSV，详见 `docs/USER_MANUAL.md` 第 6 节。
6. **收益归因分析** ✅ 已实现，**功能冻结** — Tab1 末尾 Section 5，基于 TWR 口径（类别NAV涨跌幅 × 期初权重），Plotly 瀑布图 + 归因明细表格，支持7个时间范围。

   > **设计决策（2026-04-29）**：归因分析本质是后视镜，对被动指数为主、持有周期长的组合决策价值有限。现有实现作为"回顾性摘要"已足够，不再深化（如 Brinson 配置/选择效应拆分、持仓跟踪误差对比等）。前瞻性决策依赖温度计 + 再平衡工具。
7. **再平衡建议** ✅ 已实现 — Tab1 末尾新增 Section 6，目标配置比例存 `target_allocation.json`（iCloud），UI 内可直接编辑保存。偏差柱状图（超配红/低配绿）+ 明细表格，叠加市场温度计信号。默认比例：US宽基20%/US成长10%/A股15%/ETF股票15%/黄金15%/固收10%/公司股票10%/现金5%。
8. **现金分红处理** — 详见下方专项分析（短期临时方案 A 已有操作规范，长期待实现方案 C）

---

### 🔍 专项分析：再平衡建议

**日期**：2026-04-25  
**状态**：待决策，open points 未解决

#### 功能定位

同花顺可以看个股基本面/技术面，FamilyFund 的独特价值在于：**结合持仓权重和风险集中度**，告诉你"考虑当前仓位，你应该买入/卖出多少"——这是同花顺做不到的。

#### 核心功能（已明确）

- 目标配置比例存储在 `target_allocation.json`（iCloud 同步目录）
- 输入：当前各类别市值 + 目标比例
- 输出：各类别偏差（%）、建议买入/卖出金额（CNY），**仅展示，不自动操作**

#### Open Points（待决策）

**1. 个股信号是否融入再平衡？**

| 方案 | 描述 | 复杂度 |
|------|------|--------|
| 方案 A：纯类别级再平衡 | 只看 8 大类别的配置偏差，不看个股信号 | 低 |
| 方案 B：类别级 + 温度计信号 | 叠加现有 PE×VIX 矩阵信号，标注哪些类别当前信号偏强/偏弱 | 低（信号已有） |
| 方案 C：类别级 + 个股 PE/RSI | 对 ETF_Stock 类里的腾讯、成都银行等额外拉取实时 PE/RSI | 高（新增数据源） |

**当前倾向**：方案 B，温度计信号已有，零新增数据源，且对 US/CN/Gold 的定投决策最有参考价值。

**2. 目标配置比例如何初始化？**

需要用户先定义每个资产类别的目标比例（如 US 合计 30%、CN 20%、Gold 15% 等），才能计算偏差。目标比例存入 `target_allocation.json` 后通过 Dashboard 展示，可手动修改文件调整。

**3. 是否加入一键导入调仓辅助器？**

展示建议后，是否支持点击某类别的建议金额 → 自动填入调仓辅助器的买入/卖出条目？

- 优点：操作闭环
- 缺点：增加实现复杂度；用户仍需选择具体标的（类别内可能有多个标的）

**当前倾向**：暂不做，仅展示建议，用户手动操作调仓辅助器。

---

### 🔍 专项分析：鲨鱼记账解析 + 季度现金流分析

**日期**：2026-04-27
**状态**：待实现，前置条件未满足（需 2026Q2 鲨鱼记账数据）

#### 背景

鲨鱼记账采用双轨制记账（详见 `docs/DESIGN_ACCOUNTING_PRINCIPLES.md`）：
- 所有现金流出都记录，本金还款记「债务还本」一级分类
- FamilyFund 后端解析时剔除「债务还本」还原净资产口径

#### 前置条件

- [ ] 鲨鱼记账 App 新建「债务还本」一级分类
- [ ] 从 2026Q2 起每月记录房贷本金、车贷本金（¥5,000）
- [ ] 每季末导出明细 CSV，存入 `$FAMILYFUND_DATA/<季度>/鲨鱼记账明细.csv`

#### 数据格式

文件编码：UTF-16，制表符分隔。列：`日期、收支类型、类别、金额、备注`

#### 后端实现（`src/cashflow_engine.py`，新文件）

```python
def parse_shark_csv(path) -> pd.DataFrame
    # 读取 UTF-16 CSV，标准化日期格式

def compute_cashflow_summary(df) -> dict
    # 返回：
    # - income_total: 总收入
    # - expense_total: 总支出（含债务还本）
    # - expense_net: 净支出（剔除债务还本）
    # - debt_service: 债务还本合计
    # - free_cashflow: income - expense_total（真实可支配）
    # - net_savings: income - expense_net（净资产视角）
    # - dscr: income / debt_service
    # - by_category: 各类别支出明细
```

#### Dashboard 展示（季度财报 Tab7 新增 Section）

- **KPI 卡片**：季度收入 / 自由现金流 / 净储蓄率 / DSCR
- **支出结构饼图**：各类别占比（剔除「债务还本」，展示真实消费结构）
- **现金流瀑布图**：收入 → 各类支出 → 债务还本 → 净储蓄
- **DSCR 警示**：< 1.5x 时黄色警示，< 1.2x 时红色警示

#### 净资产核对公式（季末对账）

```
期末净资产 ≈ 期初净资产
           + 鲨鱼收入合计
           - 鲨鱼支出合计（剔除「债务还本」）
           ± 资产估值变化（balance_sheet QoQ）
```

---

### 🔍 专项分析：现金分红处理

**日期**：2026-04-25  
**状态**：待决策实现

#### 涉及场景

| 标的 | 分红类型 | 当前处理方式 |
|------|---------|------------|
| 成都银行、腾讯控股 | 现金分红，直接打入证券账户 | ❌ 尚无处理 |
| 红利低波ETF 等 | 现金分红 / 红利复投 | 红利复投无需处理（净值自动体现）；现金分红同上 |
| SAP 股票 | 股票形式再投资 | ✅ 已有专门流程 |
| 场外基金（纳指/标普/A500） | 净值法（分红折算净值） | ✅ 无需处理，净值已包含 |

#### 核心矛盾：NCF 语义冲突

当前 NCF 设计：**买入 = 正数（成本流出），卖出 = 负数（成本回收）**

现金分红如果也记为正数 NCF，会和买入混淆，导致 `Cost_Basis = Σ NCF` 虚高，单资产盈亏失真：

```
成都银行实际情况：
  买入成本 ¥20,880
  持有期间收到分红 ¥1,200（现金到账）
  
错误做法（分红记 NCF = +1,200）：
  Cost_Basis = 20,880 + 1,200 = 22,080  ← 成本虚高
  Profit_Loss = 市值 - 22,080           ← 盈亏失真
  
正确做法：
  分红应减少净成本，Cost_Basis = 买入 - 分红 = 19,680
```

#### 三种方案对比

**方案 A（短期可用）：分红记为 Cash 外部入金**

分红到账时，在 Cash 行记 NCF = +分红金额（备注"XX分红"），不关联来源持仓。

- ✅ 简单，整体 NAV 正确（TWR 会剥离这笔入金）
- ❌ 单资产收益失真：成都银行分红不体现在其盈亏里
- ❌ 无法区分分红收入和工资入金
- **适用于**：分红金额小、不需要精确单资产归因时

**方案 B：分红记为持仓行负 NCF**

在来源持仓（如成都银行）记 NCF = -分红金额（负数 = 收回），Cash 行 Total_Value 增加但 NCF = 0。

- ✅ 单资产盈亏准确：Cost_Basis = 买入 - 分红 = 真实净成本
- ✅ 整体 NAV 不受影响（来源行负 NCF 与 Cash 增加互相抵消）
- ⚠️ 需要扩展调仓辅助器，增加"分红"操作类型
- ⚠️ 需要更新 `compute_cost_basis` 正确处理负 NCF 的语义

**方案 C（最完整）：transaction.csv 新增 Dividend 类型**

在 `transaction.csv` 加一条 `Type = Dividend`，Amount_CNY 为负数（收回）。盈亏分析时从 transaction 里累加分红收入，从成本里扣除。

- ✅ 完整记录分红历史，支持未来分红收益归因
- ✅ 不改变 NCF 语义，向后兼容
- ❌ 实现复杂度最高，盈亏计算需同时查 portfolio.csv 和 transaction.csv

#### 建议

**短期（现在）**：使用方案 A，在 Cash NCF 备注中注明分红来源（如"成都银行现金分红"）。接受单资产盈亏轻微失真，整体 NAV 和 XIRR 计算不受影响。

**长期（分红金额可观后）**：实现方案 C，在 transaction.csv 中用 `Type = Dividend, Amount_CNY = -分红金额` 记录，盈亏分析引擎同步升级。

#### 临时操作规范（方案 A）

每次收到现金分红时：
1. 在当周 Weekly Update 的调仓辅助器中，添加「＋ 外部入金」
2. 金额填分红到账金额
3. 备注填"[来源持仓名] 现金分红"（如"成都银行 现金分红"）
4. 这笔入金会记入 Cash NCF，整体 NAV 正确剥离



### 数据文件分层说明

FamilyFund 的数据层分三个层次，各自服务不同的分析目的：

| 文件 | 频率 | 内容 | 支撑分析 |
|------|------|------|------|
| `portfolio.csv` | 周频快照 | 各持仓当期市值/份额/NCF | NAV 净值走势、P&L、基准对比 |
| `transaction.csv` | 每笔交易 | 单笔成交价/手续费/数量 | 调仓决策复盘（"这笔买卖决策好不好"） |
| `balance_sheet.csv` + `cashflow_log.csv`（待建） | 季频 | 家庭全量资产负债、外部现金流 | 季度家庭财报、净资产 QoQ |

三者互不覆盖，`portfolio.csv` 的投资类汇总会被季度财报引擎自动聚合进 `balance_sheet.csv` 对应行。

---

### 🐛 小问题/代码改进建议

- **`fx_service.py` 没有重试机制** — ~~网络请求应加 retry~~ 评估后不做：调用的是 frankfurter.app 和 yfinance，两者均有隐性 rate limit，激进 retry 反而容易触发限流；失败时 UI 已有 warning，影响可接受。
- **SAP Tab 的默认价格硬编码为 170.0 / 8.0** — ~~缓存为空时应提示用户手动输入~~ 评估后不做：`sap_price_cache.json` 存于 iCloud 同步目录，缓存为空的场景（新机器/手动删除）实际不存在于正常使用路径中。
- **`load_data()` 缓存问题** — `@st.cache_data` 按 `csv_path` 缓存，文件内容变了需手动 `st.cache_data.clear()`（已在各处添加，但散落多处容易遗漏）。非紧急，现有 workaround 够用。
- **~~[待办] Weekly Update：Total_Value 自动计算~~** ✅ 已实现（方案 A）— 在 data_editor 下方新增「🔄 重算市值」按钮，对所有 `Shares > 0 AND Current_Price > 0` 的非 Cash 行批量更新 `Total_Value = Shares × Price × Rate`，用户可在表格中直接覆盖。

---

### 🔍 专项分析：AI 周度评估

**日期**：2026-04-25（更新 2026-04-29）
**状态**：待实现，架构待定（见下方）

#### 功能定位

每次 Weekly Update 完成后，基于 FamilyFund **自有数据**（持仓结构、市场温度计信号、本周 NAV 变化、收益归因）由 DeepSeek 生成 3-5 句中文周报，展示在 Dashboard 并可推送企业微信。

与同花顺等工具的差异：同花顺提供的是市场通用数据；本功能的价值在于结合**你这个家庭基金的具体持仓和权重**给出有操作指向的分析。

#### 架构决策（已确定）

✅ **方案 F：智谱 GLM，公司设备直连**

经测试，公司网络拦截 DeepSeek（SSL 证书错误），但 MiniMax 和智谱 GLM 均可直连。选用智谱 GLM：
- `glm-4-flash` 免费额度充足，OpenAI SDK 兼容
- `base_url`: `https://open.bigmodel.cn/api/paas/v4`
- API key 已存入 `tenth_man_config.json`（iCloud 同步，.gitignore 保护）
- 无需 EC2 中转，家里 Mac Docker 直接调用
- 成本：glm-4-flash 免费；付费模型约 ¥0.001/次

#### 输入 Context

```python
{
  "nav": 1.0421,
  "weekly_return": "+0.82%",
  "annualized_return": "+12.3%",
  "signals": {
    "SP500": "1x", "NDX100": "暂停", "CSI300": "2x",
    "A500": "2x", "Gold": "2x"
  },
  "allocation": {
    "US_Blend_Fund": "18%", "US_Growth_Fund": "12%",
    "CN_Index_Fund": "15%", "Gold": "22%", "Cash": "10%"
  },
  "attribution": [
    {"name": "黄金", "contribution": "+1.01%"},
    {"name": "美股宽基", "contribution": "+0.45%"},
    ...
  ],
  "rebalance_alerts": ["黄金超配 +7.1%", "现金超配 +28.9%"]
}
```

#### System Prompt

```
你是一位家庭基金的投资顾问助理。基于以下数据，用简洁的中文写一段3-5句的周报。
要求：
1. 点评本周表现的主要驱动因素
2. 结合温度计信号，给出下周定投方向建议（哪个类别信号强/弱）
3. 如有明显超配/低配，提醒关注再平衡
4. 语气专业但不晦涩，有具体数字支撑
5. 不要废话，不要空话，直接给结论
```

#### 触发方式

两种触发方式：
- **手动**：Weekly Update 保存快照后，Dashboard 出现「✨ 生成本周 AI 点评」按钮
- **自动**（可选）：`daily_push.py` 每日推送时同步附上 AI 点评段落

#### 输出落地

1. **Dashboard**：Tab1 基金总览 KPI 区域下方，折叠卡片展示，含生成时间戳
2. **企业微信**：在每日/每周推送末尾追加 AI 点评段落

#### 文件清单

| 文件 | 改动 |
|------|------|
| `src/ai_weekly.py` | 新建，context 组装 + DeepSeek API 调用 |
| `dashboard/app.py` | Tab1 新增「生成 AI 点评」按钮和展示区域 |
| `src/notifier.py` | 可选：推送末尾追加 AI 点评 |

#### 前置条件

- [x] DeepSeek API key 已购买
- [x] `tenth_man_config.json` 已有 API key（共用）
- [ ] 实现 `src/ai_weekly.py`

---

### 🔍 专项分析：分类净值/盈亏计算失真问题（已解决）

**日期**：2026-04-18  
**状态**：✅ 已解决

**解决方式**：
1. **重新定义 NCF 语义**：调仓时买入方记正数 NCF、赎回方记负数 NCF；外部入金/出金仍记在 Cash 行。历史数据（04-17 快照）已手动补录并通过资金守恒校验
2. **调仓辅助器升级**：新版辅助器在 Apply 时自动将 NCF 写入各资产行，不再只更新 Cash。支持新增标的自动追加行
3. **Cash 从分类展示中排除**：分类 NAV 图、饼图、业绩表、盈亏分析均不含 Cash；Cash 仅在基金总览中体现
4. **分类 TWR 准确性恢复**：分类 NAV 现在能正确识别调仓资金流入，不再误判为市场涨幅

**Weekly Update 新流程**：
1. 打开 Weekly Update，模板从上周加载（NCF 全清零）
2. 更新未变动持仓的价格/份额/市值
3. 打开调仓辅助器，逐笔录入买卖（下拉选资产）和外部资金
4. 点击「应用」→ Cash TV/NCF + 各资产 NCF 自动写入，新标的自动追加行
5. 补全新标的的份额/价格/市值
6. 填写日期，提交

---

### 🔍 专项分析：第十人系统（Anti-Fragile Allocation Red Teamer）

**日期**：2026-04-29
**状态**：待实现，设计已完成（详见 `.claude/plans/golden-finding-robin.md`）

#### 功能定位

调仓决策**前**的强制反对审查，对抗确认偏误。与调仓辅助器完全分离——辅助器用于录入已发生的交易，第十人系统用于决策前的压力测试。

#### 三个独立 Agent

| Agent | 角色 | 审查维度 |
|-------|------|---------|
| Agent A | 价值陷阱审问官 | Forward PE 可靠性、护城河、盈利可持续性 |
| Agent B | 宏观末日推演机 | 滞胀/通缩/汇率极端情景压测 |
| Agent C | 流动性与集中度审计员 | 集中度、DSCR、RSU Cliff 期流动性 |

#### 技术架构（已确定）

- **模型**：`glm-5.1`（推理模型，硬编码），`max_tokens=3000`，公司设备直连可用
- **数据预组装**（Python 侧）：持仓快照 + yfinance 基本面缓存 + 市场温度计信号 + 流动性指标 → 结构化 context 字符串
- **LLM 推理**（API 侧）：三次独立调用，各角色 system prompt 完全隔离
- **费用**：约 ¥0.10-0.15/次（glm-5.1 推理模型）
- **复用**：`ai_weekly._load_config()`、`fundamentals.get_fundamentals()`、`market_monitor.get_market_data()`
- **注意**：glm-5.1 是推理模型，`reasoning_content` 为内部思考过程，`content` 字段为最终输出，需 `max_tokens≥3000` 才能拿到完整输出

#### Agent System Prompts

**Agent A（价值陷阱审问官）**：
```
你是一个极度悲观的价值投资审问官。你的唯一任务是找出用户投资决策中的价值陷阱。
不允许说任何正面评价。输出结构：
## 致命假设 / ## 价值陷阱风险 / ## 三年后亏损50%的场景（第一人称）
```

**Agent B（宏观末日推演机）**：
```
你是一个宏观对冲基金的压力测试专员。构建至少两种极端宏观情景，测试该资产抗压能力。
输出结构：## 最脆弱的宏观假设 / ## 极端情景压测 / ## 组合层面系统性风险
```

**Agent C（流动性与集中度审计员）**：
```
你是一个只关心资产负债表健康度的风控审计员。不评价标的好坏，只看数字。
输出结构：## 集中度风险 / ## 流动性压力 / ## 强制安全条件
```

#### 输入

```python
{
  "asset_name": "成都银行",
  "code": "601838",
  "direction": "买入",
  "amount_cny": 20000,
  "core_logic": "Forward PE 仅 5x，股息率 4.75%，银行业不良率下降",
  "macro_assumption": "利率维持低位，成都经济持续增长",
}
```

#### UI 设计

- 独立「第十人」Tab（第8个）
- 决策输入区：持仓下拉快速填入 + 手动输入核心逻辑/宏观假设
- 审查报告区：三列并排，各 Agent Markdown 输出
- PDF 导出：保存至 `$FAMILYFUND_DATA/tenth_man_reports/YYYY-MM-DD_标的名称.pdf`

#### 文件清单

| 文件 | 说明 |
|------|------|
| `src/tenth_man.py` | 数据预组装 + 三个 Agent 调用逻辑 |
| `dashboard/app.py` | 新增「第十人」Tab（第8个） |
| `$FAMILYFUND_DATA/tenth_man_config.json` | API key 配置（已配置，不入 Git） |
| `$FAMILYFUND_DATA/tenth_man_reports/` | PDF 报告存储目录 |

#### 前置条件

- [x] ZhipuAI API key 已购买并验证
- [x] `openai>=1.0.0` 已在 requirements.txt
- [x] `tenth_man_config.json` 已配置（glm-4-flash，运行时硬编码切换为 glm-5.1）
- [ ] 实现 `src/tenth_man.py`
- [ ] Dashboard 新增 Tab
