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
| 累计收益金额 + 简单收益率 | `dashboard/app.py` Tab1 | KPI 显示绝对盈亏金额，delta 显示 (当前总资产-初始投入)/初始投入 |
| Cash 从分类对比/盈亏中排除 | `dashboard/app.py` Tab1 | Cash 仅在基金总览体现，不参与分类 NAV、饼图、盈亏分析 |
| NCF 全资产写入调仓辅助器 | `dashboard/app.py` Tab2 | 买入/卖出自动写对应资产行 NCF，支持新增标的追加行 |
| Docker 代码热重载 | `docker-compose.yml` | src/ 和 dashboard/ 挂载为 volume，代码改动 restart 即生效 |
| Weekly Update 重算市值 | `dashboard/app.py` Tab2 | 「🔄 重算市值」按钮，批量更新 Total_Value = Shares × Price × Rate，Cash 跳过，手动值可覆盖 |

---

### 🔧 待实现功能（按优先级排序）

#### **P2 — 中等复杂度**

1. **调仓决策复盘**（原"资金效率分析"）— 回答"这笔买入/卖出决策好不好"，通过对比成交后该资产的后续表现来评估。

   **依赖新文件 `transaction.csv`**，记录每笔实际成交明细：

   ```
   Date, Asset_Class, Platform, Name, Code, Type, Shares, Price, Amount_CNY, Fee_CNY
   ```

   - `transaction.csv` 由调仓辅助器在 Apply 时自动写入（一次 Apply 可生成多行）
   - 用户可选填"成交价"和"手续费"字段（调仓辅助器需新增这两个可选输入）；不填则按快照价格估算
   - 分析维度：按资产类别、买/卖方向，统计各笔交易的后续 1M/3M/6M 收益
   - **实施计划**：
     1. 调仓辅助器新增"成交价/手续费"字段
     2. Apply 时自动追加写入 `transaction.csv`
     3. 积累 3-6 个月数据后，再建复盘分析 UI
   - **当前状态**：`transaction.csv` 基础设施待建；数据积累阶段 P2，分析 UI 阶段 P3

2. **持仓回测** — 模拟定投策略（固定金额 vs PE×VIX / MA200×VIX 矩阵倍数），验证矩阵有效性；基于 yfinance/akshare 历史行情。
   - **A股（CSI300/A500）**：P2，akshare 有完整 PE + QVIX 历史数据
   - **黄金**：P2，使用 MA200 偏离度 × VIX 矩阵（`GOLD_BIAS_BANDS`/`GOLD_VIX_BANDS`/`GOLD_MATRIX` 已在 `market_monitor.py` 中定义），yfinance `GC=F` 价格历史充足
   - **美股（S&P500/NDX100）**：P3，待历史 PE 数据源确认后补充

#### **P3 — 高复杂度 / 需前置工作**

3. **季度财报（Tab 6）** — 资产负债表 + 净资产瀑布图；设计已完成（见 `docs/QUARTERLY_REPORT_DESIGN.md`）；前置：用户需先将 25Q4 + 26Q1 历史数据录入 `balance_sheet.csv` 和 `cashflow_log.csv`
4. **收益归因分析** — 各资产类别对总收益的贡献度分解
5. **再平衡建议** — 基于目标配置比例，自动计算各类别买入/卖出金额

---

### 数据文件分层说明

FamilyFund 的数据层分三个层次，各自服务不同的分析目的：

| 文件 | 频率 | 内容 | 支撑分析 |
|------|------|------|------|
| `portfolio.csv` | 周频快照 | 各持仓当期市值/份额/NCF | NAV 净值走势、P&L、基准对比 |
| `transaction.csv`（待建） | 每笔交易 | 单笔成交价/手续费/数量 | 调仓决策复盘（"这笔买卖决策好不好"） |
| `balance_sheet.csv` + `cashflow_log.csv`（待建） | 季频 | 家庭全量资产负债、外部现金流 | 季度家庭财报、净资产 QoQ |

三者互不覆盖，`portfolio.csv` 的投资类汇总会被季度财报引擎自动聚合进 `balance_sheet.csv` 对应行。

---

### 🐛 小问题/代码改进建议

- **`fx_service.py` 没有重试机制** — ~~网络请求应加 retry~~ 评估后不做：调用的是 frankfurter.app 和 yfinance，两者均有隐性 rate limit，激进 retry 反而容易触发限流；失败时 UI 已有 warning，影响可接受。
- **SAP Tab 的默认价格硬编码为 170.0 / 8.0** — ~~缓存为空时应提示用户手动输入~~ 评估后不做：`sap_price_cache.json` 存于 iCloud 同步目录，缓存为空的场景（新机器/手动删除）实际不存在于正常使用路径中。
- **`load_data()` 缓存问题** — `@st.cache_data` 按 `csv_path` 缓存，文件内容变了需手动 `st.cache_data.clear()`（已在各处添加，但散落多处容易遗漏）。非紧急，现有 workaround 够用。
- **~~[待办] Weekly Update：Total_Value 自动计算~~** ✅ 已实现（方案 A）— 在 data_editor 下方新增「🔄 重算市值」按钮，对所有 `Shares > 0 AND Current_Price > 0` 的非 Cash 行批量更新 `Total_Value = Shares × Price × Rate`，用户可在表格中直接覆盖。

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