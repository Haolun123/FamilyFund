# DESIGN: 鲨鱼记账解析 + 季度现金流分析（Cashflow）

> **状态**：待实现，前置条件未满足
> **日期**：2026-04-27

---

## 功能定位

解析鲨鱼记账导出 CSV，在季度财报 Tab7 展示消费结构、自由现金流、DSCR 等指标。

配合双轨制记账原则（详见 `docs/DESIGN_ACCOUNTING_PRINCIPLES.md`）：
- 鲨鱼记账「债务还本」分类的本金还款被剔除，还原净资产口径
- 保留完整现金流用于计算自由现金流和 DSCR

---

## 前置条件

- [ ] 鲨鱼记账 App 新建「债务还本」一级分类（从 2026Q2 起）
- [ ] 每月记录房贷本金、车贷本金（¥5,000）
- [ ] 每季末导出明细 CSV → `$FAMILYFUND_DATA/<季度>/鲨鱼记账明细.csv`

---

## 数据格式

文件编码：UTF-16，制表符分隔
列：`日期、收支类型、类别、金额、备注`

---

## 后端实现（`src/cashflow_engine.py`）

```python
def parse_shark_csv(path) -> pd.DataFrame:
    """读取 UTF-16 CSV，标准化日期格式"""

def compute_cashflow_summary(df) -> dict:
    """
    Returns:
      income_total:   总收入
      expense_total:  总支出（含债务还本）
      expense_net:    净支出（剔除债务还本）
      debt_service:   债务还本合计
      free_cashflow:  income - expense_total（真实可支配）
      net_savings:    income - expense_net（净资产视角）
      dscr:           income / debt_service
      by_category:    各类别支出明细
    """
```

---

## Dashboard 展示（季度财报 Tab7 新增 Section）

- **KPI 卡片**：季度收入 / 自由现金流 / 净储蓄率 / DSCR
- **支出结构饼图**：各类别占比（剔除「债务还本」）
- **现金流瀑布图**：收入 → 各类支出 → 债务还本 → 净储蓄
- **DSCR 警示**：< 1.5x 黄色，< 1.2x 红色

---

## 净资产核对公式

```
期末净资产 ≈ 期初净资产
           + 鲨鱼收入合计
           - 鲨鱼支出合计（剔除「债务还本」）
           ± 资产估值变化（balance_sheet QoQ）
```

误差 ¥5,000 以内属正常。

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `src/cashflow_engine.py` | 解析 + 汇总逻辑 |
| `dashboard/app.py` | Tab7 新增鲨鱼记账 Section |
| `$FAMILYFUND_DATA/<季度>/鲨鱼记账明细.csv` | 每季末手动放入 |
