"""
obsidian_sync.py
~~~~~~~~~~~~~~~~
Weekly Update 保存快照后，将 FamilyFund 数据同步到 Obsidian vault。

生成文件结构：
  vault/familyfund/
  ├── _dashboard.md              主看板（每次全量覆盖，含内联图表数据）
  ├── _decisions.md              个股决策一览（每次覆盖）
  ├── _notes.md                  用户自由笔记区（只建一次，不覆盖）
  ├── snapshots/
  │   └── YYYY-MM-DD.md          每周追加，全历史
  ├── holdings/
  │   └── <标的名>.md             每周全量覆盖
  └── son_fund/
      ├── _dashboard.md          儿子基金看板（每次覆盖）
      └── snapshots/
          └── YYYY-MM-DD.md      全历史
"""

import os
import json
import re
from typing import Optional, Dict

import pandas as pd


VAULT_SUBDIR = "familyfund"

# 类别中文显示名
CLASS_DISPLAY = {
    'Fixed_Income':    '固定收益',
    'Company_Stock':   '公司股票',
    'Gold':            '黄金',
    'Individual_Stock':'个股',
    'Smart_Beta':      'Smart Beta',
    'US_Growth_Fund':  '美股成长',
    'US_Blend_Fund':   '美股宽基',
    'CN_Index_Fund':   'A股指数',
    'Cash':            '现金',
}

# 不参与分类业绩/配置图的类别
EXCLUDE_FROM_CLASS = {'Cash'}


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def _fmt_yaml_str(val) -> str:
    if val is None:
        return 'null'
    s = str(val)
    if any(ord(c) > 127 for c in s) or ':' in s or '#' in s or s == '':
        return f'"{s}"'
    return s


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def _write_file_if_not_exists(path: str, content: str) -> None:
    if not os.path.exists(path):
        _write_file(path, content)


def _get_vault_path() -> Optional[str]:
    env = os.environ.get('OBSIDIAN_VAULT_PATH')
    if env:
        return env
    default = os.path.expanduser(
        '~/Library/Mobile Documents/iCloud~md~obsidian/Documents/vault'
    )
    if os.path.isdir(default):
        return default
    return None


def _parse_target_num(target_str: str) -> Optional[int]:
    if not target_str or target_str in ('0', '—', 'null', '观察', '当前不变'):
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*万', target_str)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r'(\d+)', target_str)
    if m:
        return int(m.group(1))
    return None


