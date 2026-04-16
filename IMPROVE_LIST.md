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
| 市场温度计 + 定投矩阵 | `src/market_monitor.py` | Tab 5，PE×VIX/QVIX 矩阵 |
| 每日企业微信推送 | `src/notifier.py` + `scripts/daily_push.py` | EC2 cron，北京时间 8:30 |

---

### 🔧 待实现功能（按优先级排序）

#### **P1 — 低挂果，快速完成**

1. **卡尔马比率（Calmar Ratio）** — 年化收益 / 最大回撤，两个值都已具备，几行代码
2. **风险集中度警示** — 单持仓或单资产类别占总资产比例超阈值时高亮（如 Company_Stock > 30%）
3. **货币敞口可视化** — 按 CNY/USD/EUR 汇总持仓市值，显示外汇风险分布

#### **P2 — 中等复杂度**

4. **资金效率分析** — 每笔 NCF（外部资金流入）对应的实际回报，回答"哪些时点买入决策好/差"
5. **持仓回测** — 模拟定投策略（固定倍数 vs PE×VIX 矩阵倍数），验证矩阵有效性；基于 yfinance/akshare 历史行情

#### **P3 — 高复杂度 / 需前置工作**

6. **季度财报（Tab 6）** — 资产负债表 + 净资产瀑布图；前置：迁移 25Q4+26Q1 历史 xlsx 数据
7. **收益归因分析** — 各资产类别对总收益的贡献度分解
8. **再平衡建议** — 基于目标配置比例，自动计算各类别买入/卖出金额

---

### 🐛 小问题/代码改进建议

- **`fx_service.py` 没有重试机制** — 网络请求应加 retry（`requests.adapters.HTTPAdapter` + `urllib3.util.retry.Retry`）
- **SAP Tab 的默认价格硬编码为 170.0 / 8.0** — 如果缓存为空，应该提示用户手动输入而不是给一个可能过时的默认值
- **`load_data()` 缓存问题** — `@st.cache_data` 按 `csv_path` 缓存，但如果文件内容变了（比如用户在 History tab 编辑后），需要手动 `st.cache_data.clear()`（已做，但散落在多处，容易遗漏）
- **缺少 `.streamlit/config.toml`** — Git 仓库里没有这个文件，Docker 构建时 `COPY .streamlit/ .streamlit/` 可能会失败