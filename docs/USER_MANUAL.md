# FamilyFund 用户操作手册

> **版本**: v1.0  
> **最后更新**: 2026-04-18  
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
**耗时**：约 10-15 分钟

### 步骤

**① 盘点持仓**

打开各银行/券商 APP，记录每个持仓的当前价格和市值。

**② 打开 Weekly Update Tab**

Dashboard → Weekly Update tab，系统自动加载上周快照作为模板（所有 NCF 清零）。

**③ 更新持仓数据**

在持仓表中更新每行的：
- `Current_Price`：当前净值/股价（原始币种）
- `Shares`：当前份额/股数（如有变动）

**④ 重算市值**（推荐）

点击持仓表下方的「🔄 重算市值」按钮，系统自动计算 `Total_Value = Shares × Current_Price × Exchange_Rate`，Cash 行跳过。

> 如某持仓市值需要手动指定（如理财产品直接填市值），在重算后直接在表格中覆盖对应行的 `Total_Value` 即可。

**⑤ 登记调仓操作**（如有买卖）

打开"⚖️ 调仓辅助器"，逐笔录入本周买卖，详见 [第3节](#3-调仓辅助器使用)。

> 每笔买入/卖出操作完成后，系统在点击「应用」时自动写入 `transaction.csv`，作为调仓决策复盘的历史记录。

**⑥ 登记 SAP 归属**（如有归属）

在 Company_Stock 对应行手动填写 NCF，详见 [第4节](#4-sap-股票操作)。

**⑦ 填写日期并提交**

选择本周日期，点击"保存快照"。系统自动校验并追加到 portfolio.csv。

**⑧ 查看 Dashboard**

切换到 Dashboard tab，刷新页面，所有指标自动重算。

### 特殊情况：现金分红到账

当成都银行、腾讯控股、红利ETF 等持仓发生**现金分红**（不是红利复投）时：

> **临时处理方案**：将分红记为 Cash 外部入金，整体 NAV 计算正确，单资产盈亏会轻微失真（分红收益不体现在来源持仓上）。

操作步骤：
1. 在当周 Weekly Update 的调仓辅助器中，点「＋ 外部入金」
2. 金额填分红**实际到账**金额（税后）
3. （手续费字段不适用，留空）
4. Apply 后 Cash 余额自动增加，NCF 正确记录

> 红利**复投型**基金无需操作，净值已自动包含分红收益。



| 错误 | 原因 | 处理 |
|------|------|------|
| 新日期必须晚于上次快照 | 日期填错 | 修正日期 |
| Asset_Class 无效 | 类别拼写错误 | 从下拉列表选择 |
| Total_Value 不能为负 | 数字填错 | 检查对应行 |
| NCF 远超市值 | 可能录入金额有误 | 核对调仓金额 |

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
- 赎回完全清仓的基金：卖出金额 = 赎回到账金额，该行 Total_Value 填 0（或删行）
- 如无调仓，跳过此步骤即可，所有资产 NCF 保持 0

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
  - `Net_Cash_Flow` = 本次归属 Cost_CNY 之和（即你实际支付的成本）

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

分红以股票形式再投资：

1. SAP Stock tab → Add Dividend Transaction（Date、Price、Qty）
2. 下次对账：更新对应 Company_Stock 行的 Shares 和 Total_Value
   - Own SAP Dividend: `NCF` = Cost_CNY
   - Move SAP Dividend: `NCF` = 分红全额市值（CNY）

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
| **SAP Own SAP 归属** | Company_Stock 行 = +Cost_CNY（实际支付成本） |
| **SAP Move SAP 归属** | Company_Stock 行 = +归属市值 CNY（FMV） |
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
- `Inflow_Other`：基金外特殊收入
- `Outflow_Major`：基金外大额支出

**Step 3 — 刷新 Dashboard**

保存 CSV 后，刷新浏览器，Quarterly Report Tab 自动加载新季度数据。选择 `2026Q2` 作为当前季度，`2026Q1` 作为对比季度，即可查看 QoQ 对比和瀑布图。

---

### 6.2 查看与导出

1. 打开 Dashboard → **Quarterly Report** Tab
2. 顶部下拉选择**当前季度**和**对比季度**
3. 查看 KPI 卡片、资产负债表、瀑布图、资产结构对比图
4. 点击「📄 下载季度 PDF 报告」导出 2 页 A4 横版报告

---

### 6.3 balance_sheet.csv 字段说明

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
