# 季度家庭财报工具 — 架构设计与实施方案

> **状态**: 设计已完成，待数据迁移后实现
> **日期**: 2026-04-14
> **目标项目**: FamilyFund（合并扩展，Tab 6）
> **前置条件**: Tab 5 市场温度计先行实现（见 DESIGN_MARKET_MONITOR.md）
> **实现前置**: 用户需先手工将 25Q4 和 26Q1 历史数据录入 `balance_sheet.csv` 和 `cashflow_log.csv`，完成数据迁移后方可开始实现阶段

---

## 一、背景与目标

### 现状

| 工具 | 频率 | 覆盖范围 | 局限性 |
|------|------|----------|--------|
| FamilyFund | 周频 | 投资组合（NAV净值） | 无负债端，无房产/公积金等非投资资产 |
| QuarterlyReport.xlsx | 季频 | 完整资产负债表 | 手工维护，无自动化，无历史对比 |

### 目标

构建一个**季度家庭财报工具**，能够：
1. 整合投资组合数据（来自 FamilyFund portfolio.csv）+ 全量资产负债数据
2. 自动生成机构级三张财务报表（资产负债表、损益表、现金流量表）
3. 支持 QoQ/YoY 对比（如 25Q4 → 26Q1）
4. 一键导出季度 PDF 报告

---

## 二、核心决策：合并进 FamilyFund（作为独立模块扩展）

### 决策理由

| 维度 | 分析 |
|------|------|
| **数据复用** | 投资类资产（固收/股票/黄金）在两者间完全重叠，合并可避免双录入，以 `portfolio.csv` 为单一数据源 |
| **代码复用** | `nav_engine`、`pdf_report`、`fx_service`、Streamlit 框架全部可直接复用 |
| **工作流统一** | 季末一次结账，同时完成 FamilyFund 周更 + 季报生成 |
| **数据隔离** | 新增独立 CSV 文件存放负债/非投资资产，**不改动 `portfolio.csv` 结构** |
| **零破坏** | 所有改动为追加式（新文件 + 新 Tab），现有 4 个 Tab 功能完全不受影响 |

---

## 三、数据模型设计

### 3.1 现有（不变）

- `FamilyFund/data/portfolio.csv` — 投资组合周频快照，不做任何修改

### 3.2 新增：`data/balance_sheet.csv`

季度末全量资产负债快照。**投资类资产行由引擎自动从 `portfolio.csv` 聚合，不手工录入。**

```
Quarter, Category, Sub_Category, Account, Amount, Currency, FX_Rate, CNY_Amount, Notes
```

**字段说明：**

| 字段 | 说明 | 示例 |
|------|------|------|
| `Quarter` | 季度标识 | `2025Q4` |
| `Category` | 大类（见枚举） | `Asset_Current` |
| `Sub_Category` | 子类 | `Cash`, `Mortgage`, `CreditCard` |
| `Account` | 账户/项目名称 | `招商银行`, `九号公馆房贷` |
| `Amount` | 原币金额（投资类填 0，自动聚合） | `171156` |
| `Currency` | 货币 | `CNY`, `EUR` |
| `FX_Rate` | 换算汇率 | `1.0` |
| `CNY_Amount` | 人民币金额 | `171156` |
| `Notes` | 备注 | `估算`, `剩余本金` |

**Category 枚举（基于 QuarterlyReport.xlsx 实际内容修订）：**
```
Asset_Current        流动资产（现金账户、公积金、在途资金）
Asset_Investment     投资理财（★由引擎自动聚合，不手工录入）
Asset_RealEstate     不动产 + 车辆（公允价值估算）
Asset_PrivateEquity  私募股权（精密制造A轮等）
Asset_BadDebt        预备提记坏账（原值显示，坏账准备单独列抵减项）
Liability_Current    流动负债（信用卡账单）
Liability_LongTerm   长期负债（房贷、车贷）
Liability_Family     家庭内部借款（丈母娘注资等，独立于银行贷款）
```

