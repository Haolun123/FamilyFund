# FamilyFund — CLAUDE.md

项目上下文和开发约定，帮助 Claude 在后续对话中快速理解背景、避免重踩已知的坑。

---

## 安全约定

**绝对不能写入文档、代码或 commit 的信息：**
- IP 地址、域名、主机名（EC2、服务器等）
- API Key、Token、密码、Webhook URL
- PEM 文件路径中的具体文件名
- 个人邮箱、手机号等 PII

涉及连接信息时，文档只写占位符（如 `<EC2_HOST>`、`<your-key>.pem`），并注明"见 `~/.zshrc` 或本地配置文件"。

---

## PDF 报告排版原则

**不要吝啬页面，保证信息充分展示。**

- 每张图表/表格给足空间，宁可多一页也不要把内容压缩到看不清
- 一页放一个主题：柱状图单独一页、表格单独一页，不强行合并
- 字体不小于 8pt（正文），标签/标题不小于 10pt
- 避免在同一行/同一区域塞入两个以上的图表

---

## 项目定位

家庭基金管理系统。将家庭全部金融资产视为一支"虚拟基金"，用公募基金标准的**份额净值法（TWR）**进行净值化管理。

- 数据存储：CSV 纯文本，iCloud 同步，不入 Git
- 唯一输入：`portfolio.csv`（每周快照，每行 = 一个持仓在某日期的值）
- 展示层：Streamlit Dashboard（5 Tabs）+ PDF 报告
- 部署：本地 Docker Compose

---

## 核心数据约定

### NCF（Net_Cash_Flow）语义

这是最容易混淆的地方，务必遵守：

| 情形 | NCF 填写 |
|------|---------|
| 建仓日（第一次录入） | = Total_Value |
| 买入资产 | = +(买入金额 + 手续费)（正数，含费成本） |
| 卖出/赎回资产 | = -(到账金额 - 手续费)（负数，扣费后到手） |
| 外部入金/取出 | 记在 **Cash 行**，= +/- 金额 |
| SAP Own SAP 归属（ESPP） | Company_Stock 行 = +Cost_CNY（实际支付成本，**非**归属市值） |
| SAP Move SAP 归属（RSU） | Company_Stock 行 = +归属市值 CNY |
| 无操作持仓 | = 0 |

买入/卖出 NCF 由调仓辅助器（⚖️ 调仓辅助器）自动写入，不需要手动填。

**ESPP 成本口径说明**：ESPP 是用折扣价主动购买，属于投资决策，NCF 记实际支付金额（Cost_CNY）。折扣带来的差价收益（归属市值 - Cost_CNY）体现为持仓浮盈，计入投资回报而非外部入金。RSU 是无偿归属的薪酬，NCF 记归属时市值（FMV）作为成本基准。

**手续费处理(2026-05-22 更新)**:调仓辅助器里手续费**已计入 NCF**(NCF 含费)。Cash 行同步扣减"成交金额 + 手续费",反映真实账户扣款。fee 字段同时写入 transaction.csv 备注用。

**SAP(Company_Stock)卖出处理(2026-05-30 文档化)**:与一般标的卖出**完全相同**,无需特殊处理。

- **登记方式**:调仓辅助器选"卖出",Asset 选 SAP,Amount_CNY 填到手人民币金额,Fee_CNY 填换汇 + 跨境汇款手续费
- **数据效果**:`Company_Stock NCF -= (到手 - 费)`、`Cash Total_Value += (到手 - 费)`
- **设计前提**:用户的工资账户 / 招行 / 券商账户在 FamilyFund 里**视为 Cash 池**(不按物理账户严格分),所以 SAP 卖出无论钱进哪个具体银行账户,都等价于"回 Cash"
- **不需要新 entry type / 不需要"出金型卖出"选项**——之前讨论过的"卖 SAP 不回基金"场景在用户真实工作流中不存在(钱永远会回到工资/银行账户,而这些都属 Cash 池)

**Company_Stock NCF 入金/出金的不对称性(2026-05-30 辨析)**:你可能注意到 SAP 行 NCF 的语义看起来比一般标的"模糊"——这是真实存在的不对称,但当前实现自洽。详见下方"NCF 守恒律与不对称性"。

