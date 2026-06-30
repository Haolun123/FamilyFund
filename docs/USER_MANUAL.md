# FamilyFund 用户操作手册

> **版本**: v1.4
> **最后更新**: 2026-06-30
> **适用对象**: 家庭 CIO（日常操作参考）

---

## 目录

1. [系统启动](#1-系统启动)
2. [每周更新流程](#2-每周更新流程)
3. [调仓辅助器使用](#3-调仓辅助器使用)
4. [SAP 股票操作](#4-sap-股票操作)
5. [portfolio.csv 字段参考](#5-portfoliocsv-字段参考)
6. [季度财报操作](#6-季度财报操作)

---

## 1. 系统启动

### 本地 Docker 启动（推荐）

```bash
cd /path/to/FamilyFund
docker compose up -d
# 访问 http://localhost:8501
```

### 代码更新后重启（无需重建镜像）

```bash
docker compose restart
```

> `src/` 和 `dashboard/` 已挂载为 volume，代码改动 restart 即生效。

### 需要重建镜像的情况

仅在修改 `requirements.txt` 或 `Dockerfile` 时需要：

```bash
docker compose up -d --build
```

### 数据路径配置

系统通过环境变量 `FAMILYFUND_DATA` 指定 portfolio.csv 所在目录：

```bash
# .env 文件（不入 Git）
FAMILYFUND_DATA=/Users/xxx/Library/Mobile Documents/com~apple~CloudDocs/Project_shared_files/FamilyFund/data
```

---

## 2. 每周更新流程

**频率**：每周五收盘后 / 周末  
**耗时**：约 5-10 分钟（定投标的自动搞定后）

### 步骤

**① 打开 Weekly Update Tab**

Dashboard → Weekly Update Tab，系统自动加载上周快照（NCF 清零）。

**② 导入定投确认短信（步骤一区域）**

将本周所有定投确认短信粘贴到"📱 步骤一"文本框，点「🔍 解析短信」：

- 系统自动识别：基金名称、扣款金额、确认份额、净值
- 确认解析结果无误（如有未匹配，手动从下拉指定持仓）
- 点「✅ 应用解析结果」→ **三件事自动完成**：
  1. 持仓表对应行 `Shares` += 本次确认份额
  2. 持仓表对应行 `Current_Price` = 确认净值
  3. 调仓辅助器自动登记买入记录（NCF + Cash 扣款）

> 支持格式：博时标普确认短信、南方纳指扣款短信、招商银行积存金买入短信。

**③ 确认持仓数据（步骤二区域）**

短信解析「应用解析结果」后系统会**自动刷新所有标的净值**（天天基金 / yfinance），无需手动操作。

只需处理以下例外情况：
- 固定收益（银行理财）：净值拉取标记为"需手动确认"，手动填入最新估值
- 拉取失败的标的：查看刷新详情，手动填入 `Current_Price`

**④ 重算市值**

点「🔄 重算市值」，系统自动计算 `Total_Value = Shares × Current_Price × Exchange_Rate`（Cash 行跳过）。

> 如某持仓需手动指定市值（如理财产品），重算后直接在表格覆盖 `Total_Value` 即可。

**⑤ 其他调仓操作（步骤三，如有）**

展开「⚖️ 步骤三：调仓辅助器」，处理短信解析以外的操作：
- 手动买入（短信解析不支持的标的）
- 卖出/赎回
- 外部入金（工资存入）、外部取出（大额支出）

详见 [第3节](#3-调仓辅助器使用)。

**⑥ 登记 SAP 归属**（如有）

详见 [第4节](#4-sap-股票操作)。

**⑦ 填写日期并提交**

选择本周日期，点「保存快照」。系统自动校验并追加到 portfolio.csv。

**⑧ 查看 Dashboard**

切换到 Dashboard Tab，刷新页面，所有指标自动重算。

### 常见报错处理

| 错误 | 原因 | 处理 |
|------|------|------|
| 新日期必须晚于上次快照 | 日期填错 | 修正日期 |
| Asset_Class 无效 | 类别拼写错误 | 从下拉列表选择 |
| Total_Value 不能为负 | 数字填错 | 检查对应行 |
| NCF 远超市值 | 可能录入金额有误 | 核对调仓金额 |

### 特殊情况：现金分红到账

当成都银行、腾讯控股、红利ETF 等持仓发生**现金分红**（现金打入证券账户，非复投）时：

在调仓辅助器（步骤三）中点「＋ 外部入金」，填税后到账金额，备注来源（如"成都银行 现金分红"）。

> 红利**复投型**基金无需操作，净值已自动包含分红收益。SAP 分红复投见第4.3节。

---

## 3. 调仓辅助器使用

调仓辅助器负责**自动将买卖金额写入各资产行的 NCF**，并同步更新 Cash 余额。

> 必须在编辑完持仓表后打开，确保资产下拉列表是最新的。

### 操作类型说明

| 按钮 | 适用场景 | Cash 变化 | 资产行 NCF |
|------|---------|-----------|------------|
| ＋ 买入 | 从 Cash 买入基金/ETF/黄金 | -买入金额 | +买入金额 |
| ＋ 卖出 | 赎回基金/ETF，Cash 回款 | +到账金额 | -到账金额 |
| ＋ 外部入金 | 工资等外部资金存入 | +入金金额 | 仅 Cash NCF += |
| ＋ 外部取出 | 大额支出从 Cash 取出 | -取出金额 | 仅 Cash NCF -= |

### 操作步骤

1. 点击对应按钮添加条目
2. **买入/卖出**：从下拉菜单选择关联资产，填写金额
3. **成交价**（可选）：填写实际申购/赎回确认净值（从基金平台交易记录查）；不填则用快照 Current_Price 代替，用于 transaction.csv 记录
4. **手续费**（可选）：填写已被扣除的手续费（CNY），默认 0
5. **外部入金/取出**：直接填写金额（无需选资产，无成交价/手续费字段）
6. 下方预览区确认 Cash 变化符合预期
7. 点击「✅ 应用到持仓表」→ NCF 写入各资产行，买卖记录自动追加到 `transaction.csv`

> **成交价说明**：你的实际申购发生在周二/周四，而快照日期是周五/周末。成交价应填写申购确认日的基金净值（T+1 日确认），可从基金平台"交易记录"查询。不填时系统用快照净值代替，误差通常在 0.5% 以内。

### 新增标的

买入一只之前没有的基金/股票时：
1. 点「＋ 买入」
2. 资产下拉选择「新增标的」
3. 填写资产类别、平台、名称、代码、货币
4. 点击应用 → 持仓表末尾自动追加一行，NCF 已填
5. **手动补全**：份额、当前价格、总市值

### 注意事项

- 同名资产有多行（如同基金不同平台）时，NCF 写入第一匹配行，其余行需手动填写
- 如无调仓，跳过此步骤即可，所有资产 NCF 保持 0

### 清仓处理规则

完全清仓某资产后，**不要删除该行**，而是保留并置零：

| 字段 | 值 |
|------|---|
| `Shares` | 0 |
| `Current_Price` | 最后成交价（或上期价格） |
| `Total_Value` | 0 |
| `Net_Cash_Flow` | -(到手金额 - 手续费)（由调仓辅助器自动写入） |

**原因**：NCF 的历史买入记录需要保留，`compute_cost_basis` 和 XIRR 计算依赖这笔卖出现金流。删行会导致成本基准断裂、盈亏率失真。

**下周快照**：Weekly Update 会将该行（`Shares=0, Total_Value=0`）带入下周编辑区模板。在步骤二的持仓表中**手动删除该行**，之后的快照将不再包含它。

> 如不删除也无害（Total_Value=0 不影响任何计算），但会在后续快照中留噪音。

---

## 4. SAP 股票操作

### 4.1 Own SAP (ESPP) — 每月约 5 号

每月 ESPP 归属后：

1. Dashboard → SAP Stock tab → Add Own SAP Transaction
2. 填写：Date、Stock Price (EUR)、Tax Rate (%)
3. 逐行录入：Type (Match/Purchase)、CNY、Qty
4. Save → 追加到 own_sap.csv

下次周末更新快照时：
- 更新 Company_Stock "Own SAP" 行
  - `Shares` = SAP 当前总股数
  - `Total_Value` = Shares × 当前价格 × EUR/CNY
  - `Net_Cash_Flow` = 本次归属 Cost_CNY 之和（**实际支付成本，非归属市值**）

> **为什么用 Cost_CNY 而不是归属市值？**
> ESPP 是折扣价主动购买，属于投资决策。选择 opt-in 而非拿现金，折扣差价（归属市值 - Cost_CNY）
> 是投资回报，体现为持仓浮盈，不应算作外部入金。RSU 是无偿归属（不存在"不参与"的选项），
> 口径不同，记归属市值为成本基准。

### 4.2 Move SAP (RSU) — 每季约 11 号（3/6/9/12 月）

每季 RSU 归属后：

1. Dashboard → SAP Stock tab → Add Move SAP Transaction
2. 填写：Date、Stock Price (EUR)、FX Rate (EUR/CNY)
3. 逐行录入：Qty per tranche
4. Save → 追加到 move_sap.csv

下次周末更新快照时：
- 更新 Company_Stock "Move SAP" 行
  - `Shares` = SAP 当前总股数
  - `Total_Value` = Shares × 当前价格 × EUR/CNY
  - `Net_Cash_Flow` = 归属市值 CNY（免费获得，FMV 作为成本基准）

### 4.3 Dividends — 每年约 5 月

SAP 分红以股票形式再投资（Dividend Reinvestment）。按业界全收益标准，分红为持股孵化的内部回报，**NCF = 0**。

**Own SAP 分红：**
1. SAP Stock tab → Add Own SAP Transaction → Type 选 **Dividend**
2. 填写：Date、Price (EUR)、Qty（分红买入份额）
3. Save → 追加到 own_sap.csv（`Cost_CNY` 自动计算，但不计入成本基准）

**Move SAP 分红：**
1. SAP Stock tab → Add Move SAP Transaction → Activity 选 **Dividend**
2. 填写：Date、Price (EUR)、FX Rate、Qty（分红买入份额）
3. Save → 追加到 move_sap.csv

**下次周末更新快照时：**
- 更新对应 Company_Stock 行：
  - `Shares` = 含分红后的最新总股数
  - `Total_Value` = Shares × 当前价格 × EUR/CNY
  - `Net_Cash_Flow` = **0**（分红复投，内部流转）

> 分红金额不计入成本基准，分红收益体现为持仓市值上升，NAV 自然上升，与红利低波 ETF 处理方式一致。

---

## 5. portfolio.csv 字段参考

每行 = 一个持仓在某日期的快照。每周新增一批行（同一日期）。

### 字段定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `Date` | YYYY-MM-DD | ✅ | 对账日期，同一周所有行日期相同，且必须严格大于上次日期 |
| `Asset_Class` | string | ✅ | 资产类别，见下表 8 选 1 |
| `Platform` | string | ✅ | 交易平台/账户名（如"招商银行"、"中信证券"） |
| `Name` | string | ✅ | 持仓名称（如"南方中证A500"） |
| `Code` | string | | 证券/基金代码（可选，仅作标识） |
| `Currency` | string | ✅ | CNY / HKD / USD / EUR |
| `Exchange_Rate` | float | ✅ | 该币种→CNY 汇率（CNY=1.0，USD≈7.25，EUR≈7.9） |
| `Shares` | float | ✅ | 持有份额/股数 |
| `Current_Price` | float | ✅ | 当前单价（原始币种） |
| `Total_Value` | float | ✅ | 当前总市值（CNY），= Shares × Price × Exchange_Rate |
| `Net_Cash_Flow` | float | ✅ | 本期资金变动，见下方 NCF 规则 |

### NCF 填写规则

| 情形 | NCF 填写 |
|------|---------|
| **建仓日**（第一次录入） | = Total_Value（全部市值作为初始成本） |
| **买入资产**（本周新买入） | = +买入金额（正数） |
| **卖出/赎回资产** | = -到账金额（负数）；清仓则 Total_Value=0 |
| **外部入金/取出** | 记在 Cash 行，= +入金金额 或 -取出金额 |
| **SAP Own SAP 归属（ESPP）** | Company_Stock 行 = +Cost_CNY（实际支付成本，**非**归属市值） |
| **SAP Move SAP 归属（RSU）** | Company_Stock 行 = +归属市值 CNY（FMV，无偿归属以市值为成本基准） |
| **SAP 分红复投（Dividend）** | = **0**（持股内部回报，不计入外部资本，与红利复投基金一致） |
| **无操作持仓** | = 0 |

> 推荐通过调仓辅助器填写，系统自动写入对应资产行。

### 资产类别

| 代码 | 中文名 | 典型持仓 |
|------|--------|---------|
| `US_Blend_Fund` | 美股宽基基金 | 标普500 QDII |
| `US_Growth_Fund` | 美股成长基金 | 纳指100 QDII |
| `CN_Index_Fund` | A股指数基金 | 沪深300、中证A500 |
| `ETF_Stock` | ETF与股票 | 红利ETF、A股个股、港股 |
| `Fixed_Income` | 固定收益 | 银行理财、短债基金 |
| `Gold` | 黄金 | 实物黄金、纸黄金 |
| `Company_Stock` | 公司股票 | SAP（ESPP + RSU） |
| `Cash` | 现金 | 固定现金储备（约10万），不追踪日常消费账户 |

### 示例数据

```csv
Date,Asset_Class,Platform,Name,Code,Currency,Exchange_Rate,Shares,Current_Price,Total_Value,Net_Cash_Flow
2026-04-10,US_Blend_Fund,标普场外,博时标普500 E类,018738,CNY,1.0,5320.46,5.0237,26728.39,26728.39
2026-04-10,Gold,招商银行,黄金,GOLD,CNY,1.0,55.58,1045.23,58096.39,58096.39
2026-04-10,Cash,现金,现金,CASH,CNY,1.0,99009.49,1.0,99009.49,99009.49
2026-04-17,US_Blend_Fund,标普场外,博时标普500 E类,018738,CNY,1.0,5711.09,5.2033,29716.51,800.0
2026-04-17,Gold,招商银行,黄金,GOLD,CNY,1.0,57.06,1064.7,60754.34,1572.75
2026-04-17,Cash,现金,现金,CASH,CNY,1.0,95986.83,1.0,95986.83,0.0
```

> 第二周博时标普500 E类买入了 ¥800，黄金买入了 ¥1572.75，Cash 减少对应金额，无外部入金所以 Cash NCF = 0。

---

## 6. 季度财报操作

**频率**：每季末（3月/6月/9月/12月底）  
**耗时**：约 15-20 分钟  
**入口**：Dashboard → Quarterly Report Tab

---

### 6.1 新增季度数据（手动编辑 CSV）

**Step 1 — 更新 `balance_sheet.csv`**

文件路径：`$FAMILYFUND_DATA/balance_sheet.csv`（iCloud 同步目录）

1. 用文本编辑器或 Excel 打开文件
2. 复制上一季度的所有行（例如复制所有 `2026Q1` 行）
3. 粘贴到文件末尾，将 `Quarter` 列全部改为新季度（如 `2026Q2`）
4. 逐行更新各账户余额：

| 类别 | 更新方式 |
|------|---------|
| `Asset_Current` Cash 各账户 | 查各银行/支付宝 APP 查季末余额 |
| `Asset_Current` ProvidentFund | 查公积金 APP 账户余额 |
| `Asset_Investment` | **2026Q2 起填 0**，引擎自动从 portfolio.csv 聚合季末市值 |
| `Asset_RealEstate` | 房产估值按需更新（每年 1-2 次即可），车辆按市场行情估算 |
| `Asset_PrivateEquity` | 按实际变化更新 |
| `Asset_BadDebt` Provision | 如坏账比例有变化，更新计提金额 |
| `Liability_Current` CreditCard | 查各信用卡当期账单余额 |
| `Liability_LongTerm` 贷款 | 查银行 APP 贷款剩余本金 |
| `Liability_Family` | 按实际变化更新 |

> **Asset_Investment 特别说明**：`2026Q2` 起，`Sub_Category` 为 `auto` 的行引擎会自动从 `portfolio.csv` 中取季末（最近快照）的 `Total_Value` 合计填入，无需手动填数字，保持 `Amount=0, CNY_Amount=0` 即可。

**Step 2 — 更新 `cashflow_log.csv`**（通常可跳过）

文件路径：`$FAMILYFUND_DATA/cashflow_log.csv`

**重要**：`cashflow_log.csv` 只记录**家庭基金外**的特殊现金流。

- ❌ **不需要填**：打入家庭基金的工资/注资 → 已在 `portfolio.csv` Cash NCF 里有记录
- ✅ **需要填**：鲨鱼记账捕捉不到的基金外特殊项

实际每季度通常 0-2 条，大多数季度可直接跳过此步骤：

```csv
2026Q2,2026-05-15,-15000,Outflow_Major,保险年费（基金外支出）
2026Q2,2026-06-01,23000,Inflow_Other,旧车置换补贴已收款（未打入基金）
```

Type 枚举：
- `Inflow_Salary`：工资净储蓄（未来由鲨鱼记账脚本自动生成）
- `Inflow_Other`：经营性特殊收入（政府补贴、保险理赔等）
- `Capital_Inflow`：资本性流入（资产变现、大额补偿等，不属于日常经营）
- `Capital_Outflow`：资本性流出（大额资产购置支出等）
- `Outflow_Major`：基金外大额经营性支出

**Step 3 — 刷新 Dashboard**

保存 CSV 后，刷新浏览器，Quarterly Report Tab 自动加载新季度数据。选择 `2026Q2` 作为当前季度，`2026Q1` 作为对比季度，即可查看 QoQ 对比和瀑布图。

---

### 6.2 查看与导出

1. 打开 Dashboard → **Quarterly Report** Tab
2. 顶部下拉选择**当前季度**和**对比季度**
3. 查看 KPI 卡片、资产负债表、瀑布图、资产结构对比图
4. 点击「📄 下载季度 PDF 报告」导出 2 页 A4 横版报告

---

### 6.3 季度复盘对话 Checklist

**目的**：季度财报的数据是事实层（KPI / 资产负债表 QoQ / 桑基图 / 净资产核对），但**真正的优化机会藏在数据背后的"功能性属性"和"主观决策上下文"里**——这些 AI 单方面看 CSV 看不出来。

季度复盘**不应该是 AI 单边输出建议清单**，而应该是**用户主导、AI 协助提问**的对话流程。AI 看到这份 Checklist 就知道该问什么问题，而不是按"一般家庭"模板套结论。

**为什么不把分析做成 UI 模块**：
- AI 不知道你的固收是 T+0/T+1 可赎（看到 Cash 少就误判流动性不足）
- AI 不知道你的房贷利率 2.7-3.05%（看到 ¥128 万负债就提"是否还贷"）
- AI 不知道你的投资性房产租金回报 4.78%（看到 ¥90 万就提"是否处置"）
- AI 不知道你的奖金延迟消费习惯（看到 Q2 可选 50% 就误判"消费纪律松动"）
- 每次都需要你反驳 → 心智压力增加而非减少 → 违反 M1 原则

**复盘流程（与 AI 对话时按序问）**：

```
═══════════════════════════════════════════════════════════
📋 季度复盘 Checklist  (用户主导, AI 协助)
═══════════════════════════════════════════════════════════

【一、核心指标的"意外项"】
□ Q 季储蓄率/必需占比是否符合本季工作节奏的预期?
  (年终奖季 vs 裸工资季 baseline 不同)
□ 净储蓄绝对额 vs 上季,差异是收入端还是支出端驱动?
□ 必需 vs 可选支出占比的变化,是结构性还是一次性?

【二、净资产核对残差解读】
□ 残差(实际变化 - 预测变化)在 ±2% 内为绿区,>5% 必须查证
□ 残差归因: 哪类资产的估值变化为主?(股票/黄金/房产)
□ 资本性现金流(卖车/买车等)是否已正确归类为 Capital_Inflow/Outflow?

【三、资产负债表的"新增/消失项"】
□ 新增账户/项目: 来自哪个主观决策?(开户/借款/继承)
□ 消失账户/项目: 处置原因?(卖车/还清贷款/账户合并)
□ 占比突变(±5% 以上)的大类是否在 P1-P12 仓位框架内?

【四、大额可选支出的心智账户归属】
□ Q 季单笔 >¥10k 的可选支出(旅行/购物等),资金来源是?
  ├ 工资季储蓄 → 算入"日常可选预算"
  ├ 奖金延迟消费 → 应归"奖金池"心智账户(不算入纪律考核)
  └ 资产变现(卖车/卖股) → 应归"资本性支出"
□ 鲨鱼记账中是否给奖金池支出加 [奖金池] 备注以便未来分离统计?

【五、流动性结构】
□ 用户的"广义流动性"(Cash + T+0/T+1 可赎固收)总额?
  (注: 用户的 Fixed_Income 全部可赎,所以等价于现金缓冲层)
□ 是否能覆盖 ≥6 个月家庭支出?
□ 是否有真正"锁定"的资产被误算入流动性?(定期/封闭基金)

【六、负债的实际成本】
□ 各笔房贷/车贷的实际利率是多少?
  (用户当前: 房贷 2.7-3.05%, 在 M2 周期下属负实际利率优质负债)
□ 是否有循环信用卡欠款?(用户全额还款 → 无利息成本)
□ 家庭内部负债(丈母娘注资等)是否需要主动安排归还?

【七、投资性资产的现金回报】
□ 投资性房产的毛租售比 = 年租金 / 估值?
  (用户保利天悦: ¥4.3 万 / ¥90 万 = 4.78% 毛租售比 → 优质)
□ 私募/基金等是否产生分红现金流?
□ SAP 股票被动归属(ESPP+RSU)是否在预期节奏内?

【八、战略持仓的逻辑校验】
□ 黄金/中海油/腾讯等战略仓位的入场逻辑是否依然成立?
  (检查: 浮亏 ≠ 逻辑破; 只在逻辑破时才退场)
□ 是否有偏离 P1-P12 仓位原则的事项发生?
□ ESPP/RSU 占比是否已超过预设上限?(集中风险审视)

【九、下季度前瞻】
□ 已知的现金流大事件: 学费/保险年费/旅行/家电更换等
□ 已知的收入变化: 加薪/奖金归属时点/RSU vesting 节奏
□ 是否有计划中的资产处置或新增?
□ 是否有外部宏观风险需要纳入考量?(利率/政策/汇率)

═══════════════════════════════════════════════════════════
```

**用法**：

1. 季末做完 Weekly Update 后，告诉 AI："咱们做 Q2 复盘"
2. AI 会按上述 Checklist 顺序提问，**先获取你的功能性上下文，再做诊断**
3. 你的反驳 = 校准 AI 的认知（重要数据源），不是 AI 出错
4. 复盘结束后，AI 可能会更新一两条 memory（消费习惯、负债利率等"准静态"事实）

**核心原则**：复盘的价值密度来自**对话与碰撞**，不来自"AI 给的结论本身"。把 Checklist 沉淀下来，是为了让每次复盘对话都从"功能确认"开始，而不是从"形式分析"开始。

> 详见 memory: `feedback_asset_function_before_form.md`(2026-06-30 起)

---

### 6.4 balance_sheet.csv 字段说明

| 字段 | 说明 | 示例 |
|------|------|------|
| `Quarter` | 季度标识 | `2026Q2` |
| `Category` | 大类（见枚举） | `Asset_Current` |
| `Sub_Category` | 子类 | `Cash`, `Mortgage`, `auto` |
| `Account` | 账户/项目名称 | `招商银行` |
| `Amount` | 原币金额 | `102011` |
| `Currency` | 货币 | `CNY` |
| `FX_Rate` | 原币→CNY 汇率 | `1.0` |
| `CNY_Amount` | 人民币金额（= Amount × FX_Rate） | `102011` |
| `Notes` | 备注 | `剩余本金`, `估算` |

**Category 枚举：**

| 代码 | 含义 | 录入方式 |
|------|------|---------|
| `Asset_Current` | 流动资产（现金/公积金） | 手动 |
| `Asset_Investment` | 金融投资 | 2026Q2 起自动聚合，早期手动 |
| `Asset_RealEstate` | 不动产/车辆 | 手动估算 |
| `Asset_PrivateEquity` | 私募股权 | 手动 |
| `Asset_BadDebt` | 坏账（原值 + Provision 抵减行） | 手动 |
| `Liability_Current` | 流动负债（信用卡等） | 手动 |
| `Liability_LongTerm` | 长期负债（房贷/车贷） | 手动，填剩余本金 |
| `Liability_Family` | 家庭内部负债 | 手动 |

**坏账计提写法**（原值 + 抵减行各一行）：
```csv
2026Q2,Asset_BadDebt,Loan,基础工程投资,3400000,CNY,1.0,3400000,原始出资额
2026Q2,Asset_BadDebt,Provision,坏账准备,-1700000,CNY,1.0,-1700000,50%计提
```


---

## 7. EC2 数据拉取

### 7.1 背景

EC2（AWS ap-southeast-2）每天自动运行 `daily_push.py`，拉取并写入以下文件到 `~/data/`：

| 文件 | 内容 | 用途 |
|------|------|------|
| `market_cache.json` | VIX/VXN/QVIX/PE/价格等当日市场数据 | Dashboard Market Tab 直接读取 |
| `pe_history_us.json` | SAP/腾讯等美股+港股每日 PE 快照 | 基本面面板历史分位数 |
| `vol_history.json` | QVIX 每日快照 | QVIX 动态历史分位数 |

当公司网络无法访问 yfinance/akshare 时，从 EC2 拉取缓存可恢复 Dashboard 正常显示。

### 7.2 一键拉取命令

终端运行：

```bash
ff-pull
```

输出示例：
```
Pulling from EC2...
  ✓ market_cache.json
  ✓ pe_history_us.json
  ✓ vol_history.json
Done.
```

文件自动写入 iCloud 数据目录，Dashboard 无需重启即可读取最新数据。

> `ff-pull` 已配置在 `~/.zshrc`，新终端窗口执行 `source ~/.zshrc` 或重开终端后生效。

### 7.3 手动 scp（备用）

如果 `ff-pull` 不可用，手动运行（EC2 地址和 PEM 路径见 `~/.zshrc` 中的 `ff-pull` 定义）：

```bash
scp -i ~/PEM/<your-key>.pem ec2-user@<EC2_HOST>:~/data/market_cache.json \
    "/Users/.../FamilyFund/data/market_cache.json"
# 同理拉取 pe_history_us.json 和 vol_history.json
```