**示例数据（25Q4）：**
```csv
Quarter,Category,Sub_Category,Account,Amount,Currency,FX_Rate,CNY_Amount,Notes
2025Q4,Asset_Current,Cash,招商银行,171156,CNY,1.0,171156,
2025Q4,Asset_Current,Cash,工商银行,4960,CNY,1.0,4960,
2025Q4,Asset_Current,Cash,支付宝余额宝,15777.29,CNY,1.0,15777.29,
2025Q4,Asset_Current,Cash,中信,50554.08,CNY,1.0,50554.08,
2025Q4,Asset_Current,ProvidentFund,住房公积金,82458.81,CNY,1.0,82458.81,
2025Q4,Asset_Investment,auto,portfolio_aggregate,0,CNY,1.0,0,自动从portfolio.csv聚合
2025Q4,Asset_RealEstate,Apartment,九号公馆,1800000,CNY,1.0,1800000,估算
2025Q4,Asset_RealEstate,Apartment,麓湖生态城,1500000,CNY,1.0,1500000,估算
2025Q4,Asset_PrivateEquity,A_Round,精密制造A轮,1000000,CNY,1.0,1000000,
2025Q4,Liability_Current,CreditCard,招商未出账单,69589,CNY,1.0,69589,
2025Q4,Liability_Current,CreditCard,中国银行实时欠款,97.99,CNY,1.0,97.99,
2025Q4,Liability_Current,CreditCard,工商银行实时欠款,25481.59,CNY,1.0,25481.59,
2025Q4,Liability_Current,CreditCard,京东白条,779.45,CNY,1.0,779.45,
2025Q4,Liability_LongTerm,AutoLoan,Mega车贷,153333.33,CNY,1.0,153333.33,剩余本金
2025Q4,Liability_LongTerm,Mortgage,九号公馆房贷,791000,CNY,1.0,791000,
2025Q4,Liability_LongTerm,Mortgage,麓湖生态城房贷,524000,CNY,1.0,524000,
```

### 3.3 新增：`data/cashflow_log.csv`

**重新澄清定位（2026-04-22）**

`cashflow_log.csv` **只记录家庭基金外的现金流**，用于季度损益表中解释"家庭净资产变化 - 投资收益"的剩余部分。

**不需要记录**在 `cashflow_log.csv` 的内容：
- 打入家庭基金的外部资金 → 已在 `portfolio.csv` 的 Cash NCF 中记录，季度财报引擎可直接汇总
- 家庭注资（如丈母娘注资）→ 同上，打入基金时已在 Cash NCF 有记录

**需要记录**在 `cashflow_log.csv` 的内容：
- **工资净储蓄**：工资收入 - 日常消费（家庭基金外，来自鲨鱼记账聚合脚本，未来可自动化）
- **基金外大额特殊项**：出售资产但未打入基金的收入、大额支出等
- 若鲨鱼记账脚本已实现，工资净储蓄可自动算入，无需手填

**损益表公式**：
```
家庭净资产 QoQ 变化
  = 投资收益（portfolio.csv NAV 引擎，2026Q2 起）
  + 家庭基金外部净流入（portfolio.csv Cash NCF 季度汇总）
  + 工资净储蓄（鲨鱼记账聚合）
  + 基金外大额特殊项（cashflow_log.csv 手动补录）
```

**实操上**：日常基本不需要填 `cashflow_log.csv`，只有鲨鱼记账捕捉不到的基金外特殊收支才手动补录（每季度通常 0-2 条）。

```csv
Quarter,Date,Amount,Type,Note
2026Q2,2026-05-15,-15000,Outflow_Major,保险年费（基金外支出）
2026Q2,2026-06-01,23000,Inflow_Other,旧车X3置换补贴已收款（未打入基金）
```

**Type 枚举：**
```
Inflow_Salary    工资净储蓄（鲨鱼记账聚合，未来自动化）
Inflow_Other     基金外特殊收入（出售资产等未打入基金的部分）
Outflow_Major    基金外大额支出（保险/装修等）
```

> ⚠️ **已打入家庭基金的资金不要在此重复记录**，portfolio.csv Cash NCF 已是 source of truth。

---

## 四、新增模块设计

### 4.1 `src/quarterly_engine.py`