---

### NCF 守恒律与不对称性(2026-05-30 辨析,设计文档)

> 本节解释一个看起来"语义模糊"但实际"自洽"的设计权衡。**当前不修复,只文档化**——用户可能未来再思考是否值得清洁化。

**理想的 NCF 守恒律**:
> 每周 portfolio.csv 所有行 NCF 之和 = 当周外部资金净流入

按守恒律,买入腾讯 ¥3.9 万应该:
- 腾讯行 NCF +3.9 万(腾讯收到现金)
- Cash 行 NCF -3.9 万(Cash 流出)
- 合计 = 0(内部调仓,无外部资金动)

**当前实现的实际行为**:
- 腾讯行 NCF +3.9 万 ✅
- Cash 行 NCF **0**(不记调仓流转,只记外部入金/取出)
- 合计 = +3.9 万 ❌ 违反守恒律

**SAP 入金看起来"特殊"的真相**:
- ESPP 归属:SAP NCF +6,800,Cash NCF 0(因为工资账户外部代扣,Cash 真没动)
- 这看似"不对称"——但其实和上面腾讯买入**遵循同样的实现规则**(Cash 行不记调仓 NCF)
- SAP 的"不对称"特别**显眼**只因为它没有对应的 Cash 流出可看,而一般标的的不对称隐藏在了"Cash NCF 漏记调仓"里

**为什么没改实现就能正常工作**:

| 算法 | 用什么数据 | 是否受影响 |
|------|----------|----------|
| `compute_fund_nav` / `compute_xirr`(整体 NAV / XIRR) | filter Cash 行 + Company_Stock 行 + 建仓日全部 | ❌ 不受影响(已正确反映外部现金流) |
| `compute_class_nav`(单类别 NAV) | 该类自己 NCF | ❌ 不受影响(每个类自洽) |
| `compute_cost_basis`(盈亏分析) | 各资产自己 NCF 累加 | ❌ 不受影响(成本基准 = 含费历史买入) |

所以**两条独立的逻辑链都自洽**:
1. **Fund-level 链**:NCF 选择性看(只 Cash + Company_Stock + 建仓日)→ 反映真实外部现金流
2. **Asset-level 链**:NCF 累加 = 该资产含费成本 → 用于盈亏分析

**两条链不冲突,但也不彼此校验**——所以会出现微小的"NCF 累加 vs 实际现金流"差额(2026-05-30 已知约 ¥536,占总资产 0.014%)。

**这部分差异的来源**:
- 港股/SAP 跨境汇率取整、买卖手续费的微小不平衡
- 当前在 dashboard help 文字里已解释("累计收益 vs 总盈亏"两口径差异)

**未来如果想清洁化(暂不做)**:
- 选项 A:Cash 行 NCF 改成完整记账(含调仓)→ 但 XIRR 算法要重写,牵动大
- 选项 B:portfolio.csv 加一列 `NCF_Internal` 区分调仓 vs 外部 → schema 变更
- 选项 C:接受现状,把"NCF 守恒律不严格成立"作为已知设计权衡

**当前选 C**——理由是:
1. 所有指标(累计收益、XIRR、TWR、盈亏分析)在修复 79cb0ab 后均给出**正确**数字
2. 差异 ¥536 / 374 万 = 0.014%,不影响任何投资决策
3. 改造工程量大,M1 心智压力最小化原则下不值得

---

**旧约定(已废弃)**:
- 内部调仓 NCF = 0,只有外部入金记 NCF。这是错的,会导致分类成本和 TWR 失真。
- NCF 不含费,手续费仅备注。这会导致 Cash 长期偏高(每笔交易遗留几十元)。

### Cash 的特殊处理

Cash 是调仓中转池，本身不增值，**不参与**：
- 分类 NAV 对比图
- 资产配置饼图
- 分类业绩一览表
- 盈亏分析（成本 / 市值 / 收益率）

Cash 仅在**基金总览**的总资产 KPI 中体现。代码中通过 `all_classes` 过滤（`dashboard/app.py`）和 `pl_df` 过滤（盈亏分析）实现。

### 盈亏分析口径

