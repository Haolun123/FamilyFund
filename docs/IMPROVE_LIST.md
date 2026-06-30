# FamilyFund 功能改进清单

> **最后更新**：2026-05-27
>
> 本文件只做状态跟踪和优先级排序。详细设计见各 `DESIGN_XXX.md` 文件。

---

## ✅ 已完成功能

| 功能 | 实现位置 |
|------|---------|
| 年化收益率（TWR）/ 最大回撤 / XIRR / 夏普 / 卡尔马 | `src/nav_engine.py` |
| 数据原子写入 + 自动备份（保留30份） | `nav_engine._atomic_write_csv` |
| 基准对比（CSI300/S&P500/CPI/M2） | `src/benchmark.py` |
| 风险集中度警示 + 货币敞口可视化 | `dashboard/app.py` Tab1 |
| 市场温度计 + 定投矩阵（5标的）| `src/market_monitor.py` Tab5 |
| 美债10Y收益率展示 | `src/market_monitor.py` Tab5 |
| 黄金 MA200乖离率×VIX 矩阵 | `src/market_monitor.py` Tab5 |
| 个股基本面面板(芒格信号面板,持仓个股动态拉取,yfinance + akshare 双源)| `src/fundamentals.py` Research Tab(2026-05-28 从 Market Tab 迁出)|
| AH 股溢价监测（溢价率 + 1年分位数，动态增删标的）| `src/ah_monitor.py` Tab5，见 `DESIGN_AH_MONITOR.md` |
| 每日企业微信推送 | `src/notifier.py` + `scripts/daily_push.py` |
| NCF 全资产写入调仓辅助器 | `dashboard/app.py` Tab2 |
| Weekly Update 重算市值 | `dashboard/app.py` Tab2 |
| transaction.csv 基础设施 | `dashboard/app.py` Tab2 |
| SAP 盈亏分析（Own/Move/Combined） | `dashboard/app.py` Tab4 |
| 定投策略回测（固定 vs 矩阵） | `src/backtest.py` Tab6 |
| 季度财报（资产负债表 + 瀑布图） | `src/quarterly_engine.py` Tab7 |
| 收益归因分析（TWR 口径，7个时间范围）| `dashboard/app.py` Tab1 Section5 |
| 再平衡建议（偏差柱状图 + 温度计信号）| `dashboard/app.py` Tab1 Section6 |
| AI 周度评估（GLM-4-flash）| `src/ai_weekly.py` Tab1，见 `DESIGN_AI_WEEKLY.md` |
| PDF 报告（6页 A4 横版） | `src/pdf_report.py` |
| 人生阶段规划（里程碑支出曲线 + FI情景对比）| `src/life_stages_engine.py` + `dashboard/app.py` Tab1 Section7，见 `DESIGN_LIFE_STAGES.md` |
| 定投管理模块（DCA Manager）| `src/dca_manager.py` + `dashboard/app.py` Tab5 底部，见 `DESIGN_DCA_MANAGER.md` |
| 第十人系统（Anti-Fragile Allocation Red Teamer）| `src/tenth_man.py` + `dashboard/app.py` Tab8，见 `DESIGN_TENTH_MAN.md` |
| 弹药池与现金流压力测试 | `dashboard/app.py` Tab5 DCA下方，见 `DESIGN_AMMO_POOL.md` |
| Weekly Update 自动化（净值刷新 + 短信解析）| 见 `DESIGN_AUTO_WEEKLY.md` |
| 财务独立测算 + 储蓄率追踪 | 见 `DESIGN_ANALYTICS.md` |
| HTML/PDF 报告全面切换 | Portfolio / Quarterly / 10th Man |
| 仓位管理与组合架构（P1-P12）| 见 `DESIGN_PORTFOLIO_ARCHITECTURE.md`,2026-05-22 完成 |
| F4 PB/PE 历史分位（A 股 akshare + 港股 eniu 长期参考）| `src/position_percentile.py`,2026-05-23 |
| 组合压力测试 + What-If 动态目标 | Tab1 Section7,2026-05-23 |
| 芒格信号面板（4 区 2x2:估值 / 多维位置 / 质量 / 决策）| Research Tab,2026-05-23 |