```
load_balance_sheet(bs_path, portfolio_path) -> pd.DataFrame
    读取 balance_sheet.csv
    找到 Category == Asset_Investment 的行
    从 portfolio.csv 中取对应季末最近一个快照的 Total_Value 合计
    填充 CNY_Amount 字段
    返回完整 DataFrame

compute_net_worth(df, quarter) -> dict
    total_assets: 所有 Asset_* 类的 CNY_Amount 合计
    total_liabilities: 所有 Liability_* 类的 CNY_Amount 合计
    net_worth: total_assets - total_liabilities
    asset_breakdown: 按 Category 分组的占比字典
    liability_breakdown: 同上

compute_qoq(df, q_prev, q_curr) -> dict
    对比两个季度的 net_worth、各大类资产
    返回绝对变化值和变化率

compute_financial_ratios(df, quarter) -> dict
    debt_ratio: 总负债 / 总资产
    current_ratio: 流动资产 / 流动负债
    investment_ratio: 投资资产 / 总资产

generate_balance_sheet_table(df, quarter) -> (asset_df, liability_df)
    返回两个 DataFrame，分别为格式化的资产端和负债端
    含各子类小计和总计行

generate_income_statement(df, cashflow_df, q_prev, q_curr, nav_engine_result) -> dict
    net_worth_change: Δ净资产（来自 QoQ）
    salary_savings: cashflow_log 中 Inflow_Salary 汇总
    investment_gain: nav_engine 季度收益 × 期初投资规模
    family_inflow: cashflow_log 中 Inflow_Family 汇总
    residual: 反推大额消费 = Δ净资产 - 工资储蓄 - 投资收益 - 其他注资
```

### 4.2 `src/quarterly_report.py`

复用 `pdf_report.py` 的 `matplotlib PdfPages` 基础设施，生成 4 页季度财报 PDF：

```
Page 1: 封面 + 核心 KPI 卡片（4列）
    家庭净资产（绝对值 + QoQ 变化率）
    总资产 / 总负债
    资产负债率
    投资组合 NAV（来自 FamilyFund）

Page 2: 资产负债表
    左：资产结构（大类 + 明细表格）
    右：负债结构 + 净资产
    底部：大类资产占比环形图

Page 3: 损益分析
    净资产 QoQ 变化瀑布图：
    期初净值 → +工资储蓄 → +投资收益 → +/-其他 → 期末净值
    QoQ 资产结构对比（并排柱状图）

Page 4: 投资组合深潜
    复用 nav_engine 输出：NAV 趋势 + 资产类别分解
    （本页内容与 FamilyFund PDF Page 1/2 一致）
```

### 4.3 `dashboard/app.py` — 新增 Tab 6

在现有 5 个 Tab 之后追加（Tab 5 为市场温度计），**不改动任何现有 Tab 逻辑**：

```
Tab 6: 季度财报 (Quarterly Report)
    ┌─ 控制栏 ─────────────────────────────────────────────┐
    │ 季度选择：[26Q1 ▼]   对比季度：[25Q4 ▼]             │
    └──────────────────────────────────────────────────────┘
    
    ┌─ KPI 卡片（4列）────────────────────────────────────┐
    │ 家庭净资产  │ QoQ 变化  │ 资产负债率  │ 流动比率    │
    └──────────────────────────────────────────────────────┘
    
    ┌─ 资产负债表（左右双列）────────────────────────────┐
    │ 资产端（含小计/合计）  │ 负债端（含小计/合计）      │
    └──────────────────────────────────────────────────────┘
    
    ┌─ 净资产变化瀑布图（Plotly waterfall chart）─────────┐
    └──────────────────────────────────────────────────────┘
    
    ┌─ QoQ 资产结构对比（并排柱状图）────────────────────┐
    └──────────────────────────────────────────────────────┘
    
    [生成季度PDF报告]  [导出 CSV]
```

---

## 五、文件变更清单

| 文件路径 | 变更类型 | 说明 |
|----------|----------|------|
| `data/balance_sheet.csv` | 新增 | 季度末资产负债快照，含 25Q4 + 26Q1 迁移数据 |
| `data/cashflow_log.csv` | 新增 | 季度外部现金流记录 |
| `src/quarterly_engine.py` | 新增 | 季度财报核算引擎（约 200 行） |
| `src/quarterly_report.py` | 新增 | 季度 PDF 报告生成器（复用 pdf_report.py 基础设施） |
| `dashboard/app.py` | 修改 | 追加 Tab 6，不改动现有 Tab 1-5 逻辑 |
| `tests/test_quarterly_engine.py` | 新增 | 核算引擎单元测试 |
| `requirements.txt` | 不变 | 所有依赖已存在（pandas, plotly, matplotlib） |