- **总成本** = 金融资产历史买入 NCF 累计（不含 Cash、不含 Market_Value=0 的清仓行）
- **累计收益率** = 简单收益率：`(当前总资产 - 初始投入) / 初始投入`
  - 初始投入 = `fund_nav_df.iloc[0]['Total_Value']`（建仓日全部持仓 Total_Value 之和）
- **TWR** 体现在"单位净值"和"年化收益率(TWR)"两个独立 KPI，不和累计收益率重复

### 建仓基准日

2026-04-10 是建仓日，以此日期的 Total_Value 为成本基准，历史盈亏不追溯。

### 外币资产（HKD / EUR / USD）的 Currency / Exchange_Rate 约定

**核心原则**：portfolio.csv 里 `Currency` 表示资产的**原始报价币种**，不是"目标计价币种"。`Total_Value = Shares × Current_Price × Exchange_Rate` 永远等于人民币市值。

| 资产 | Currency | Current_Price | Exchange_Rate |
|------|---------|---------------|---------------|
| A 股个股 / 国内基金 | CNY | 人民币价 | 1.0 |
| 港股（HK0700 等）| **HKD** | 港币原始价 | HKD/CNY 实时汇率（~0.87） |
| SAP（法兰克福） | EUR | 欧元价 | EUR/CNY 实时汇率（~7.92） |
| 黄金（GOLD/GOLD.P）| CNY | 元/克（已换算） | 1.0 |

**重算市值公式**：`Total_Value = Shares × Current_Price × Exchange_Rate`

**港股自动化（2026-05-22 实现）**：
- `price_fetcher._fetch_hk_with_fx()` 拉港币股价 + HKD/CNY 实时汇率
- 调仓辅助器/Weekly Update 点"刷新价格"时，港股行自动写入 `Currency=HKD, Exchange_Rate=<实时汇率>, Current_Price=<港币价>`
- 调仓辅助器登记买入港股时，**主金额栏填券商显示的人民币结算金额**（不需手动换汇），Price 字段填港币原始单价（仅 transaction.csv 备注）
- 历史 4/10-5/15 腾讯行的 `Currency=CNY/Rate=1.0/Current_Price=已换汇人民币价` 是**历史遗留**，保持不动；从 5/22 起自动进入 HKD 模式

**旧约定（已废弃）**：
- 港股 Currency=CNY, Exchange_Rate=1.0，Current_Price 手动换汇为人民币。这导致每周点"刷新价格"自动覆盖为港币价后，重算市值高估 ~15%，需手动修复。

---

## 资产类别（8 种）

```
US_Blend_Fund   美股宽基基金（标普500 QDII）
US_Growth_Fund  美股成长基金（纳指100 QDII）
CN_Index_Fund   A股指数基金（沪深300、中证A500）
ETF_Stock       ETF与股票（红利ETF、A股个股、港股）
Fixed_Income    固定收益（银行理财、短债基金）
Gold            黄金（实物、纸黄金）
Company_Stock   公司股票（SAP ESPP + RSU）
Cash            现金储备（约10万，不追踪日常消费账户）
```

---

## 部署约定

### 代码改动后（常规）

```bash
docker compose restart
```

`src/` 和 `dashboard/` 已通过 volume 挂载，restart 即可生效，**不需要重建镜像**。

### 需要重建镜像的情况

仅在修改 `Dockerfile` 或 `requirements.txt` 时：

```bash
docker compose up -d --build
```

### 验证代码是否生效

restart 后如行为不符预期，先确认容器内文件是否是最新的：

```bash
docker exec familyfund grep -n "关键词" /app/dashboard/app.py
```

### 常见陷阱

- `docker compose restart` ≠ 重建镜像，旧镜像的代码不会更新
- `docker compose build --no-cache` 会重新下载所有依赖包，耗时较长，非必要不用
- `fonts-noto-cjk` 在某些网络环境下载失败，Dockerfile 中已移除（改用系统自带字体）

---

## 文档维护约定

实现新功能后必须同步更新以下文档，不能只改代码：

