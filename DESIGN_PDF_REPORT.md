# Design: PDF Report Generation

> 从 Dashboard 一键导出家庭基金 PDF 报告

## 1. Motivation

CIO 需要将基金状态导出为 PDF，用于：
- **定期归档**：每月/每季留存一份"快照报告"
- **家庭分享**：发给家庭成员查看（不需要打开 Dashboard）
- **离线审阅**：在没有电脑的场景下回顾基金表现

## 2. Report Content

PDF 报告复用 Dashboard Tab 1 (Dashboard) 的全部内容，分为 4 个板块：

### 2.1 Page 1 — 基金总览

| 区域 | 内容 | 数据源 |
|------|------|--------|
| 标题栏 | "FamilyFund 家庭基金报告" + 报告日期 | 最新快照日期 |
| KPI 卡片 | 总资产、单位净值、累计收益率、持仓数 | `compute_fund_nav()` |
| 净值走势图 | 基金整体 NAV 折线图（含 1.0 基准线） | `compute_fund_nav()` |

### 2.2 Page 2 — 资产类别对比

| 区域 | 内容 | 数据源 |
|------|------|--------|
| 分类净值对比图 | 多线折线图（每个 Asset_Class 一条线） | `compute_class_nav()` |
| 资产配置饼图 | 甜甜圈图（各类别占比 + 金额） | `compute_allocation()` |
| 分类业绩表 | 表格：资产类别 / 净值 / 收益率 / 市值 / 占比 | 合并上述两个数据源 |

### 2.3 Page 3 — 盈亏分析

| 区域 | 内容 | 数据源 |
|------|------|--------|
| 盈亏 KPI | 总成本、总市值、总盈亏、总收益率 | `compute_cost_basis()` |
| 盈亏柱状图 | 水平柱状图（绿盈红亏） | `compute_cost_basis()` |
| 盈亏明细表 | 表格：持仓 / 成本 / 市值 / 盈亏额 / 盈亏率 | `compute_cost_basis()` |

### 2.4 Page 4 — 持仓明细

| 区域 | 内容 | 数据源 |
|------|------|--------|
| 持仓全表 | 所有持仓的完整信息（类别/平台/名称/代码/份额/价格/市值） | `load_portfolio()` 最新日期 |

## 3. Tech Approach: matplotlib → PDF

### 3.1 Why Not Plotly?

Plotly 导出静态图片需要 `kaleido` 引擎（~100MB），且在 Docker Alpine 环境中安装复杂。matplotlib 已在依赖中，且 `nav_engine.py` 已有 matplotlib 绑定函数。

### 3.2 Why Not HTML → PDF (weasyprint/pdfkit)?

- `weasyprint` 需要系统级依赖（Cairo, Pango, GDK-Pixbuf）— Docker 镜像膨胀
- `pdfkit` 需要 `wkhtmltopdf` 二进制 — 同样的 Docker 问题
- 对于结构固定的报告，直接用 matplotlib 画布更可控

### 3.3 Chosen Approach: matplotlib `PdfPages`

matplotlib 内置 `PdfPages` 可以将多个 figure 写入一个 PDF 文件。**零额外依赖。**

```python
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages('report.pdf') as pdf:
    # Page 1: Fund Overview
    fig1 = create_overview_page(fund_nav_df)
    pdf.savefig(fig1)
    plt.close(fig1)
    
    # Page 2: Asset Class
    fig2 = create_class_page(class_nav_dict, allocation_df)
    pdf.savefig(fig2)
    plt.close(fig2)
    
    # ... more pages
```

**Advantages:**
- Zero new dependencies (matplotlib already installed)
- Works in Docker without extra system packages
- Full control over layout (axes positioning, font sizes, colors)
- Native PDF — no rasterization, text is selectable

### 3.4 Table Rendering

matplotlib's `ax.table()` can render DataFrames as formatted tables within a figure. For better control, use cell-by-cell text placement:

```python
def render_table(ax, df, col_widths):
    ax.axis('off')
    table = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        cellLoc='center',
        loc='center',
        colWidths=col_widths,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    # Style header row
    for j in range(len(df.columns)):
        table[0, j].set_facecolor('#2196F3')
        table[0, j].set_text_props(color='white', fontweight='bold')
```

### 3.5 KPI Cards

KPI cards rendered as styled text boxes using `ax.text()` with `bbox`:

```python
def render_kpi(ax, label, value, x, y):
    ax.text(x, y + 0.05, label, fontsize=9, color='#666',
            ha='center', transform=ax.transAxes)
    ax.text(x, y - 0.05, value, fontsize=16, fontweight='bold',
            ha='center', transform=ax.transAxes)
```

## 4. New Module: `src/pdf_report.py`