---

## 🔧 待实现功能

### P2 — 随时可做

| # | 功能 | 设计文档 | 前置条件 |
|---|------|---------|---------|
| 1 | **鲨鱼记账解析 + 季度现金流分析** | `DESIGN_CASHFLOW.md` | Q2 数据(2026-07 启动) |
| 2 | **F5 商品价格抓取**(Brent + 黄金 + 铜 + WTI + 铁矿石)| `DESIGN_PORTFOLIO_ARCHITECTURE.md` F5 节 | 中海油接近建仓窗口(Brent < 60 + PB < 1.0)时再做,当前不需要 |
| 3 | **短信解析 UX 改进**(防"幽灵漏加")| 见下方"已知隐患"详述 | 当前靠对账纪律兜底,可缓行 |
| 4 | **修复 7 个 stale tests** | — | 2026-06-09 跑全量发现:5 个 SAP 测试(`test_sap_stock.py`)与 commit `05164eb` 后的"分红复投排除成本基准"实际行为不一致;2 个矩阵阈值测试(`test_market_monitor.py::test_sp500_watch_high_pe_mid_vix` / `test_ndx100_watch`)与现行观望/暂停阈值不一致。本次 mcp_server bug 修复无关,但应尽快修齐让 suite 100% 绿 |
| 5 | **Dashboard 首次加载慢优化（tabs → 条件渲染）** | 见下方"已知隐患"详述 | 当前 10+ 秒首次加载，每次切换正常；评估后决定暂不实施 |
| 6 | ~~净资产核对公式：区分经营性 vs 资本性现金流~~ | ✅ 2026-07-01 完成 | ~~当前公式把卖车¥207k算入预测，导致残差异常虚低~~ | 

### P3 — 需前置数据积累

| # | 功能 | 设计文档 | 前置条件 |
|---|------|---------|---------|
| 1 | **调仓决策质量复盘** | `DESIGN_ANALYTICS.md` | transaction.csv 积累 3-6 个月 |
| 2 | **FIFO 已实现盈亏** | — | transaction.csv 积累 3-6 个月 |
| 3 | **现金分红方案 C** | `DESIGN_DIVIDEND.md` | 见设计文档 |
| 4 | **Portfolio Tab 区间分析模式** | 见下方"待沉淀设计" | 数据积累 12 个月以上 + 用户有 3+ 次"想看历史区间但当前工具给不了"的实际场景 |

### 元层面待办(非紧急)

| # | 项 | 触发点 |
|---|----|-------|
| 1 | 能力圈定义填充 | 用户主动提议时(`memory/user_capability_circle.md` stub)|
| 2 | ~~Memory → GitHub 私有 repo 迁移~~ ✅ 2026-06-04 完成 | ~/.claude/projects/ 整体作为 git repo,4 项目共 23 memory,见 memory/project_memory_sync_plan.md |

---

## 📐 待沉淀设计（已讨论，暂不实施）

### Portfolio Tab 区间分析模式（2026-06-06 讨论，暂搁）

**触发原因：** 用户发现 Portfolio Tab 的 "🔧 筛选与导出" 控件名暗示作用于全部内容，但实际上日期范围/类别筛选只作用于折线图，KPI / 饼图 / HTML 报告均使用全量数据。

**已落地的临时措施（方向 A，2026-06-06）：**
- 控件名改为 "🔧 图表显示与导出"
- 加 caption 明确"日期范围仅作用于本页折线图"
- "资产类别"控件标记"（分类 NAV 折线图）"
- 语义层面统一，消除 UX 认知摩擦

**未来完整实现（方向 B，待数据积累后评估）：**

新增 "📊 区间分析模式" 开关。开启后所有展示按选定区间重算：