| 文档 | 何时更新 |
|------|---------|
| `docs/IMPROVE_LIST.md` | 完成功能后移到"已完成"；新问题/待办添加到对应分类 |
| `docs/ARCHITECTURE.md` | 架构变更、新增模块、设计决策变化时更新；版本号递增 |
| `docs/USER_MANUAL.md` | 操作流程变更时更新（Weekly Update 步骤、调仓辅助器规则等） |
| `docs/DESIGN_*.md` | 对应功能的设计文档有变化时更新（如矩阵分界值、数据源切换）|

---

## 测试约定

**每个新增的 `src/` 模块必须有对应的测试文件**，放在 `tests/test_<module_name>.py`。

- 测试文件命名：`tests/test_dca_manager.py`、`tests/test_vxn.py` 等，一个模块一个文件
- 不要把多个模块的测试打包进同一个文件（`test_new_features.py` 仅保留无独立文件的历史测试）
- 跑单个测试：`docker cp tests/<file>.py familyfund:/app/tests/<file>.py && docker exec familyfund python -m pytest /app/tests/<file>.py -v`
- 跑完整 test suite(push 前视情况,见下方"Push 前流程"):`docker exec familyfund python -m pytest /app/tests/ -q`
- 网络依赖的测试用 `pytest.skip` 处理，不能让网络不可用导致整体失败

### 何时强制写测试(纪律 C,2026-05-28 加入)

| 改动类型 | 是否写测试 |
|---------|----------|
| 新增 src/ 模块 | ✅ 必须 |
| 修改 src/ 模块的对外函数行为 | ✅ 必须 |
| **修复 src/ 模块的 bug** | ✅ **必须写一个回归测试**(reproduce bug 的场景) |
| src/ 模块内私有函数重构(行为不变) | ❌ 可选 |
| dashboard/app.py UI 调整 | ❌ 不强制 |
| docs/ 文档变更 | ❌ 不写 |
| 配置变更(decisions.json / target_allocation.json 等)| ❌ 不写 |

**核心规则**:**这个 bug 6 个月后我重构相关代码,会不会重新踩坑?** 会的话就写回归测试。

### Push 前流程(2026-05-30 更新)

**Push 前不再"必跑"完整 test suite,改为问用户拍板**。原因:完整 suite 需 ~2 分钟,大多数小改动不需要跑(用户主观判断风险面比死规则更准)。

具体流程:
- 改动只涉及单个模块/文件 → commit 时**问一句**: "需要跑完整 test suite 吗?"
- 用户回 "**跑**" → `docker exec familyfund python -m pytest /app/tests/ -q`,全过再 push
- 用户回 "**不用**" → 至少跑**新增/相关的单文件测试**(`pytest tests/test_xxx.py`),通过即可 push
- 用户回 "**直接 push**" → 跳过测试,push

**默认建议跑的场景**(可主动建议用户跑):
- 改了 `src/` 多个模块(交叉影响风险高)
- 改了 `nav_engine.py` / `fundamentals.py` / `position_percentile.py` 等核心
- 删除/重构代码(不仅是 bug fix)
- schema 变更

**默认建议不跑的场景**:
- 仅 `dashboard/app.py` 的 UI 文字/help 调整
- 仅 `docs/` 文档
- 仅 memory / 配置文件

**完成一个 feature 的标准**：代码 ✓ + 文档 ✓ + 测试 ✓ + git push ✓(完整 suite 视情况)

---

## 代码结构关键点

| 文件 | 职责 | 注意 |
|------|------|------|
| `src/nav_engine.py` | 核心计算引擎（NAV、XIRR、Sharpe、Calmar） | 改算法逻辑在这里 |
| `dashboard/app.py` | 展示层（所有 Tab 的 UI） | 改展示逻辑只改这里，不动 nav_engine |
| `src/market_monitor.py` | 市场温度计（PE/VIX/QVIX 矩阵） | EC2 每日推送依赖此模块 |
| `src/pdf_report.py` | PDF 报告生成 | 使用 matplotlib PdfPages，零新增依赖 |
| `data/portfolio.csv` | 唯一数据源（iCloud 同步，不入 Git） | 路径由 `$FAMILYFUND_DATA` 环境变量指定 |

### Dashboard 数据流