```
src/pdf_report.py
├── generate_report(df, output_path)     # Main entry point
├── _page_overview(pdf, fund_nav_df)     # Page 1
├── _page_class(pdf, class_nav_dict, allocation_df)  # Page 2
├── _page_pnl(pdf, cost_basis_df)        # Page 3
├── _page_holdings(pdf, df, date)        # Page 4
├── _render_kpi_row(ax, kpis)            # Helper: KPI cards
├── _render_table(ax, df, col_widths)    # Helper: DataFrame table
└── _style_chart(ax, title)              # Helper: consistent chart styling
```

### Function Signature

```python
def generate_report(csv_path=None, output_path=None, date=None):
    """Generate a PDF report for the family fund.
    
    Args:
        csv_path: Path to portfolio.csv (default: iCloud path)
        output_path: Where to save the PDF (default: output/report_YYYY-MM-DD.pdf)
        date: Report date (default: latest date in CSV)
    
    Returns:
        str: Path to the generated PDF file
    """
```

## 5. Dashboard Integration

Add a "Download PDF" button in the Dashboard tab sidebar:

```python
# In dashboard/app.py sidebar
if st.button("Download PDF Report"):
    from pdf_report import generate_report
    pdf_path = generate_report(csv_path=data_path)
    with open(pdf_path, 'rb') as f:
        st.download_button(
            label="Save PDF",
            data=f.read(),
            file_name=f"FamilyFund_{latest_date}.pdf",
            mime="application/pdf",
        )
```

This uses Streamlit's `st.download_button` — the PDF is generated server-side, then offered as a browser download. No file path exposure to the user.

## 6. Styling

| Element | Style |
|---------|-------|
| Page size | A4 landscape (297 × 210 mm) — wider for tables and charts |
| Font | Arial Unicode MS (already configured in matplotlib for CJK support) |
| Title | 14pt bold, dark blue (#1a237e) |
| Section headers | 11pt bold, medium blue (#1565c0) |
| KPI values | 16pt bold, dark text |
| KPI labels | 9pt, grey (#666) |
| Table header | White text on blue (#2196F3) background |
| Table rows | Alternating white / light grey (#f5f5f5) |
| Charts | Consistent with Dashboard color scheme |
| Footer | Page number + "Generated by FamilyFund" + timestamp |

## 7. Files Modified

| File | Changes |
|------|---------|
| `src/pdf_report.py` | **New** — PDF report generation module |
| `dashboard/app.py` | Add "Download PDF" button in sidebar |

### No New Dependencies

The entire implementation uses `matplotlib.backends.backend_pdf.PdfPages` which is part of matplotlib (already installed). No changes to `requirements.txt` or Docker image needed.

## 8. Example Output Structure

```
┌─────────────────────────────────────────────────────┐
│  Page 1: FamilyFund 家庭基金报告 — 2026-04-10       │
│                                                      │
│  [总资产: ¥3,500,000]  [净值: 1.0000]               │
│  [收益率: +0.00%]      [持仓: 25]                   │
│                                                      │
│  ┌──────────────────────────────────────┐            │
│  │  Fund NAV Trend (折线图)             │            │
│  │  ~~~~~~~~~/~~~~~~~~~~~~              │            │
│  │  --------1.0 baseline---             │            │
│  └──────────────────────────────────────┘            │
├─────────────────────────────────────────────────────┤
│  Page 2: 资产类别对比                                │
│                                                      │
│  ┌─────────────────┐  ┌─────────────────┐           │
│  │ Class NAV Lines  │  │ Allocation Pie  │           │
│  └─────────────────┘  └─────────────────┘           │
│                                                      │
│  ┌──────────────────────────────────────┐            │
│  │ 类别  │ 净值  │ 收益率 │ 市值  │ 占比 │           │
│  │ ───── │ ───── │ ────── │ ───── │ ──── │           │
│  │ ...   │ ...   │ ...    │ ...   │ ...  │           │
│  └──────────────────────────────────────┘            │
├─────────────────────────────────────────────────────┤
│  Page 3: 盈亏分析                                    │
│                                                      │
│  [总成本]  [总市值]  [总盈亏]  [收益率]              │
│                                                      │
│  ┌──────────────────────────────────────┐            │
│  │ P/L Bar Chart (green/red)            │            │
│  └──────────────────────────────────────┘            │
│                                                      │
│  ┌──────────────────────────────────────┐            │
│  │ 持仓 │ 成本  │ 市值  │ 盈亏  │ 盈亏率│           │
│  └──────────────────────────────────────┘            │
├─────────────────────────────────────────────────────┤
│  Page 4: 持仓明细                                    │
│                                                      │
│  ┌──────────────────────────────────────┐            │
│  │ Full holdings table (all columns)    │            │
│  └──────────────────────────────────────┘            │
│                                                      │
│              Page X of 4 · FamilyFund               │
└─────────────────────────────────────────────────────┘
```

---

*Created: 2026-04-10*
