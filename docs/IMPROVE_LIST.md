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
| 个股基本面面板（持仓个股动态拉取，yfinance） | `src/fundamentals.py` Tab5 |
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

---

## 🔧 待实现功能

### P2 — 随时可做

| # | 功能 | 设计文档 | 前置条件 |
|---|------|---------|---------|
| 1 | **鲨鱼记账解析 + 季度现金流分析** | `DESIGN_CASHFLOW.md` | 2026Q2 数据 |
| 2 | **Weekly Update 自动化**（净值一键刷新 + 短信解析录入）| `DESIGN_AUTO_WEEKLY.md` | 无，立即可做 |
| 3 | **财务独立测算** | `DESIGN_ANALYTICS.md` | ✅ 已完成 |
| 4 | **储蓄率追踪** | `DESIGN_ANALYTICS.md` | ✅ 已完成 |
| 5 | **定投管理模块** | `DESIGN_DCA_MANAGER.md` | ✅ 已完成 |

### P3 — 需前置数据积累

| # | 功能 | 设计文档 | 前置条件 |
|---|------|---------|---------|
| 5 | **调仓决策质量复盘** | `DESIGN_ANALYTICS.md` | transaction.csv 积累 3-6 个月；天天基金接口已验证可用 |
| 6 | **人生阶段规划** | `DESIGN_LIFE_STAGES.md` | ✅ 已完成 |
| 7 | **FIFO 已实现盈亏** | — | transaction.csv 积累 3-6 个月 |
| 8 | **现金分红处理方案 C** | `DESIGN_DIVIDEND.md` | 见设计文档 |

---

## 🐛 已知小问题（评估后暂不修复）

- **`fx_service.py` 无重试机制** — yfinance/frankfurter 有隐性 rate limit，激进 retry 易触发限流；失败时 UI 已有 warning
- **SAP Tab 默认价格硬编码** — `sap_price_cache.json` 存于 iCloud，缓存为空的场景在正常使用路径中不存在
- **`load_data()` 缓存散落** — `st.cache_data.clear()` 散落各处，现有 workaround 够用

---

## 设计决策记录

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