```
portfolio.csv
  → load_data() [@st.cache_data]
  → (raw_df, fund_nav_df, class_nav_dict, allocation_df, cost_basis_df, xirr, sharpe, calmar)
  → Tab 1 展示
```

缓存清除：修改 CSV 后如 Dashboard 未更新，调用 `st.cache_data.clear()` 或重启。

### 调仓辅助器（⚖️）使用范围

**调仓辅助器仅在 Weekly Update Tab 的"步骤三"内使用**，不能脱离 Weekly Update 单独工作。

工作机制：
1. **Weekly Update 启动** → 系统从 portfolio.csv 最新快照复制一份"模板"放进 `session_state['update_template']`
2. **调仓辅助器登记买入/卖出** → 改的是这个**内存模板**（Cash 减、NCF 加），还没落盘
3. **必须最后点"保存新快照"** → 模板才作为新一行追加到 portfolio.csv

实际工作流约定：
- 交易日 ≠ 快照日：周中交易（如 5/18 买腾讯）等到周末做 Weekly Update 时（5/22 或 5/23 这一行）一次性登记本周所有交易
- 一周内多次交易：都在同一次 Weekly Update 里登记多个买入条目
- 模拟看影响：登记后不点"保存"则不会落盘，可以做"What-If"模拟

设计妥协（2026-05-06 决策）：系统只管"本周投多少"，不管"哪天分几次执行"。这意味着交易日到快照日之间的市值波动被"沉淀"到下一周快照里。

### 短信解析应用后的对账纪律（2026-05-23 教训）

**风险点：** 短信解析"应用解析结果"按钮点击后立即触发自动刷新价格 rerun，UI 反馈不显式。用户容易"以为系统漏加 → 手动补加 → 实际重复"，或反向"以为加对了 → 实际短信缺一条 → 漏加"。

**强制对账步骤（保存快照前必须执行）：**
1. 应用短信解析后，**对每个本周有定投的标的**，验证当前 Shares 增量 ≈ 本周买入金额 ÷ 短信净值
2. 检查 transaction.csv 本周新增条数与短信条数一致
3. 累计验证：`portfolio.csv 累计 NCF（4/10 后）== transaction.csv 总买入金额`，两者必须严格相等

**真实出过事的案例（2026-05-22 周）：** 南方纳指 100 I 漏 1 笔 480 元短信 → transaction 漏 1 行 → portfolio NCF 漏 480 → 用户手动补 Shares 误打误撞凑齐份额，但 NCF 累计漏 480 → 5/23 用 transaction 反向对账才发现。

**修复方向**（IMPROVE_LIST P2-#3）：UI 加 diff 高亮、应用后弹确认、保存前自检 NCF vs transaction 一致性。

---

## /sync-research Skill（研报库同步，2026-05-30）

**触发词**：用户说"同步研报"、"更新 Research Tab"、"我买了 XXX 请更新研报库"

此 skill 将新建仓标的从 `_watchlist`（观察）升级为持仓，同步三个地方：
1. 目录：`_watchlist/<folder>` → `<Finance Reports>/<folder>`
2. `ticker_map.json`：从 `观察` 移到 `持仓`，填入 `portfolio_codes`
3. `decisions.json`：若 `current.action == '买入'`，自动改为 `'持有'`

**核心函数**：`src/research_library.py::sync_new_holding(reports_dir, folder_name, portfolio_codes)`

### 执行步骤

1. **确定需要同步的标的**
   - 若用户指定标的名/代码：直接用 `find_folder_by_code(reports_dir, code)` 找 folder_name
   - 若未指定：对比 portfolio.csv 最新日期 vs 上一日期，找新出现的 Code，逐一查 folder

2. **执行同步**
   ```python
   from research_library import sync_new_holding, find_folder_by_code, get_reports_dir
   reports_dir = get_reports_dir(data_dir)
   result = sync_new_holding(reports_dir, folder_name, portfolio_codes=[code])
   ```

3. **告知用户结果**
   - 目录已移动：`moved=True`
   - ticker_map 已更新：`ticker_map_updated=True`
   - decision 已改为持有：`decision_updated=True`
   - 提示："到 Dashboard Research Tab 点 🔄 刷新研报库即可看到变化"