---

## 六、从 xlsx 迁移数据

### 25Q4 关键数字（已从 xlsx 解析）

| 大类 | CNY 金额 |
|------|----------|
| 流动现金 | 招商 171,156 + 工行 4,960 + 余额宝 15,777 + 中信 50,554 + 公积金 82,459 = **324,906** |
| 在途资金 | 长城短债 10,000 + 余额宝 12,253 = **22,253** |
| 投资理财 | 自动聚合（portfolio.csv 2025-12-31 快照） |
| 房产（估算） | 九号公馆 + 麓湖（待填入估值） |
| 私募股权 | 精密制造A轮 **1,000,000** |
| 流动负债 | 69,589 + 98 + 25,482 + 779 = **95,948** |
| 长期负债 | 车贷 153,333 + 房贷 791,000 + 524,000 = **1,468,333** |

### 26Q1 关键变化

| 变化 | 说明 |
|------|------|
| 新增中国银行 501,848 | 大额现金账户（可能为在途） |
| 招行周周宝大幅增加 | 803,684（上季 600,059） |
| 招行长城短债大幅增加 | 501,712（上季 49,983） |
| 新增融资负债 | 丈母娘注资 500,000（需在负债端标注） |
| 新增 ES8 车贷 | 300,000 |
| SAP 股价下跌 | 1,176 EUR/股（上季 1,717 EUR/股），总值从 614,090 → 478,931 |

---

## 七、实施阶段

### Phase 1 — 数据层（1 天）
- [ ] 初始化 `balance_sheet.csv`，迁移 25Q4 + 26Q1 数据
- [ ] 初始化 `cashflow_log.csv`，补录已知外部现金流

### Phase 2 — 引擎层（1-2 天）
- [ ] 实现 `quarterly_engine.py` 全部核心函数
- [ ] 编写 `tests/test_quarterly_engine.py`（对标 FamilyFund 的 98 项测试风格）

### Phase 3 — 展示层（1-2 天）
- [ ] 在 `dashboard/app.py` 追加 Tab 6
- [ ] 实现 `quarterly_report.py` PDF 生成器

---

## 八、数据边界与 cashflow_log 定义（2026-04-21 更新）

### 8.1 两个资金池的边界

家庭资产分两层管理，边界清晰：

```
┌─────────────────────────────────────────────┐
│              家庭基金（~350万）               │
│  固收 / 权益 / 黄金 / SAP / Cash(¥9.6万)     │
│  → FamilyFund portfolio.csv 追踪             │
│  → 周频 NAV 核算                             │
└─────────────────────────────────────────────┘
         ↑ 工资划入 / 旧车出售等大额收入
         ↓ 暂无（基金不向外划出，只有取出消费）

┌─────────────────────────────────────────────┐
│           基金外日常现金（~20万）             │
│  信用卡还款 / 日常消费 / 应急备用             │
│  → 不纳入 FamilyFund                        │
│  → 鲨鱼记账 App 追踪                         │
└─────────────────────────────────────────────┘
```

**关键原则**：20 万日常现金是运营资本，不是投资资产，不纳入家庭基金。信用卡账单、日常消费均在基金外处理。

### 8.2 cashflow_log.csv 的正确定位

cashflow_log **只记录实际划入家庭基金的外部资金**，每季度通常只有 2-5 条：

```csv
Quarter, Date,       Amount,   Type,           Note
2026Q1,  2026-01-31, +50000,   Inflow_Salary,  工资本季划入基金金额
2026Q1,  2026-02-01, +500000,  Inflow_Family,  丈母娘注资
2026Q2,  2026-06-15, +230000,  Inflow_Other,   旧车出售划入基金
```

**不在 cashflow_log 里记录**：
- 日常消费（在鲨鱼记账里）
- 信用卡还款（基金外现金处理）
- 贷款月供（负债端每季度更新余额即可）

### 8.3 鲨鱼记账的角色：损益表辅助数据

鲨鱼记账 CSV 不直接生成 cashflow_log，而是用于**季度损益表**中计算"工资净储蓄"：

```
季度净资产变化 = 投资收益（NAV引擎）
              + 工资净储蓄（鲨鱼记账聚合）
              + 大额特殊项（旧车出售、礼金等）
              + 家庭注资（丈母娘等）
```

