# FamilyFund — CLAUDE.md

项目上下文和开发约定，帮助 Claude 在后续对话中快速理解背景、避免重踩已知的坑。

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
| 买入资产 | = +买入金额（正数） |
| 卖出/赎回资产 | = -到账金额（负数） |
| 外部入金/取出 | 记在 **Cash 行**，= +/- 金额 |
| SAP Own SAP 归属 | Company_Stock 行 = +Cost_CNY |
| SAP Move SAP 归属 | Company_Stock 行 = +归属市值 CNY |
| 无操作持仓 | = 0 |

买入/卖出 NCF 由调仓辅助器（⚖️ 调仓辅助器）自动写入，不需要手动填。

**旧约定（已废弃）**：内部调仓 NCF = 0，只有外部入金记 NCF。这是错的，会导致分类成本和 TWR 失真。

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
| `docs/ARCHITECTURE_shared.md` | 架构变更、新增模块、设计决策变化时更新；版本号递增 |
| `docs/USER_MANUAL.md` | 操作流程变更时更新（Weekly Update 步骤、调仓辅助器规则等） |

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

---

## 已知问题 / 待决策

- **Weekly Update：Total_Value 自动计算** — 建议方案 A（加"重算市值"按钮），待实现
- **`fx_service.py` 缺少重试机制** — 网络请求应加 retry，待实现
- **SAP Tab 默认价格硬编码** — 缓存为空时应提示手动输入，待改进

详见 `docs/IMPROVE_LIST.md`。