4. **找不到研报 folder 时**
   - 告知用户："该标的（`<code>`）尚无研报，需要先写研报再同步。Finance Reports 目录下没有对应文件夹，或 ticker_map.json 未包含该 portfolio_code"

### 注意

- `sync_new_holding()` **幂等**：对已在持仓的标的重复调用不会报错，也不会重复归档 decision
- Weekly Update 保存快照时如有新建仓且已有研报，Dashboard 会自动弹出"一键同步"提示（无需手动调 skill）
- `/sync-research` 适用于：手动建仓后忘了同步、其他 agent 需要同步、批量同步多个标的

---

## CSV 数据修改纪律（2026-06-30 加入）

**起因：** 2026-06-30 一次失误,用 Python `csv.writer` 直接覆盖 balance_sheet.csv,**绕过了 backup 机制+删除了 Q1 丈母娘注资¥50万负债行**。靠用户的人工备份才发现。

**强制纪律：**

| 数据文件 | 必须走的写入路径 | 禁止 |
|---------|----------------|------|
| `portfolio.csv` | `nav_engine._atomic_write_csv()` | 直接 `df.to_csv()` |
| `balance_sheet.csv` | `quarterly_engine.save_balance_sheet()` | `csv.writer` / `df.to_csv()` |
| `transaction.csv` | dashboard 走步骤三调仓辅助器写入(已有逻辑) | 直接覆盖 |
| `decisions.json` | 直接 Edit,但**改前先打印当前行让用户确认** | 盲目覆盖 |
| `yf_symbols.json` | `fundamentals.save_yf_symbols()` | 直接 json.dump |

**所有 CSV 写入函数应走 `_atomic_write_csv`,获得：**
- 30 份滚动备份(在 `$FAMILYFUND_DATA/backups/`)
- 原子替换(避免半写状态)
- 文件锁(避免并发)

**操作前的纪律：**

任何修改 CSV/JSON 的脚本,**写入前必须先做：**
1. `cp source /tmp/backup_<timestamp>.csv` 留一份原文备份
2. 对比预期改动量(行数+/-多少),与实际是否吻合
3. 如果是用户主动指挥的"补充新数据",改动只能是 append/insert,**绝对不能 delete**

如果非要 rewrite 整个文件,要做到：
- 改完立即 diff 显示给用户看
- 在 commit message 里明确说明被删/被改的行

---

## 已知问题 / 待决策

- **Weekly Update：Total_Value 自动计算** — 建议方案 A（加"重算市值"按钮），待实现
- **`fx_service.py` 缺少重试机制** — 网络请求应加 retry，待实现
- **SAP Tab 默认价格硬编码** — 缓存为空时应提示手动输入，待改进

详见 `docs/IMPROVE_LIST.md`。

---

## 个股研报分析工作流

当用户说"分析 XXX"、"评估 XXX"、"用芒格框架分析 XXX"等类似指令时，按以下流程执行。

### 分析框架（三维度强制叠加，2026-06-15 确立）

每次个股分析或研报复盘，**必须同时使用以下三个维度**，缺一不可：

1. **芒格框架**（`munger-perspective` skill）
   - 逆向思考：怎么买会亏钱？
   - 五模型审视：多元思维 / 逆向 / Lollapalooza / 能力圈 / 激励机制
   - 三筐分类：Yes / No / Too Hard
   - 葡萄干与粪便：有没有致命缺陷？

2. **知识库模型校验**（`~/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/vault/atoms/`）
   - 搜索与该标的相关的 atom 文件（行业特征、护城河、周期、激励、IP等关键词）
   - 至少找到 2-3 个可以直接应用或反驳的 atom
   - 重点 atoms（高频相关）：
     - `cyclical_stocks_reverse_pe_logic`：周期股用PB分位而非PE
     - `yield_on_cost_compounds_over_time`：红利股成本股息率复利
     - `asset_function_is_cycle_dependent`：资产功能是周期依赖的
     - `logic_intact_hold_through_drawdown`：逻辑未破，浮亏不是止损信号
     - `sniper_stink_bid_precommit_entry`：狙击手挂单，不追涨
     - `ip_scarcity_discipline_evergreen`：IP稀缺管理纪律
     - `policy_driven_strategy_asymmetric_risk`：政策驱动不对称风险
     - `subculture_ip_breakout_death_trap`：子文化IP破圈死亡陷阱