其中"工资净储蓄"由脚本从鲨鱼记账自动计算：
```
工资净储蓄 = 工资收入 - 日常消费（排除贷款还款本金部分）
```

### 8.4 贷款处理规则

等额本息月供包含本金+利息，对季度财报的处理方式：

- **资产负债表负债端**：每季末直接从银行 APP 读取贷款剩余本金，不拆分
- **损益表**：利息部分折进日常消费总支出（不单独追踪，简化操作）
- **cashflow_log**：不记录贷款月供（不影响家庭基金，基金外处理）

### 8.5 季度 Workflow

```
每季末（3月/6月/9月/12月底）执行以下步骤：

Step 1 — 基金层（~5分钟）
  ├── 完成当季最后一次 Weekly Update
  └── 记录 cashflow_log.csv 本季实际划入基金的资金（2-5条）

Step 2 — 日常账本层（~5分钟）
  ├── 导出鲨鱼记账 CSV（当季完整数据）
  └── 运行 scripts/import_cashflow.py → 自动聚合工资净储蓄和大额项

Step 3 — 资产负债表（~15分钟，手工）
  ├── 更新 balance_sheet.csv 各账户余额
  │   ├── 流动现金：各银行 APP 查余额
  │   ├── 贷款剩余本金：各银行 APP 查
  │   ├── 房产估值：按需更新（每年1-2次即可）
  │   └── 其他（公积金、私募等）：按需更新
  └── Asset_Investment 行：quarterly_engine 自动从 portfolio.csv 聚合

Step 4 — 生成报告（自动）
  └── Dashboard Tab 6 → 点击"生成季度报告" → PDF 自动生成
```

### 8.6 鲨鱼记账 2026Q1 数据分析

导出文件：`data/2026Q1/鲨鱼记账明细.csv`，UTF-16 编码，Tab 分隔，字段：`日期、收支类型、类别、金额、备注`，共 669 条。

**收入汇总（2026Q1）**：

| 类别 | 金额 | 损益表归类 |
|------|------|-----------|
| 工资 | ¥333,067 | 工资净储蓄分子 |
| 公积金 | ¥96,358 | 工资净储蓄分子（含2月补缴¥82,459）|
| 房租 | ¥34,520 | Inflow_Other（不进基金则忽略）|
| 孩子礼金 | ¥28,033 | Inflow_Other（不进基金则忽略）|

**支出汇总（2026Q1）**：

| 类别 | 金额 | 处理方式 |
|------|------|---------|
| 公司车 | ¥43,588 | Outflow_Major（首付+购置税+保险，一次性资本支出）|
| 贷款月供 | ¥36,491 | 排除在消费外（负债偿还，资产负债表直接更新余额）|
| 日常消费合计 | ~¥107,292 | 工资净储蓄分母 |

**工资净储蓄估算（2026Q1）**：
```
(¥333,067 + ¥96,358) - ¥43,588（公司车）- ¥107,292（日常消费）= 约 ¥278,545
```
注：房租和礼金视划入基金情况决定是否加入。

**待实现：`scripts/import_cashflow.py`**
- 读取鲨鱼记账 CSV（UTF-16）
- 按类别映射：贷款→排除，消费类→净储蓄分母，大额项→单独列出
- 季末运行一次，自动输出损益表所需数据



## 八、验证方式

1. `python -m pytest tests/test_quarterly_engine.py -v`
2. `streamlit run dashboard/app.py` → 打开 Tab 6，验证 26Q1 资产负债表数字与 xlsx 一致
3. 点击"生成季度PDF报告"，验证 4 页 PDF 内容完整
4. 检查 `Asset_Investment` 行是否从 `portfolio.csv` 最近季末快照正确自动聚合
5. 验证 25Q4 → 26Q1 QoQ 对比数字逻辑正确

---

## 九、后续演进方向（Phase 4+，可选）

| 功能 | 价值 | 复杂度 |
|------|------|--------|
| 净资产历史走势折线图（季频） | 高 | 低 |
| 蒙特卡洛退休压力测试 | 高 | 高 |
| 房产估值接入（链家/贝壳API） | 中 | 中 |
| 损益表自动化（接入银行账单） | 高 | 高 |
| 多年度 YoY 趋势报告 | 中 | 低 |
