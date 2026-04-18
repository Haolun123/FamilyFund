## 对 FamilyFund 当前功能的评价与改进建议

### 整体评价：⭐⭐⭐⭐ 非常扎实

项目的核心功能已经相当完善，代码质量高，架构清晰，文档详尽。作为一个个人/家庭使用的投资管理工具，已经完全可用。以下是具体评价和建议：

---

### ✅ 做得好的地方

1. **核心算法正确且优雅** — `_run_nav_calculation()` 单一函数复用于基金整体和每个资产类别，份额净值法实现准确
2. **数据安全** — `_atomic_write_csv()` 用文件锁 + 原子替换 + 自动备份（保留最近 30 份），防止写入中断导致数据损坏
3. **Dashboard 完整度高** — 5 个 Tab（总览/周更/历史/SAP/市场温度计）覆盖了日常所有操作场景
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

---

### 🔧 待实现功能（按优先级排序）

#### **P2 — 中等复杂度**

1. **资金效率分析** — 每笔 NCF（外部资金流入）对应的实际回报，回答"哪些时点买入决策好/差"
2. **持仓回测** — 模拟定投策略（固定倍数 vs PE×VIX 矩阵倍数），验证矩阵有效性；基于 yfinance/akshare 历史行情

#### **P3 — 高复杂度 / 需前置工作**

3. **季度财报（Tab 6）** — 资产负债表 + 净资产瀑布图；前置：迁移 25Q4+26Q1 历史 xlsx 数据
4. **收益归因分析** — 各资产类别对总收益的贡献度分解
5. **再平衡建议** — 基于目标配置比例，自动计算各类别买入/卖出金额

---

### 🐛 小问题/代码改进建议

- **`fx_service.py` 没有重试机制** — 网络请求应加 retry（`requests.adapters.HTTPAdapter` + `urllib3.util.retry.Retry`）
- **SAP Tab 的默认价格硬编码为 170.0 / 8.0** — 如果缓存为空，应该提示用户手动输入而不是给一个可能过时的默认值
- **`load_data()` 缓存问题** — `@st.cache_data` 按 `csv_path` 缓存，但如果文件内容变了（比如用户在 History tab 编辑后），需要手动 `st.cache_data.clear()`（已做，但散落在多处，容易遗漏）
- **缺少 `.streamlit/config.toml`** — Git 仓库里没有这个文件，Docker 构建时 `COPY .streamlit/ .streamlit/` 可能会失败
- **[待办] Weekly Update：Total_Value 自动计算** — 当前 `Total_Value` 需手动填写，与 `Shares × Current_Price × Exchange_Rate` 不联动。三种方案：
  - **方案 A（推荐）：增加"重算市值"按钮** — 点击后批量更新所有 `Shares > 0 AND Current_Price > 0` 的行，Cash 类跳过。约 20 行代码，保留手动覆盖能力，实现最简单。
  - **方案 B：提交前不一致警告** — 在提交按钮上方实时对比"系统计算值 vs 用户填值"，差异 > 1% 时高亮提示，用户选择采用哪个值。透明度更高，实现中等复杂度。
  - **方案 C：Total_Value 列只读，完全自动计算** — 从 `data_editor` 移除 `Total_Value` 列，改为 Python 层根据 `Shares × Price × Rate` 自动计算后展示；Cash 类 `Shares == Total_Value`。最彻底但需重构数据录入约定，破坏"直接填市值"的现有习惯，不推荐。

---

### 🔍 专项分析：分类净值/盈亏计算失真问题（已解决）

**日期**：2026-04-18  
**状态**：✅ 已解决

**解决方式**：
1. 重新定义 NCF 语义：调仓时买入方记正数 NCF、赎回方记负数 NCF，历史数据已补录
2. Cash 从分类对比图、饼图、业绩表、盈亏分析中全部排除，作为"流动性储备"仅在基金总览中体现
3. 分类 TWR 现在可以正确识别调仓资金流入，不再误判为市场涨幅

#### 两个表象，一个根因

**表象一：分类 NAV / 收益率虚高**

分类 NAV 图和收益率在调仓后出现系统性失真：
- 当周加仓的分类（如黄金、纳指）NAV 大幅跳升，因为 Total_Value 增加但 NCF = 0，TWR 算法误判为纯市场涨幅
- Cash 是调仓中转池，本身不增值，NAV 理论上应始终 ≈ 1.0，显示收益率无实际意义

**表象二：盈亏分析总成本虚高**

`compute_cost_basis` 用 `Cost_Basis = 历史所有 NCF 之和` 计算持仓成本，但 NCF 语义是"外部现金流"，不是"买入成本"：

| 持仓 | Cost_Basis 实际计算结果 | 应该是 |
|------|------|------|
| 基金/ETF/黄金 | 0（调仓 NCF 始终为 0） | 当时买入花了多少钱 |
| Cash | 所有历史外部入金累计（含已调仓出去的钱） | 当前现金余额 ≈ Total_Value |
| Company_Stock | SAP 交易记录覆盖（✅ 正确） | — |

结果：Cash 的 Cost_Basis 包含所有历史入金（如 ¥350 万），但 Cash 当前市值只是剩余现金（如 ¥10 万），导致 Cash 显示巨额"亏损"；其他资产 Cost_Basis = 0，显示全部市值为"盈利"；总成本数字虚高且无意义。

#### 根因

**NCF 语义设计与使用方不一致**：
- 数据录入约定：`NCF = 外部资金进出`（仅 Cash 行记录外部入金/出金，内部调仓 NCF = 0）
- 计算函数假设：`NCF = 每笔资产的买入/卖出成本`

两者完全不同。所有分类级计算（TWR、成本、P/L）都依赖 NCF，而 NCF 从未记录过调仓资金流向，导致分类层面的所有指标均失真。**基金整体层面不受影响**（整体 NCF = 外部入金，语义一致）。

#### 三种解决方案

**方案 X（数据层修改，彻底解决）：记录分类间转账 NCF**

调仓时为每个分类记录内部资金流：
- 黄金买入 ¥10000 → `Gold.NCF_internal = +10000`
- Cash 减出 ¥10000 → `Cash.NCF_internal = -10000`

分类 TWR 和成本计算均可准确还原。  
**代价**：需新增 `NCF_internal` 字段或重定义现有 NCF 语义；历史快照需补录（约 N 周 × M 笔调仓）；Weekly Update 录入流程增加"调仓来源/去向"填写步骤，复杂度明显上升。

**方案 Y（展示层简化，规避误导，推荐短期方案）：**

- 分类对比图：只展示市值走势，移除 NAV 净值线和收益率%列
- 盈亏分析：Cost_Basis 改为用"最早持仓日的 Total_Value"作为成本基准（近似值，不精确但不会虚高）；或直接隐藏总成本，只展示"当前市值分布"
- Cash 从盈亏分析中排除，单独作为"流动性储备"展示
- 收益率仅在基金整体层面展示（整体 TWR/XIRR 是准确的）

**代价**：损失分类级收益率信息；成本基准仍为近似值，非精确。实现简单，仅改展示逻辑。

**方案 Z（最简，不推荐）：保留现状 + 免责标注**

在分类 NAV 图和盈亏分析下方加注说明。信息质量低，长期造成决策误导。

#### 待决策

1. **短期**：是否先用方案 Y 修复展示层，避免当前虚高数字继续误导？
2. **长期**：是否接受在 Weekly Update 增加调仓资金流向记录，换取准确的分类收益率和成本（方案 X）？