3. **中美债务周期视角**（参考 `essays/china_debt_cycle_asset_allocation.md` 和 `essays/us_debt_cycle_analysis.md`）
   - 中国化债进度对该标的的传导链（直接/间接/无关）
   - 美国债务周期（AI增长+温和通胀）对该标的的含义
   - 利率方向对估值的影响（注意区分路径一/路径二）
   - 可观测的领先信号：M1/PPI/居民中长期贷款

### Step 1: 准备工作

- 路径：`$FAMILYFUND_DATA/Finance Reports/`
  - 持仓标的：`<中文名（代码）>/reports/` 直接放在根目录
  - 观察标的：`_watchlist/<中文名（代码）>/reports/` 放在 `_watchlist/` 下
- 文件夹命名规范：`中文名（代码）`，括号必须用全角 `（）`
- 财报 PDF 应已由用户放入 `reports/` 子目录
- 如果文件夹不存在或没 PDF，提醒用户先准备数据，不要继续

### Step 2: 数据采集

1. **读取财报 PDF**：用 Read 工具取最新 1-2 期 PDF 的关键页（首页 + 财务数据章节，通常前 5-10 页）
2. **拉取实时数据**：必须先 `setproxy` 配置代理（参考 `~/.zshrc` 中的 alias）。然后用 Python + yfinance 获取：
   - 主代码：`currentPrice`、`trailingPE`、`forwardPE`、`priceToBook`、`dividendYield`、`returnOnEquity`、`marketCap`、`bookValue`、`trailingEps`、`beta`
   - 同业对比：行业内 5-8 家可比公司同样字段
3. **港 A 双上市判定**：如果标的同时有 A 股代码（`.SS` / `.SZ`）和 H 股代码（`.HK`），必须**同时**拉取双市场数据 + `HKDCNY=X` 汇率，并在分析中加入"A 股 vs H 股决策"章节

### Step 3: 调用芒格 Skill

使用 `munger-perspective` skill 完成框架分析。遵循已有研报的结构（参考 `成都银行投资审视：芒格框架分析.md` 或 `招商银行投资审视：芒格框架分析.md`）。

### Step 4: 写入分析文档

- 路径：`<标的文件夹>/analysis/<中文名>投资审视：芒格框架分析.md`
  - 持仓标的示例：`成都银行（601838.SS）/analysis/成都银行投资审视：芒格框架分析.md`
  - 观察标的示例：`_watchlist/招商银行（600036.SS）/analysis/招商银行投资审视：芒格框架分析.md`

- **文档末尾必须**有 `## 决策记录` 章节，包含 yaml 代码块：

  ````markdown
  ---

  ## 决策记录

  ```yaml
  action: 买入       # 7 选 1：买入 / 加仓 / 持有 / 观察 / 减仓 / 卖出 / 不感兴趣
  market: A股        # 3 选 1：A股 / H股 / N/A
  date: 2026-05-21   # 当天日期
  summary: ≤50字一句话决策摘要
  ```
  ````

  这个块是 `decisions.json` 自动更新的输入源。

### Step 5: 更新 decisions.json

写完文档后，必须更新 `$FAMILYFUND_DATA/Finance Reports/_meta/decisions.json`：

调用 `src/research_library.py` 的 `update_decision()` 函数（或直接读写 JSON），它会自动：
- 把现有的 `current` 归档到 `history` 数组（如果有）
- 写入新的 `current` 决策
- 仅追加，不可删除历史

### Step 6: 更新 ticker_map.json（仅新标的）

如果是首次分析的新标的（`ticker_map.json` 中没有该 folder 的条目），必须添加到 `_meta/ticker_map.json` 的 `持仓` 或 `观察` 下：

```json
"中文名（代码）": {
  "portfolio_codes": ["持仓时使用的 Code 字段"],
  "yf_symbol": "用于基本面分析的 yfinance symbol",
  "full_name": "公司法定全名"
}
```