def _load_decisions(data_dir: str) -> dict:
    path = os.path.join(data_dir, 'Finance Reports', '_meta', 'decisions.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    return {k: v.get('current', {}) for k, v in raw.items()}


# ── 图表构建 ──────────────────────────────────────────────────────────────────

def _build_nav_line_chart(fund_nav_df: pd.DataFrame) -> str:
    """基金净值折线图（Obsidian Charts 格式）。"""
    df = fund_nav_df.copy()
    labels = [str(d)[:10] for d in df['Date'].tolist()]
    navs   = [round(float(v), 4) for v in df['NAV'].tolist()]

    labels_str = ', '.join(f'"{l}"' for l in labels)
    navs_str   = ', '.join(str(v) for v in navs)

    return f"""\
```chart
type: line
labels: [{labels_str}]
series:
  - title: 净值
    data: [{navs_str}]
tension: 0.3
width: 100%
labelColors: false
fill: false
beginAtZero: false
```"""


def _build_class_nav_chart(class_nav_dict: Dict[str, pd.DataFrame]) -> str:
    """分类净值多线对比图（Obsidian Charts 格式）。"""
    # 取所有类别的日期并集，以最新行对齐
    classes = [c for c in class_nav_dict if c not in EXCLUDE_FROM_CLASS]
    if not classes:
        return ''

    # 用第一个类别的日期序列作为 X 轴（所有类别日期相同）
    ref = class_nav_dict[classes[0]]
    labels = [str(d)[:10] for d in ref['Date'].tolist()]
    labels_str = ', '.join(f'"{l}"' for l in labels)

    series_lines = []
    for cls in classes:
        nav_df = class_nav_dict[cls]
        navs = [round(float(v), 4) for v in nav_df['NAV'].tolist()]
        navs_str = ', '.join(str(v) for v in navs)
        display = CLASS_DISPLAY.get(cls, cls)
        series_lines.append(f'  - title: {display}\n    data: [{navs_str}]')

    series_block = '\n'.join(series_lines)

    return f"""\
```chart
type: line
labels: [{labels_str}]
series:
{series_block}
tension: 0.3
width: 100%
labelColors: true
fill: false
beginAtZero: false
```"""


def _build_allocation_pie_chart(allocation_df: pd.DataFrame) -> str:
    """资产配置饼图（Obsidian Charts 格式）。"""
    df = allocation_df[~allocation_df['Asset_Class'].isin(EXCLUDE_FROM_CLASS)].copy()
    if df.empty:
        return ''

    mv_col = 'Total_Value' if 'Total_Value' in df.columns else 'Market_Value'
    pct_col = 'Allocation_Percent' if 'Allocation_Percent' in df.columns else None

    if pct_col:
        df['_pct'] = (df[pct_col].astype(float) * 100).round(1)
    else:
        total = df[mv_col].sum()
        df['_pct'] = (df[mv_col].astype(float) / total * 100).round(1) if total > 0 else 0

    df = df[df['_pct'] > 0]
    labels = [CLASS_DISPLAY.get(r['Asset_Class'], r['Asset_Class']) for _, r in df.iterrows()]
    values = [r['_pct'] for _, r in df.iterrows()]

    labels_str = ', '.join(f'"{l}"' for l in labels)
    values_str = ', '.join(str(v) for v in values)

    return f"""\
```chart
type: pie
labels: [{labels_str}]
series:
  - data: [{values_str}]
width: 70%
labelColors: true
```"""


# ── KPI 卡片区 ────────────────────────────────────────────────────────────────

def _build_kpi_section(
    fund_nav_df: pd.DataFrame,
    xirr: Optional[float],
    sharpe: Optional[float],
    calmar: Optional[float],
    total_invested: float,
) -> str:
    latest = fund_nav_df.iloc[-1]
    total_value = float(latest['Total_Value'])
    nav         = float(latest['NAV'])
    profit      = total_value - total_invested
    ret_pct     = (profit / total_invested * 100) if total_invested > 0 else 0.0

    ann = latest.get('Annualized_Return(%)')
    ann_str = f"{float(ann):+.2f}%" if (ann is not None and str(ann) != 'None') else "< 1年"

    mdd = latest.get('Max_Drawdown(%)')
    mdd_str = f"{float(mdd):.2f}%" if (mdd is not None and str(mdd) != 'None') else "—"

    xirr_str   = f"{xirr*100:+.2f}%"   if xirr   is not None else "< 1年"
    sharpe_str = f"{sharpe:.2f}"         if sharpe  is not None else "< 1年"
    calmar_str = f"{calmar:.2f}"         if calmar  is not None else "< 1年"

    return f"""\
## 📊 基金总览

| 指标 | 数值 |
|------|------|
| 总资产 | **¥{total_value:,.0f}** |
| 单位净值 | **{nav:.4f}** |
| 累计收益 | **¥{profit:+,.0f}**（{ret_pct:+.2f}%）|
| 年化收益(TWR) | {ann_str} |
| XIRR(MWR) | {xirr_str} |
| 夏普比率 | {sharpe_str} |
| 卡尔马比率 | {calmar_str} |
| 最大回撤 | {mdd_str} |
"""


# ── 分类业绩表 ────────────────────────────────────────────────────────────────

def _build_class_perf_table(
    class_nav_dict: Dict[str, pd.DataFrame],
    allocation_df: pd.DataFrame,
    cost_basis_df: pd.DataFrame,
) -> str:
    mv_col  = 'Total_Value' if 'Total_Value' in allocation_df.columns else 'Market_Value'
    pct_col = 'Allocation_Percent' if 'Allocation_Percent' in allocation_df.columns else None

    # 按类别汇总盈亏
    cls_pnl = {}
    if cost_basis_df is not None and not cost_basis_df.empty:
        cb = cost_basis_df[cost_basis_df['Market_Value'] > 0].groupby('Asset_Class').agg(
            cost=('Cost_Basis', 'sum') if 'Cost_Basis' in cost_basis_df.columns else ('Cost', 'sum'),
            mv=('Market_Value', 'sum')
        )
        for cls, row in cb.iterrows():
            cls_pnl[cls] = float(row['mv']) - float(row['cost'])

    rows = ['## 📋 分类业绩一览\n',
            '| 类别 | 净值 | 收益率 | 收益额 | 市值 | 占比 |',
            '|------|------|--------|--------|------|------|']

    classes = [c for c in class_nav_dict if c not in EXCLUDE_FROM_CLASS]
    for cls in classes:
        nav_df = class_nav_dict[cls]
        if nav_df.empty:
            continue
        latest = nav_df.iloc[-1]
        nav_val  = float(latest['NAV'])
        ret_val  = float(latest['Cumulative_Return(%)'])
        tv_val   = float(latest['Total_Value'])

        alloc_row = allocation_df[allocation_df['Asset_Class'] == cls]
        if pct_col and not alloc_row.empty:
            pct_val = float(alloc_row[pct_col].values[0]) * 100
        elif not alloc_row.empty:
            total_mv = allocation_df[mv_col].sum()
            pct_val  = float(alloc_row[mv_col].values[0]) / total_mv * 100 if total_mv > 0 else 0
        else:
            pct_val = 0.0

        pnl = cls_pnl.get(cls)
        pnl_str = f"¥{pnl:+,.0f}" if pnl is not None else '—'
        display = CLASS_DISPLAY.get(cls, cls)

        rows.append(
            f'| {display} | {nav_val:.4f} | {ret_val:+.2f}% | {pnl_str} '
            f'| ¥{tv_val:,.0f} | {pct_val:.1f}% |'
        )

    return '\n'.join(rows) + '\n'


# ── 持仓明细表 ────────────────────────────────────────────────────────────────

def _build_holdings_table(cost_basis_df: pd.DataFrame) -> str:
    if cost_basis_df is None or cost_basis_df.empty:
        return ''

    df = cost_basis_df[
        (cost_basis_df['Asset_Class'] != 'Cash') &
        (cost_basis_df['Market_Value'] > 0)
    ].copy()
    if df.empty:
        return ''

    cost_col = 'Cost_Basis' if 'Cost_Basis' in df.columns else 'Cost'
    pnl_col  = 'Profit_Loss' if 'Profit_Loss' in df.columns else None
    pct_col  = 'Profit_Loss_Rate' if 'Profit_Loss_Rate' in df.columns else None
    name_col = 'Name' if 'Name' in df.columns else 'Asset'

    rows = ['## 📦 持仓明细\n',
            '| 标的 | 类别 | 成本 | 市值 | 盈亏 | 盈亏率 |',
            '|------|------|------|------|------|--------|']

    for _, row in df.iterrows():
        name  = str(row.get(name_col, ''))
        cls   = CLASS_DISPLAY.get(str(row.get('Asset_Class', '')), str(row.get('Asset_Class', '')))
        cost  = float(row[cost_col])
        mv    = float(row['Market_Value'])
        pnl   = float(row[pnl_col]) if pnl_col else (mv - cost)
        pct   = float(row[pct_col]) * 100 if pct_col else ((pnl / cost * 100) if cost > 0 else 0)
        sign  = '+' if pnl >= 0 else ''
        rows.append(
            f'| {name} | {cls} | ¥{cost:,.0f} | ¥{mv:,.0f} '
            f'| {sign}¥{pnl:,.0f} | {pct:+.1f}% |'
        )

    return '\n'.join(rows) + '\n'


# ── 主 dashboard 生成 ─────────────────────────────────────────────────────────

def _build_main_dashboard(
    date_str: str,
    fund_nav_df: pd.DataFrame,
    class_nav_dict: Dict[str, pd.DataFrame],
    allocation_df: pd.DataFrame,
    cost_basis_df: pd.DataFrame,
    xirr: Optional[float],
    sharpe: Optional[float],
    calmar: Optional[float],
    total_invested: float,
) -> str:
    sections = []

    # frontmatter
    sections.append(f"""\
---
tags: [familyfund, dashboard]
updated: {date_str}
---

# FamilyFund Portfolio
*最后更新：{date_str}*
""")

    # KPI
    sections.append(_build_kpi_section(fund_nav_df, xirr, sharpe, calmar, total_invested))

    # 净值折线图
    sections.append('## 📈 基金净值走势\n')
    sections.append(_build_nav_line_chart(fund_nav_df))
    sections.append('')

    # 配置饼图 + 分类多线
    sections.append('## 🥧 资产配置\n')
    sections.append(_build_allocation_pie_chart(allocation_df))
    sections.append('')

    sections.append('## 📉 分类净值对比\n')
    sections.append(_build_class_nav_chart(class_nav_dict))
    sections.append('')

    # 分类业绩表
    sections.append(_build_class_perf_table(class_nav_dict, allocation_df, cost_basis_df))

    # 持仓明细表
    sections.append(_build_holdings_table(cost_basis_df))

    # 历史净值表（Dataview，从 snapshots 读）
    sections.append("""\
## 🕐 历史快照

```dataview
TABLE
  date as "日期",
  total_assets as "总资产",
  nav as "净值",
  nav_change_wow as "周变动",
  xirr as "XIRR%"
FROM "familyfund/snapshots"
SORT date DESC
```
""")

    return '\n'.join(sections)


# ── decisions 文件 ────────────────────────────────────────────────────────────

def _build_decisions_file(decisions: dict, cost_basis_df: pd.DataFrame, date_str: str) -> str:
    # 当前持仓市值映射
    mv_map = {}
    if cost_basis_df is not None and not cost_basis_df.empty:
        name_col = 'Name' if 'Name' in cost_basis_df.columns else 'Asset'
        for _, row in cost_basis_df.iterrows():
            name = str(row.get(name_col, ''))
            code = str(row.get('Code', ''))
            mv   = float(row.get('Market_Value', 0))
            mv_map[name] = mv
            mv_map[code] = mv

    lines = [
        f"""\
---
tags: [familyfund, decisions]
updated: {date_str}
---

# 个股决策一览
*最后更新：{date_str}*

| 标的 | 层级 | 决策 | 风格 | 当前市值 | 目标仓位 | 缺口 | 加仓触发 | 减仓触发 |
|------|------|------|------|---------|---------|------|---------|---------|"""
    ]

    # 只展示有 tier 的（个股池标的）
    pool = {k: v for k, v in decisions.items()
            if v.get('tier') in ('核心', '卫星', '观察→?')}

    for folder, dec in sorted(pool.items(), key=lambda x: (x[1].get('tier',''), x[0])):
        name = folder.split('（')[0]
        tier   = dec.get('tier', '—')
        action = dec.get('action', '—')
        style  = dec.get('style', '—')
        target = dec.get('target_position', '—')
        add_t  = dec.get('add_trigger', '—')
        trim_t = dec.get('trim_trigger', '—')

        # 查当前市值
        cur_mv = mv_map.get(name, 0)
        mv_str = f"¥{int(cur_mv):,}" if cur_mv > 0 else '未建仓'

        # 计算缺口
        target_num = _parse_target_num(target)
        if target_num and cur_mv > 0:
            gap = target_num - int(cur_mv)
            gap_str = f"¥{gap:+,}"
        elif target_num and cur_mv == 0:
            gap_str = f"¥{target_num:,}（待建）"
        else:
            gap_str = '—'

        # 截断过长文字
        add_t  = add_t[:30]  + '…' if len(add_t)  > 30 else add_t
        trim_t = trim_t[:30] + '…' if len(trim_t) > 30 else trim_t

        lines.append(
            f'| {name} | {tier} | {action} | {style} | {mv_str} '
            f'| {target} | {gap_str} | {add_t} | {trim_t} |'
        )

    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('> 点击 holdings/ 下各标的文件查看完整决策与研报链接')
    lines.append('')

    return '\n'.join(lines)


# ── 快照 frontmatter ──────────────────────────────────────────────────────────

def _build_snapshot_frontmatter(
    date_str: str,
    fund_nav_df: pd.DataFrame,
    allocation_df: pd.DataFrame,
    xirr: Optional[float],
    max_drawdown: Optional[float],
    cash: float,
    weekly_dca: Optional[float],
) -> str:
    latest = fund_nav_df.iloc[-1] if not fund_nav_df.empty else None
    prev   = fund_nav_df.iloc[-2] if len(fund_nav_df) >= 2 else None

    nav          = round(float(latest['NAV']), 4)         if latest is not None else 'null'
    total_assets = int(float(latest['Total_Value']))      if latest is not None else 'null'

    nav_change = 'null'
    if latest is not None and prev is not None:
        delta = float(latest['NAV']) - float(prev['NAV'])
        nav_change = f"{delta:+.4f}"

    alloc = {}
    if allocation_df is not None and not allocation_df.empty:
        mv_col  = 'Total_Value' if 'Total_Value' in allocation_df.columns else 'Market_Value'
        pct_col = 'Allocation_Percent' if 'Allocation_Percent' in allocation_df.columns else None
        if pct_col:
            for _, row in allocation_df.iterrows():
                alloc[row['Asset_Class']] = round(float(row[pct_col]) * 100, 1)
        else:
            total_mv = allocation_df[mv_col].sum()
            for _, row in allocation_df.iterrows():
                alloc[row['Asset_Class']] = round(float(row[mv_col]) / total_mv * 100, 1) if total_mv > 0 else 0

    def pct(cls): return alloc.get(cls, 0.0)

    xirr_val = round(xirr * 100, 2)          if xirr          is not None else 'null'
    mdd_val  = round(max_drawdown * 100, 2)  if max_drawdown  is not None else 'null'
    dca_val  = int(weekly_dca)               if weekly_dca    is not None else 'null'

    return '\n'.join([
        '---',
        f'date: {date_str}',
        f'total_assets: {total_assets}',
        f'nav: {nav}',
        f'nav_change_wow: "{nav_change}"',
        f'xirr: {xirr_val}',
        f'max_drawdown: {mdd_val}',
        f'fixed_income_pct: {pct("Fixed_Income")}',
        f'company_stock_pct: {pct("Company_Stock")}',
        f'individual_stock_pct: {pct("Individual_Stock")}',
        f'smart_beta_pct: {pct("Smart_Beta")}',
        f'gold_pct: {pct("Gold")}',
        f'us_growth_pct: {pct("US_Growth_Fund")}',
        f'us_blend_pct: {pct("US_Blend_Fund")}',
        f'cn_index_pct: {pct("CN_Index_Fund")}',
        f'cash: {int(cash)}',
        f'weekly_dca: {dca_val}',
        'tags: [familyfund, snapshot]',
        '---',
        '',
        f'**{date_str}** | 总资产 ¥{total_assets:,} | 净值 {nav} | XIRR {xirr_val}%',
        '',
    ])


def _build_son_snapshot_frontmatter(
    date_str: str,
    son_nav_df: pd.DataFrame,
    xirr: Optional[float],
    cash: float,
) -> str:
    latest = son_nav_df.iloc[-1] if not son_nav_df.empty else None
    prev   = son_nav_df.iloc[-2] if len(son_nav_df) >= 2 else None

    nav          = round(float(latest['NAV']), 4)    if latest is not None else 'null'
    total_assets = int(float(latest['Total_Value'])) if latest is not None else 'null'

    nav_change = 'null'
    if latest is not None and prev is not None:
        delta = float(latest['NAV']) - float(prev['NAV'])
        nav_change = f"{delta:+.4f}"

    xirr_val = round(xirr * 100, 2) if xirr is not None else 'null'

    return '\n'.join([
        '---',
        f'date: {date_str}',
        f'total_assets: {total_assets}',
        f'nav: {nav}',
        f'nav_change_wow: "{nav_change}"',
        f'xirr: {xirr_val}',
        f'cash: {int(cash)}',
        'tags: [familyfund, son-fund, snapshot]',
        '---',
        '',
        f'**{date_str} Son Fund** | 总资产 ¥{total_assets:,} | 净值 {nav}',
        '',
    ])


# ── Holdings 文件 ─────────────────────────────────────────────────────────────

def _build_holding_file(
    name: str, code: str, asset_class: str,
    cost: float, market_value: float, pnl: float, pnl_pct: float,
    decision: dict, date_str: str, target_num: Optional[int],
) -> str:
    tier       = _fmt_yaml_str(decision.get('tier', ''))
    style      = _fmt_yaml_str(decision.get('style', ''))
    action     = _fmt_yaml_str(decision.get('action', ''))
    add_t      = _fmt_yaml_str(decision.get('add_trigger', ''))
    trim_t     = _fmt_yaml_str(decision.get('trim_trigger', ''))
    target_pos = _fmt_yaml_str(decision.get('target_position', ''))
    source_doc = decision.get('source_doc', '')

    gap = (target_num - int(market_value)) if target_num is not None else 'null'

    lines = [
        '---',
        f'name: {_fmt_yaml_str(name)}',
        f'code: {code}',
        f'class: {asset_class}',
        f'tier: {tier}',
        f'style: {style}',
        f'cost: {int(cost)}',
        f'market_value: {int(market_value)}',
        f'pnl: {int(pnl)}',
        f'pnl_pct: {round(pnl_pct, 2)}',
        f'action: {action}',
        f'target_position: {target_pos}',
        f'gap: {gap}',
        f'add_trigger: {add_t}',
        f'trim_trigger: {trim_t}',
        f'date: {date_str}',
        'tags: [familyfund, holding]',
        '---',
        '',
        f'## {name}',
        '',
        f'**决策**：{decision.get("action","—")}　**层级**：{decision.get("tier","—")}　**风格**：{decision.get("style","—")}',
        '',
        f'**摘要**：{decision.get("summary","—")}',
        '',
        '| 指标 | 数值 |',
        '|------|------|',
        f'| 成本 | ¥{int(cost):,} |',
        f'| 市值 | ¥{int(market_value):,} |',
        f'| 盈亏 | ¥{int(pnl):,}（{pnl_pct:+.1f}%）|',
        f'| 目标仓位 | {decision.get("target_position","—")} |',
        '',
        f'**加仓触发**：{decision.get("add_trigger","—")}',
        '',
        f'**减仓触发**：{decision.get("trim_trigger","—")}',
        '',
    ]
    if source_doc:
        lines.append(f'**研报**：[[{source_doc.replace(".md","")}]]')
        lines.append('')
    return '\n'.join(lines)


def _build_son_holding_file(
    asset_class: str, cost: float, market_value: float,
    pnl: float, pnl_pct: float, date_str: str,
) -> str:
    return '\n'.join([
        '---',
        f'asset_class: {asset_class}',
        f'cost: {int(cost)}',
        f'market_value: {int(market_value)}',
        f'pnl: {int(pnl)}',
        f'pnl_pct: {round(pnl_pct, 2)}',
        f'date: {date_str}',
        'tags: [familyfund, son-fund, holding]',
        '---',
        '',
        f'## {asset_class}',
        '',
        f'成本 ¥{int(cost):,} | 市值 ¥{int(market_value):,} | 盈亏 ¥{int(pnl):,}（{pnl_pct:+.1f}%）',
        '',
    ])


# ── Son Fund dashboard ────────────────────────────────────────────────────────

def _build_son_dashboard(
    date_str: str,
    son_nav_df: pd.DataFrame,
    son_cost_df: Optional[pd.DataFrame],
    son_xirr: Optional[float],
) -> str:
    latest = son_nav_df.iloc[-1]
    nav    = float(latest['NAV'])
    total  = float(latest['Total_Value'])
    xirr_str = f"{son_xirr*100:+.2f}%" if son_xirr is not None else "< 1年"

    sections = [
        f"""\
---
tags: [familyfund, son-fund, dashboard]
updated: {date_str}
---

# Son Fund — 教育/安家基金
*最后更新：{date_str}*

| 指标 | 数值 |
|------|------|
| 总资产 | **¥{total:,.0f}** |
| 单位净值 | **{nav:.4f}** |
| XIRR | {xirr_str} |

## 📈 净值走势
"""
    ]
    sections.append(_build_nav_line_chart(son_nav_df))
    sections.append('')

    if son_cost_df is not None and not son_cost_df.empty:
        sections.append('## 📦 持仓明细\n')
        sections.append('| 类别 | 成本 | 市值 | 盈亏 | 盈亏率 |')
        sections.append('|------|------|------|------|--------|')
        cost_col = 'Cost_Basis' if 'Cost_Basis' in son_cost_df.columns else 'Cost'
        for _, row in son_cost_df[son_cost_df['Market_Value'] > 0].iterrows():
            cls = CLASS_DISPLAY.get(str(row['Asset_Class']), str(row['Asset_Class']))
            c   = float(row[cost_col])
            mv  = float(row['Market_Value'])
            p   = mv - c
            pp  = (p / c * 100) if c > 0 else 0
            sections.append(f'| {cls} | ¥{c:,.0f} | ¥{mv:,.0f} | ¥{p:+,.0f} | {pp:+.1f}% |')
        sections.append('')

    sections.append("""\
## 🕐 历史快照

```dataview
TABLE date, total_assets as "总资产", nav as "净值", nav_change_wow as "周变动"
FROM "familyfund/son_fund/snapshots"
SORT date DESC
```
""")
    return '\n'.join(sections)


# ── 主入口 ─────────────────────────────────────────────────────────────────────

def sync_to_obsidian(
    date_str: str,
    data_dir: str,
    fund_nav_df: pd.DataFrame,
    allocation_df: pd.DataFrame,
    cost_basis_df: pd.DataFrame,
    xirr: Optional[float],
    max_drawdown: Optional[float],
    weekly_dca: Optional[float] = None,
    class_nav_dict: Optional[Dict[str, pd.DataFrame]] = None,
    sharpe: Optional[float] = None,
    calmar: Optional[float] = None,
    total_invested: Optional[float] = None,
    son_nav_df: Optional[pd.DataFrame] = None,
    son_cost_df: Optional[pd.DataFrame] = None,
    son_xirr: Optional[float] = None,
    vault_path: Optional[str] = None,
) -> dict:
    """将 FamilyFund 数据同步到 Obsidian vault。"""
    if vault_path is None:
        vault_path = _get_vault_path()
    if vault_path is None:
        return {'ok': False, 'vault_dir': None, 'files_written': 0,
                'error': 'Obsidian vault 路径未找到，请设置 OBSIDIAN_VAULT_PATH 环境变量'}

    ff_dir = os.path.join(vault_path, VAULT_SUBDIR)
    files_written = 0

    try:
        # cash
        cash = 0.0
        if cost_basis_df is not None and not cost_basis_df.empty:
            cr = cost_basis_df[cost_basis_df['Asset_Class'] == 'Cash']
            if not cr.empty:
                cash = float(cr['Market_Value'].sum())

        # total_invested 估算（若未传入）
        if total_invested is None:
            total_invested = float(fund_nav_df.iloc[0]['Total_Value']) if not fund_nav_df.empty else 0.0

        # ── 1. 主 dashboard（每次全量覆盖）─────────────────────────────────
        _cn_dict = class_nav_dict or {}
        dash_content = _build_main_dashboard(
            date_str, fund_nav_df, _cn_dict, allocation_df, cost_basis_df,
            xirr, sharpe, calmar, total_invested,
        )
        _write_file(os.path.join(ff_dir, '_dashboard.md'), dash_content)
        files_written += 1

        # ── 2. 决策一览（每次覆盖）──────────────────────────────────────────
        decisions = _load_decisions(data_dir)
        dec_content = _build_decisions_file(decisions, cost_basis_df, date_str)
        _write_file(os.path.join(ff_dir, '_decisions.md'), dec_content)
        files_written += 1

        # ── 3. 用户笔记（只建一次）──────────────────────────────────────────
        _write_file_if_not_exists(
            os.path.join(ff_dir, '_notes.md'),
            f'---\ntags: [familyfund, notes]\n---\n\n# 投资笔记\n\n> 此文件不会被自动覆盖，可自由记录。\n'
        )

        # ── 4. 快照（全历史追加）────────────────────────────────────────────
        snap_dir = os.path.join(ff_dir, 'snapshots')
        snap_content = _build_snapshot_frontmatter(
            date_str, fund_nav_df, allocation_df, xirr, max_drawdown, cash, weekly_dca
        )
        _write_file(os.path.join(snap_dir, f'{date_str}.md'), snap_content)
        files_written += 1

        # ── 5. Holdings（每周覆盖）──────────────────────────────────────────
        holdings_dir = os.path.join(ff_dir, 'holdings')
        os.makedirs(holdings_dir, exist_ok=True)

        if cost_basis_df is not None and not cost_basis_df.empty:
            individual_classes = {'Individual_Stock', 'Company_Stock', 'Smart_Beta'}
            pl_df = cost_basis_df[
                (cost_basis_df['Asset_Class'].isin(individual_classes)) &
                (cost_basis_df['Market_Value'] > 0)
            ].copy()

            for _, row in pl_df.iterrows():
                code  = str(row.get('Code', ''))
                name  = str(row.get('Name') or row.get('Asset') or code)
                acls  = str(row.get('Asset_Class', ''))
                cost  = float(row.get('Cost_Basis') or row.get('Cost') or 0)
                mv    = float(row.get('Market_Value', 0))
                pnl   = float(row.get('Profit_Loss') or (mv - cost))
                pnl_pct = float(row.get('Profit_Loss_Rate') or
                                ((pnl / cost * 100) if cost > 0 else 0.0))

                dec = {}
                for k, v in decisions.items():
                    if code and code in k:
                        dec = v; break
                if not dec:
                    for k, v in decisions.items():
                        if name and name in k:
                            dec = v; break

                target_num = _parse_target_num(dec.get('target_position', ''))
                content = _build_holding_file(
                    name, code, acls, cost, mv, pnl, pnl_pct, dec, date_str, target_num
                )
                _write_file(os.path.join(holdings_dir, _safe_filename(name) + '.md'), content)
                files_written += 1

        # ── 6. 儿子基金（可选）──────────────────────────────────────────────
        if son_nav_df is not None and not son_nav_df.empty:
            son_dir = os.path.join(ff_dir, 'son_fund')

            # son dashboard（每次覆盖）
            son_dash = _build_son_dashboard(date_str, son_nav_df, son_cost_df, son_xirr)
            _write_file(os.path.join(son_dir, '_dashboard.md'), son_dash)
            files_written += 1

            # son snapshot
            son_cash = 0.0
            if son_cost_df is not None and not son_cost_df.empty:
                scr = son_cost_df[son_cost_df['Asset_Class'] == 'Cash']
                if not scr.empty:
                    son_cash = float(scr['Market_Value'].sum())

            son_snap = _build_son_snapshot_frontmatter(date_str, son_nav_df, son_xirr, son_cash)
            _write_file(os.path.join(son_dir, 'snapshots', f'{date_str}.md'), son_snap)
            files_written += 1

            # son holdings
            if son_cost_df is not None and not son_cost_df.empty:
                son_pl = son_cost_df[
                    (son_cost_df['Asset_Class'] != 'Cash') &
                    (son_cost_df['Market_Value'] > 0)
                ].copy()
                cost_col = 'Cost_Basis' if 'Cost_Basis' in son_pl.columns else 'Cost'
                son_pl = son_pl.rename(columns={cost_col: '_cost'})
                son_agg = son_pl.groupby('Asset_Class').agg(
                    Cost=('_cost', 'sum'), Market_Value=('Market_Value', 'sum')
                ).reset_index()
                son_hdir = os.path.join(son_dir, 'holdings')
                os.makedirs(son_hdir, exist_ok=True)
                for _, row in son_agg.iterrows():
                    cls = str(row['Asset_Class'])
                    c   = float(row['Cost'])
                    mv  = float(row['Market_Value'])
                    p   = mv - c
                    pp  = (p / c * 100) if c > 0 else 0.0
                    content = _build_son_holding_file(cls, c, mv, p, pp, date_str)
                    _write_file(os.path.join(son_hdir, _safe_filename(cls) + '.md'), content)
                    files_written += 1

        return {'ok': True, 'vault_dir': ff_dir, 'files_written': files_written, 'error': None}

    except Exception as e:
        import traceback
        return {'ok': False, 'vault_dir': ff_dir, 'files_written': files_written,
                'error': f"{e}\n{traceback.format_exc()}"}
