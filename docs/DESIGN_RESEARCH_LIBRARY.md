# 研报库 Tab — 设计文档

> **版本**: v0.1
> **创建日期**: 2026-05-18
> **状态**: 设计阶段，待实现

---

## 一、定位

独立的第 6 个 Tab（暂名"研报库"），管理个股财报与分析文档。

### 两个入口

- **持仓 Tab 联动**：持仓行有对应研报时显示 `📄` 图标，点击跳转到研报库并定位该标的
- **直接访问**：独立浏览所有持仓中 + 观察中的标的

---

## 二、数据源

### 文件结构（iCloud）

```
$FINANCE_REPORTS_DIR/
├── 成都银行（601838.SS）/
│   ├── analysis/
│   │   └── 2026-05 芒格框架分析.md
│   └── reports/
│       ├── 2025年年度报告摘要.pdf
│       └── 2026年Q1报告.pdf
├── 思爱普（SAP）/
│   ├── analysis/
│   └── reports/
├── 腾讯控股（00700.HK）/
│   ├── analysis/
│   └── reports/
├── _watchlist/
│   ├── 阿里巴巴（09988.HK）/
│   ├── 农夫山泉（09633.HK）/
│   └── ...（共 9 个观察标的）
└── _meta/
    ├── ticker_map.json
    ├── 家庭基金投资框架：2026年5月版.md
    └── 财富、选择与投资的哲学框架.md
```

### ticker_map.json 结构

```json
{
  "持仓": {
    "成都银行（601838.SS）": {
      "portfolio_codes": ["601838.SS"],
      "yf_symbol": "601838.SS",
      "full_name": "成都银行股份有限公司"
    }
  },
  "观察": {
    "阿里巴巴（09988.HK）": {
      "portfolio_codes": [],
      "yf_symbol": "09988.HK",
      "full_name": "阿里巴巴集团控股有限公司"
    }
  }
}
```

`portfolio_codes`：用于从 `portfolio.csv` Code 字段反查文件夹（联动）。
`yf_symbol`：基本面分析用的 yfinance symbol。

---

## 三、布局

```
┌─ 左列（30%）──────────────┐  ┌─ 右列（70%）─────────────────────────┐
│ 🔴 持仓中                 │  │  成都银行（601838.SS）                 │
│   • 成都银行 ◀ (选中)     │  │  ──────────────────────────────────  │
│   • 思爱普               │  │  📄 分析文档                           │
│   • 腾讯控股              │  │    • 2026-05 芒格框架分析         ▶   │
│                           │  │                                      │
│ 👁 观察中                 │  │  📋 原始财报                           │
│   • 阿里巴巴              │  │    • 2025年年度报告摘要.pdf    ⬇       │
│   • 农夫山泉              │  │    • 2026年Q1报告.pdf          ⬇       │
│   • 招商银行              │  │                                      │
│   • ...                  │  │  ──────────────────────────────────  │
│                           │  │  [Markdown 全文渲染区]                │
└───────────────────────────┘  └──────────────────────────────────────┘
```

---

## 四、交互逻辑

1. **左列**：按 `ticker_map.json` 顺序，持仓在上、观察在下；点击标的名高亮并刷新右列
2. **右列 — 分析文档**：`analysis/` 下的 `.md` 文件，按文件名排序，点击展开正文（`st.markdown` 渲染）
3. **右列 — 原始财报**：`reports/` 下的 `.pdf` 文件，列出文件名 + `st.download_button` 下载
4. **右列 — 渲染区**：被选中的 `.md` 全文，支持表格、标题等 GitHub Flavored Markdown

---

## 五、持仓 Tab 联动

持仓 Tab 的资产行中，凡 `Code` 字段能在 `ticker_map.json` 的 `portfolio_codes` 中匹配，该行末尾显示 `📄` 按钮：

```python
st.session_state['research_target'] = folder_name
# 跳转到研报库 Tab 并定位该标的
```

---

## 六、新增模块

### `src/research_library.py`

| 函数 | 说明 |
|------|------|
| `load_ticker_map(reports_dir)` | 读取 `_meta/ticker_map.json` |
| `list_tickers(reports_dir)` | 返回 `{持仓: [...], 观察: [...]}` 文件夹名列表 |
| `list_ticker_files(reports_dir, folder_name)` | 返回 `{analysis: [...], reports: [...]}` |
| `read_analysis_md(reports_dir, folder_name, filename)` | 返回 md 文本 |

### 环境变量

`FINANCE_REPORTS_DIR`：指定 Finance Reports 目录路径。

若未设置，从 `FAMILYFUND_DATA` 推导：`os.path.join(os.path.dirname(FAMILYFUND_DATA), 'Finance Reports')`。

---

## 七、待决策

| 问题 | 选项 | 倾向 |
|------|------|------|
| PDF 查看方式 | 下载 vs iframe 内嵌预览 | 待定 |
| Tab 顺序 | 第 6 个（现有 5 个 Tab 之后） | 第 6 个 |
| `_meta` 框架文档 | 是否在研报库展示 | 待定，可作独立小节 |