观察中标的的 `portfolio_codes` 留空数组 `[]`。

### Step 7: 完成提示

告诉用户：
- 分析文档已写入 `<完整路径>`
- 决策已更新到 `decisions.json`
- 到 Dashboard Research Tab 点 🔄 刷新研报库 即可看到（或 60 秒内自动刷新）

### 港 A 双上市分析要点（双市场标的必看）

参考 `招商银行投资审视：芒格框架分析.md` 第九节的格式，必须涵盖：

1. **价差快照**：A 股价（CNY）、H 股价（HKD/换算CNY）、PB、PE、AH 价差百分比
2. **股息税差异**：A 股长期持有 0% vs H 股港股通固定 20%
3. **流动性对比**：日均成交额、买卖价差
4. **港股通摩擦成本**：印花税 0.13% 等约 0.20% 单次买卖
5. **汇率风险**：HKD/CNY 中长期走势
6. **5 年综合年化回报对比**：基于 PB 修复假设
7. **决策**：在 `decisions.json` 的 `market` 字段标注买哪个市场

### 决策枚举速查

| Action | 含义 |
|--------|------|
| 买入 | 当前未持有，建议建仓 |
| 加仓 | 已持仓，建议增加 |
| 持有 | 已持仓，维持不动 |
| 观察 | 暂不操作，监控基本面 |
| 减仓 | 已持仓，建议部分卖出 |
| 卖出 | 已持仓，建议清仓 |
| 不感兴趣 | 评估后排除（如 Too Hard 筐） |
| **不进池** | **质量优秀但不进个股池**（如与已持仓同行业，名额有限）。区别于"不感兴趣"——不感兴趣是质量否定，不进池是组合管理决策 |

### decisions.json 字段（2026-05-22 扩展）

每个标的的 `current` 决策包含：

| 字段 | 类型 | 含义 |
|------|------|------|
| action | enum | 决策枚举（见上表） |
| market | enum | A股 / H股 / N/A |
| date | str | YYYY-MM-DD |
| summary | str | ≤50 字摘要 |
| source_doc | str | 研报文件名 |
| **tier** | str | 核心 / 卫星 / 不进池 / 观察 / 战略持仓（不计入个股池）|
| **style** | str | 高股息 / 成长 / 周期 / 防御 / 混合（如"高股息+周期"）|
| **target_position** | str | 绝对金额，如 "5万" / "3-4万" / "0" / "观察" / "ESPP 持续被动" |
| **pace** | str | 节奏，如 "6 月分批"、"1-2 周一次性"、"已超配，不再加仓" |
| **position_signal** | str | 触发信号，如 "PB 历史分位 + 油价" |
| add_trigger | str | 加仓触发条件 |
| trim_trigger | str | 减仓/止损触发条件 |

详细决策原则（P1-P12）见 `docs/DESIGN_PORTFOLIO_ARCHITECTURE.md`。其中 **P12（2026-05-23 加）**：用户可承受 -25% 回撤；FI 不一步到位降到 target 10%，而是 5 年渐进到 ~30%（每周定投 ~5000 元 × 52 × 5 = 130 万 + 个股池 22 万 + 红利低波 3 万 = ~155 万从 FI 抽到股票）。

### 个股池架构（2026-05-22 决策，详见 OPEN_POINTS）

- **总额度 35 万写死**（P6，2026-05-23 从 30 万调整），约占总资产 9.5%，亏光不影响家庭财务
- **核心仓 5-7 万 × 2-3 个 + 卫星仓 2-4 万 × 3-5 个**（A3 决策）
- **7 个标的进池（2026-05-23 复盘后）**：腾讯（核心）/ 中海油（核心）/ 万华（核心）/ 成都银行（卫星）/ **招商银行（卫星，5/23 进池）** / 长江电力（卫星）/ 泡泡玛特（卫星）
- **金风科技（5/23 改不感兴趣）**——A 股 PB 94 分位过高，研报误用港股 PB
- 招商银行**不进池**（与成都银行同行业）
- SAP / 红利低波 ETF / 宽基 ETF **不算入个股池**（独立战略 / 风格 ETF / 国运暴露）
