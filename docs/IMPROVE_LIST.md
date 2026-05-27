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

### P3 — 需前置数据积累

| # | 功能 | 设计文档 | 前置条件 |
|---|------|---------|---------|
| 1 | **调仓决策质量复盘** | `DESIGN_ANALYTICS.md` | transaction.csv 积累 3-6 个月 |
| 2 | **FIFO 已实现盈亏** | — | transaction.csv 积累 3-6 个月 |
| 3 | **现金分红方案 C** | `DESIGN_DIVIDEND.md` | 见设计文档 |

### 元层面待办(非紧急)

| # | 项 | 触发点 |
|---|----|-------|
| 1 | 能力圈定义填充 | 用户主动提议时(`memory/user_capability_circle.md` stub)|
| 2 | Memory → GitHub 私有 repo 迁移 | 2026 Q3-Q4 启动,公司 Mac 2026 年底换机前完成 |

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
- **黄金矩阵策略：固定定投更优**（2026-05-12，10年样本修正）：原始矩阵在10年视角下 XIRR 有正超额（+0.5%），但绝对盈亏接近零，性价比低。对冲矩阵（标普PE×VIX）在10年维度下明确失效（第四象限，绝对盈亏-200k）。综合两套矩阵，黄金固定定投仍是更简洁可靠的选择。详见 `DESIGN_BACKTEST.md` 第十六节。
- **标普500矩阵策略修正：20年视角有效**（2026-05-12）：早期结论"标普长期慢牛，矩阵系统性踏空"基于短期数据误判。20年（2005起）回测显示矩阵 XIRR +0.2%、绝对多赚150万，核心价值在于危机时顶格加仓（2008/2020），而非日常高PE减仓。详见 `DESIGN_BACKTEST.md` 第十六节。
