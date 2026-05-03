# FamilyFund 功能改进清单

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
| 个股基本面面板（成都银行/腾讯/SAP） | `src/fundamentals.py` Tab5 |
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
| 第十人系统 — 调仓前三 Agent 强制反对审查 | `src/tenth_man.py` Tab8，见 `DESIGN_TENTH_MAN.md` |

---

## 🔧 待实现功能

### P2 — 随时可做

| # | 功能 | 设计文档 | 前置条件 |
|---|------|---------|---------|
| 1 | **鲨鱼记账解析 + 季度现金流分析** | `DESIGN_CASHFLOW.md` | 2026Q2 数据（需先建「债务还本」分类） |

### P3 — 需前置数据积累

| # | 功能 | 前置条件 |
|---|------|---------|
| 3 | **调仓决策复盘** — 对比成交后后续1M/3M/6M收益 | transaction.csv 积累 3-6 个月 |
| 4 | **FIFO 已实现盈亏** — 先进先出批次成本匹配 | 同上 |
| 5 | **现金分红处理方案 C** — transaction.csv Dividend 类型 | 见 `DESIGN_DIVIDEND.md` |

---

## 🐛 已知小问题（评估后暂不修复）

- **`fx_service.py` 无重试机制** — yfinance/frankfurter 有隐性 rate limit，激进 retry 易触发限流；失败时 UI 已有 warning
- **SAP Tab 默认价格硬编码** — `sap_price_cache.json` 存于 iCloud，缓存为空的场景在正常使用路径中不存在
- **`load_data()` 缓存散落** — `st.cache_data.clear()` 散落各处，现有 workaround 够用

---

## 设计决策记录

- **收益归因不深化**（2026-04-29）：后视镜效应，对被动指数为主的组合决策价值有限。现有 TWR 归因够用，不做 Brinson 配置/选择效应拆分。
- **不引入个股 PE/RSI**（2026-04-28）：同花顺已有，FamilyFund 的价值在组合层面而非个股层面。
- **不引入 DXY**（2026-04-29）：美元-黄金负相关在当前去美元化背景下周期不稳定，加入反而增加噪音。
- **贪恐指数/基金经理仓位不做**（2026-04-30）：CNN F&G 无官方 API；仓位数据滞后一季度。