| 元素 | 当前行为 | 区间模式行为 |
|------|---------|-------------|
| 总资产 KPI | 最新值 | 区间末日值 |
| 单位净值 | 最新 | 区间末值 |
| 累计收益 | 全局收益 | 区间收益（区间末值 - 区间起始值 - 区间内入金） |
| 年化收益率 (TWR) | 全周期 | 区间 TWR（重跑 nav_engine 子区间） |
| XIRR | 全周期 | 区间 XIRR |
| 夏普 / 卡尔马 | 全周期 | 区间重算 |
| 最大回撤 | 全周期 | 区间内最大回撤 |
| 持仓数 | 当下 | 区间末日 |
| 资产配置饼图 | 最新截面 | 区间末日截面 |
| 持仓表格 | 最新 | 区间末日 |
| 盈亏分析 | 最新 | 区间末日（成本基准 = 区间起始日的成本） |
| HTML 报告 | 全量 | 按区间重算 |

**核心设计问题（讨论时已识别）：**

1. **KPI 语义切换**："KPI 始终最新" vs "KPI 跟随筛选" 用户预期不一致
2. **累计收益锚点**：区间起始日如何确定"当时的累计投入"？需追溯历史 NCF
3. **TWR/XIRR 子区间**：nav_engine 需要支持任意起止区间重算，而不是只算全周期
4. **截面 vs 区间的天然不匹配**：饼图、持仓表是截面性质，需要明确"用区间末日截面"而非"区间内某种聚合"
5. **HTML 报告**：是"区间报告"还是永远"完整组合快照"？

**为何当前不做：**

1. **数据量不够**：建仓 2 个月 8 个快照，区间筛选信号噪声比极低
2. **真实工作流低频**：日常 Weekly Update / 季报已覆盖主要查询需求
3. **现有替代方案**：Tab1 Section 5 的归因分析已支持 7 个时间窗口（1M/3M/6M/YTD/1Y/全部）
4. **工程量大**：涉及 nav_engine 子区间重算、HTML 模板改造、UX 模式切换

**重新启动条件（任意一个）：**
- 数据积累 12 个月以上
- 用户出现 3+ 次"想看历史区间但当前工具给不了"的真实场景
- 季度/年度复盘需要给配偶/家人展示某个特定时段的报告

**关联原则：** `feedback_mental_load_minimization.md` — 心智压力最小化原则。引入"区间模式"会增加"现在是全局还是区间"的认知负担，当前数据规模下不值得。

---

## 🐛 已知小问题（评估后暂不修复）

- **`fx_service.py` 无重试机制** — yfinance/frankfurter 有隐性 rate limit，激进 retry 易触发限流；失败时 UI 已有 warning
- **SAP Tab 默认价格硬编码** — `sap_price_cache.json` 存于 iCloud，缓存为空的场景在正常使用路径中不存在
- **`load_data()` 缓存散落** — `st.cache_data.clear()` 散落各处，现有 workaround 够用

## ⚠️ 隐患（已识别，待修复，对应 P2-#3）

### 短信解析的"幽灵漏加"陷阱

**问题描述：**
当短信解析后用户对"是否成功更新份额"产生认知偏差时，会发生数据双重错误且不易察觉。

**实际触发链（2026-05-22 真实案例）：**
1. 5/15 那周南方纳指 100 I（021000）实际有 3 笔确认（480 + 320 + 480 = 1280 元），但短信解析时漏掉一笔，transaction.csv 和 portfolio.csv 5/15 行都只记了 480+320=800 元 + 354 份
2. 5/22 那周收到 1 条 480 元定投短信，短信解析正常加了 +212 份
3. 用户 UI 上看到当前 Shares 数字"还是 5/15 旧值"（实际系统已加，但视觉感知滞后/被 update_template 缓存遮蔽），**误以为系统漏加**
4. 用户手动在表格里补加 +212 份
5. 结果：5/22 行 Shares 多加了 ~212 份（共 422 份增量）
6. **误打误撞地，5/22 多加的 212 份正好"修复"了 5/15 漏的份额**——APP 显示份额变成对的，但 NCF/transaction.csv 仍漏 480 元
7. 隐患埋伏：累计 NCF 比 transaction 总额少 480 元，影响成本基准、XIRR、盈亏率

