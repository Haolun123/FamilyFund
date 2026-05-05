# DESIGN: 分析层功能（Analytics）

> **状态**：待实现
> **日期**：2026-05-02

四个功能共同构成"领先指标仪表盘"，替代无意义的终值预测，关注可控的行为质量指标。

---

## 1. TWR vs 市场基准对比图

### 目标
在现有 NAV 折线图旁边，叠加主要市场基准的同期表现，直观判断是跑赢还是跑输市场。

### 基准选取
| 基准 | yfinance symbol | 代表含义 |
|------|----------------|---------|
| 沪深300 | `000300.SS` | A股基准 |
| 标普500 | `SPY` | 美股基准 |
| 纳指100 | `QQQ` | 成长股基准 |
| 黄金 | `GC=F` | 避险资产基准 |

用户可在 UI 勾选显示哪几条基准线（默认全显示）。

### 技术方案
- 基准起始日 = 建仓日（2026-04-10），以该日收盘价为100，归一化处理
- 组合 TWR 也以100为起点，同口径对比
- 用 yfinance 拉取历史价格，缓存到 `benchmark_cache.json`，TTL 1天
- 复用现有 NAV chart（Plotly），新增基准 trace

### 位置
Tab1 NAV 图区域，在现有折线图上叠加，或加一个 toggle 切换"含基准/不含基准"。

---

## 2. 财务独立测算

### 目标
回答一个问题：**按当前资产规模和储蓄速度，还需要多少年达到财务独立？**

不预测终值，而是倒推达到目标需要的时间。

### 输入参数（用户配置，存入 `fi_config.json`）
| 参数 | 说明 | 示例 |
|------|------|------|
| 年度生活支出目标 | 财务独立后每年需要多少钱 | ¥200,000 |
| 安全提款率 | 默认 4%（25x法则） | 4% |
| 预期年化收益率 | 保守估计 | 6% |
| 月储蓄额 | 每月新增投入 | ¥15,000 |

### 输出
- **目标资产规模** = 年支出 / 提款率（如 200万/4% = 500万）
- **当前进度** = 当前总资产 / 目标资产（进度条）
- **预计达标年份** = 基于复利公式反推（考虑当前资产 + 月储蓄的复利增长）
- **敏感性分析** = 收益率 ±1%、月储蓄 ±20% 对达标年份的影响（小表格）

### 技术方案
- 纯数学计算，无外部依赖
- 参数存入 `$FAMILYFUND_DATA/fi_config.json`，UI 可编辑
- 位置：Tab1 新增 Section，或独立放在 Dashboard 底部

---

## 3. 储蓄率追踪

### 目标
追踪每月实际储蓄率，判断财务纪律是否稳定。

### 定义
```
储蓄率 = 当月投入基金的净现金流 / 税后月收入
```

### 数据来源
- **投入金额**：`portfolio.csv` Cash 行的正 NCF（外部入金）
- **税后月收入**：用户手动配置（存入 `fi_config.json`，与财务独立测算共用）

### 注意
- ESPP/RSU 归属的 NCF 不计入"当月储蓄"——那是薪酬收入的另一种形式，不反映储蓄行为
- 只统计 Cash 行的外部入金

### 输出
- 月度储蓄率柱状图（过去12个月）
- 滚动平均储蓄率
- 与目标储蓄率对比（用户设定目标，存入 `fi_config.json`）

### 位置
与财务独立测算放在同一 Section，两者共用 `fi_config.json`。

---

## 4. 调仓决策质量复盘

### 目标
每次调仓后，在 T+30/T+90/T+180 天自动评估：这笔交易买对了吗？

### 前置条件
- transaction.csv 积累 3-6 个月（目前从 2026-04-17 开始）
- 国内公募基金净值来源：**天天基金非官方接口**（已验证可用）

### 数据来源
| 资产类型 | 价格来源 | 示例 |
|---------|---------|------|
| 国内公募基金 | 天天基金 `api.fund.eastmoney.com/f10/lsjz` | `018738` → 净值历史 |
| A股个股 | yfinance | `601838.SS` |
| 港股 | yfinance | `0700.HK` |
| 美股/ETF | yfinance | `SAP` |
| 黄金 | yfinance | `GC=F` |

Code → yf_symbol 的映射复用 `fundamentals.py` 的 `yf_symbols.json`。
国内基金 Code（如 `018738`）直接传给天天基金接口，无需映射。

### 评估逻辑
对 transaction.csv 中每笔买入/卖出，计算：
1. **绝对收益**：成交后 T+30/90/180 天，该资产涨跌幅
2. **相对收益**：同期对应基准（按 Asset_Class 匹配）的涨跌幅
3. **决策评分**：绝对收益 - 基准收益（正数 = 跑赢，负数 = 跑输）

Asset_Class → 基准映射：
| Asset_Class | 基准 |
|------------|------|
| US_Blend_Fund / US_Growth_Fund | SPY / QQQ |
| CN_Index_Fund / ETF_Stock | 000300.SS |
| Gold | GC=F |
| Fixed_Income | 无（固收不参与评估） |
| Company_Stock | SAP（直接评估） |

### 天天基金接口封装
```python
# src/fund_nav.py（新建）
def fetch_fund_nav(fund_code: str, start_date: str, end_date: str) -> dict:
    """拉取国内公募基金历史净值，返回 {date_str: nav_float}"""
    url = f'https://api.fund.eastmoney.com/f10/lsjz?fundCode={fund_code}&pageIndex=1&pageSize=500&startDate={start_date}&endDate={end_date}'
    # 带 Referer header，解析 LSJZList
```

### 输出展示
- 表格：每笔历史调仓 + T+30/90/180 绝对/相对收益
- 颜色标记：跑赢基准绿色，跑输红色
- 汇总统计：胜率、平均超额收益

### 位置
Tab2（Weekly Update）下方新增"Decision Review"折叠区，或独立 Tab。

---

## 共用配置文件

`$FAMILYFUND_DATA/fi_config.json`：
```json
{
  "monthly_income_cny": 30000,
  "monthly_savings_target_pct": 0.40,
  "annual_expense_target_cny": 200000,
  "withdrawal_rate": 0.04,
  "expected_annual_return": 0.06
}
```

---

## 实现顺序建议

1. **TWR vs 基准** — 最快，复用现有图表，立即有数据
2. **财务独立测算 + 储蓄率** — 纯计算，一起做，共用 `fi_config.json`
3. **调仓决策复盘** — 等 2026Q3/Q4 数据积累后再做，先把天天基金接口封装好