**根本原因：**
- 短信应用后立即触发 `_refresh_prices=True` rerun，UI 反馈被自动刷新覆盖
- 没有"已更新份额: X → Y（+Z 份）" 的明显 diff 高亮
- 用户无法快速验证"系统加对了 vs 漏了"

**修复方向（待实施）：**
1. **应用后高亮 diff**：明确显示 `南方纳指: Shares 19296.82 → 19509.06 (+212.24 份)` 持续 5-10 秒
2. **应用后弹出确认**：列出本次更新的所有标的及份额变化，用户点击确认才进入下一步
3. **份额对账自检**：每次保存快照前，自动校验 `Σ(Shares 增量 × 净值) == Σ(NCF)`，不一致则警告
4. **transaction.csv 反向校验**：保存快照时检查 `Σtransaction == Σ NCF`，发现遗漏则提示

**临时缓解（已落地）：**
- CLAUDE.md 提示用户"短信解析应用后仔细检查每个标的的 Shares 增量是否符合预期"

**修复优先级：** 中 — 不会主动出错，但出错时影响成本基准准确性，且不易察觉（这次靠交叉对账 transaction.csv 才发现）

---

### Dashboard 首次加载慢（对应 P2-#5）

**现象：** 每次重新打开浏览器/重启 Streamlit 后，首屏加载需 10+ 秒；后续页面交互（点按钮、切 tab）速度正常（<1 秒）。

**根因分析（2026-06-30 排查）：**

1. **Streamlit 的 `with tab_xxx:` 是显示控制，不是执行控制**——9 个 tab 的 5508 行 Python 代码每次 rerun 都会完整执行
2. **首屏渲染累计 45 个 plotly 图表**——每个图需要：Python 侧 plotly 生成（20-100ms）+ JSON 序列化（10-50ms）+ WebSocket 传输 + 浏览器 plotly.js 渲染（50-200ms）。45 × ~200ms ≈ 9 秒
3. **后端 Python 执行其实很快**：实测端到端 <1 秒（imports 530ms + load_data 47ms + 各 tab 数据加载 <100ms）
4. **iCloud IO 不是瓶颈**：实测读 portfolio.csv 0.1ms、写 yf_symbols.json 2ms
5. **基本面 yfinance 拉取不是首屏瓶颈**：缓存命中 200ms，未命中也只在 Research tab 被访问时触发

**已排除的方向：**
- `@st.fragment` 不能解决首次加载——fragment 只优化"交互时的局部 rerun"，首次仍全量渲染
- 提高基本面缓存 TTL 不解决——基本面拉取不在首屏关键路径

**真正能解决的方案（评估后暂不实施）：**

方案A：放弃 `st.tabs`，改用 `st.radio` + `if active == "xxx":` 条件渲染
- 工程量：中（重构 9 个 tab 的外层包裹，逻辑零变化）
- 收益：首屏 10s → 2-3s（只渲染当前 tab 的图表）
- 风险：UI 从 tabs 变 radio，视觉降级

方案B：保留 `st.tabs` 视觉 + session_state 追踪当前 tab + 条件渲染
- 工程量：中（hack 写法）
- 收益：同 A
- 风险：依赖 Streamlit 内部行为，可能不稳定

方案C：用 `st.navigation` + `st.Page` 多页面架构（Streamlit 1.36+）
- 工程量：大（每个 tab 拆成独立脚本）
- 收益：天然按需加载
- 风险：tabs 变 sidebar navigation，UX 改变大

**为何暂不实施（2026-06-30 决定）：**
1. **后续交互正常**——只是首次打开慢，使用流畅度受影响有限
2. **使用频率低**——通常一天打开一次，10 秒等待可接受
3. **改造收益不足以匹配工程量+风险**——核心功能没问题，优化属于锦上添花
4. **当前组合管理优先级更高**——花同等时间在投研、复盘、决策上更有价值

**重新启动条件（任意一个）：**
- 浏览器关闭后频繁需要重新打开（一天多次）
- 加入新 tab 后图表总数超过 60 个，加载时间超过 20 秒
- 部署到家庭其他成员设备后，加载慢成为协作瓶颈

**关联原则：** `feedback_mental_load_minimization.md` M1 — 心智压力最小化原则。这是一个典型的"分析清楚但暂不动手"的决策——证据完整、方向清晰、但当前不值得投入工程。

---

### 净资产核对公式：经营性 vs 资本性区分（对应 P2-#6，2026-06-30 发现）

**起因：** Q2 现金流分析跑通后,Quarterly Tab 显示"预测净资产变化 ¥294,864",但实际 Q2 净资产变化只有 +¥102,987,残差 -¥191,877（-1.27%）。用户质疑："卖车这种把不动产置换成现金的方式，真的应该计入预测吗？"

**根因：** `cashflow_engine.compute_net_worth_reconciliation` 公式错误地把 `Capital_Inflow`（资本性流入,如卖X3 ¥207k）当成"创造净资产"计入预测。

**正确的逻辑：**

| Type 枚举 | 性质 | 对净资产影响 |
|---------|------|------------|
| `Inflow_Salary` | 经营性流入(工资) | **+** 影响 |
| `Inflow_Other` | 经营性流入(政府补贴/保险理赔等) | **+** 影响 |
| `Capital_Inflow` | 资本性流入(资产变现，如卖车) | **不影响**（一进一出） |
| `Capital_Outflow` | 资本性流出(大额资产购置) | **不影响**（一进一出） |
| `Outflow_Major` | 经营性流出(基金外大额支出) | **-** 影响 |

**当前 cashflow_log.csv 的数据问题：**

```
2026Q2,2026-04-15,207000,Capital_Inflow,X3卖出变现  ✅ 正确
2026Q2,2026-05-25,15000, Capital_Inflow,政府补贴   ❌ 错误，应为 Inflow_Other
```

**修复方案（3 项）：**

1. **`cashflow_engine.compute_net_worth_reconciliation`**：
   - 按 Type 字段区分经营性 vs 资本性
   - 只把经营性现金流（`Inflow_Salary` / `Inflow_Other` / `Outflow_Major`）计入预测
   - 资本性现金流（`Capital_Inflow` / `Capital_Outflow`）作为独立项展示但不计入预测
   - 返回字典里改为：`operating_inflow` / `operating_outflow` / `capital_inflow` / `capital_outflow`

2. **`cashflow_log.csv`**：把 2026Q2 政府补贴的 Type 从 `Capital_Inflow` 改为 `Inflow_Other`

3. **UI 显示（dashboard/app.py Quarterly Tab）**：
   - 净资产核对表把"特殊入金"拆成"经营性"和"资本性"两组
   - 资本性单独显示（如"卖X3 ¥207k 资产置换"），但不参与预测计算
   - 残差解读文案更新

**修复后的预期结果：**

```
修正前：
  预测 = 202,487 - 129,623 + 222,000 = 294,864
  实际 = 102,987
  残差 = -191,877 (-1.27%)  ← 包含错误的+¥207k资产置换

修正后：
  预测 = 202,487 - 129,623 + 15,000 = 87,864
  实际 = 102,987
  残差 = +15,123 (0.1%)  ← 真实反映金融资产估值波动（SAP跌+黄金跌等综合）
```

**残差从 -1.27% 收敛到 0.1%**——这才是真正合理的对账,证明公式修正方向正确。

**测试覆盖（修复时必须做）：**
- 单元测试覆盖 `Capital_Inflow` / `Inflow_Other` / `Outflow_Major` 三种 Type 的处理
- 集成测试用 Q2 真实数据验证残差 < 0.5%

**修复优先级：** 高 — 影响 Quarterly Tab 现金流分析的可信度。明天上手即可,工程量小（~30 分钟）。

---

## 设计决策记录

- **删除 Market Tab 个股基本面面板**（2026-05-28）：功能已被 Research Tab 芒格信号面板完全覆盖（4 区 2x2:估值/多维位置/质量信号/决策状态),保留 Market Tab 会造成"两个面板看同一个数据"的心智压力。删除后 Market Tab 纯粹化为"宽基 + 宏观"语义,Research Tab 是"个股研究"的唯一归属。`yf_symbols.json` 的 `show_fundamentals` 字段保留(Research Tab 也用)。删除 ~179 行代码。
- **Tab5 折叠重构**（2026-05-07）：个股基本面、AH溢价、DCA均用 `expander` 默认收起，解决页面过长问题。语义仍在 Market Monitor，但按需展开。
- **AH 溢价数据源选 yfinance 而非 akshare**（2026-05-07）：`stock_zh_ah_spot_em()` 依赖东方财富，公司网络下超时。yfinance 直接拉 `.SS`/`.HK` ticker 可用，历史分位数自建每日快照存入 `ah_config.json`。`run_backtest()` 加 `end_date` 参数用于分析特定市场周期（如排除黄金单边牛市）；加 `cash_rate_annual` 参数（默认2%）计算矩阵策略少投差额的货币基金复利，使固定 vs 矩阵在同等总预算下公平对比。综合价值 = 矩阵市值 + 货币基金余额。
- **DCA Manager 以周为最小颗粒度**（2026-05-06）：市场信号（PE/VIX）是低频信号，日内拆单抢 QDII 额度属于执行层细节，系统只管"本周投多少"，不管"哪天分几次执行"。
- **DCA 黄金克数取整：raw < min_unit 暂停**（2026-05-06）：`raw = base_amount_unit × multiplier`，未取整前已低于最小交易单位则建议暂停，不向上凑整。语义：信号强度不足以支撑最小交易单位时宁可不做。
- **SAP 个股基本面用 ADR（`SAP`）而非法兰克福（`SAP.DE`）**（2026-05-06）：基本面字段覆盖率更完整，PE/EPS/ROE 数据质量优先。股价显示 USD/ADR 与实际持仓 EUR/法兰克福不同币种，属已知不一致，可接受。SAP Stock Tab 的盈亏计算仍用 `SAP.DE` EUR 价格，两者互不干扰。
- **不做终值预测/复利曲线**（2026-05-02）：数字制造虚假确定性，锚定效应负面。替代方案：财务独立测算（倒推需要多少年）+ 储蓄率 + TWR vs 基准，关注可控的领先指标。
- **收益归因不深化**（2026-04-29）：后视镜效应，对被动指数为主的组合决策价值有限。现有 TWR 归因够用，不做 Brinson 配置/选择效应拆分。
- **不引入个股 PE/RSI**（2026-04-28）：同花顺已有，FamilyFund 的价值在组合层面而非个股层面。
- **不引入 DXY**（2026-04-29）：美元-黄金负相关在当前去美元化背景下周期不稳定，加入反而增加噪音。
- **贪恐指数/基金经理仓位不做**（2026-04-30）：CNN F&G 无官方 API；仓位数据滞后一季度。
- **黄金矩阵策略：固定定投更优**（2026-05-12，10年样本修正）：原始矩阵在10年视角下 XIRR 有正超额（+0.5%），但绝对盈亏接近零，性价比低。对冲矩阵（标普PE×VIX）在10年维度下明确失效（第四象限，绝对盈亏-200k）。综合两套矩阵，黄金固定定投仍是更简洁可靠的选择。详见 `DESIGN_BACKTEST.md` 第十六节。
- **标普500矩阵策略修正：20年视角有效**（2026-05-12）：早期结论"标普长期慢牛，矩阵系统性踏空"基于短期数据误判。20年（2005起）回测显示矩阵 XIRR +0.2%、绝对多赚150万，核心价值在于危机时顶格加仓（2008/2020），而非日常高PE减仓。详见 `DESIGN_BACKTEST.md` 第十六节。
