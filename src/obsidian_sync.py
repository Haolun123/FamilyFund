"""
obsidian_sync.py
~~~~~~~~~~~~~~~~
Weekly Update 保存快照后，将 FamilyFund 数据同步到 Obsidian vault。

生成文件结构：
  vault/familyfund/
  ├── _dashboard.md              主看板（Dataview 汇总页，首次生成后不覆盖）
  ├── snapshots/
  │   └── YYYY-MM-DD.md          每周追加，全历史
  ├── holdings/
  │   └── <标的名>.md             每周全量覆盖，只存最新持仓
  └── son_fund/
      ├── _dashboard.md          儿子基金独立看板（首次生成后不覆盖）
      └── snapshots/
          └── YYYY-MM-DD.md      儿子基金全历史
"""

import os
import json
import re
from datetime import datetime
from typing import Optional

import pandas as pd


# ── 常量 ──────────────────────────────────────────────────────────────────────

VAULT_SUBDIR = "familyfund"

# Dataview 看板模板（只在首次创建时写入，之后不覆盖用户自定义）
_MAIN_DASHBOARD_TEMPLATE = """\
---
tags: [familyfund, dashboard]
---

# FamilyFund 看板

## 本周快照

```dataview
TABLE
  total_assets as "总资产(¥)",
  nav as "净值",
  xirr as "XIRR%",
  max_drawdown as "最大回撤%",
  cash as "现金(¥)"
FROM "familyfund/snapshots"
SORT date DESC
LIMIT 1
```

## 净值历史

```dataview
TABLE
  date as "日期",
  total_assets as "总资产(¥)",
  nav as "净值",
  nav_change_wow as "周变动",
  xirr as "XIRR%",
  weekly_dca as "本周DCA(¥)"
FROM "familyfund/snapshots"
SORT date DESC
```

## 配置结构（最新）

```dataview
TABLE
  fixed_income_pct as "固收%",
  company_stock_pct as "公司股%",
  individual_stock_pct as "个股%",
  smart_beta_pct as "SmartBeta%",
  gold_pct as "黄金%",
  us_growth_pct as "美股成长%",
  us_blend_pct as "美股宽基%",
  cn_index_pct as "A股指数%"
FROM "familyfund/snapshots"
SORT date DESC
LIMIT 1
```

## 个股池全景

```dataview
TABLE
  tier as "层级",
  market_value as "市值(¥)",
  pnl_pct as "盈亏%",
  action as "决策",
  target_position as "目标仓位",
  gap as "缺口(¥)"
FROM "familyfund/holdings"
SORT tier ASC, market_value DESC
```

## 待建仓 / 补仓

```dataview
TABLE
  action as "决策",
  target_position as "目标",
  gap as "缺口(¥)",
  add_trigger as "加仓触发"
FROM "familyfund/holdings"
WHERE gap > 0
SORT gap DESC
```

## 持仓标的决策一览

```dataview
TABLE
  action as "决策",
  style as "风格",
  add_trigger as "加仓触发",
  trim_trigger as "减仓触发"
FROM "familyfund/holdings"
SORT tier ASC
```
"""

_SON_DASHBOARD_TEMPLATE = """\
---
tags: [familyfund, son-fund, dashboard]
---

# Son Fund 看板

## 本周快照

```dataview
TABLE
  total_assets as "总资产(¥)",
  nav as "净值",
  xirr as "XIRR%",
  cash as "现金(¥)"
FROM "familyfund/son_fund/snapshots"
SORT date DESC
LIMIT 1
```

## 净值历史

```dataview
TABLE
  date as "日期",
  total_assets as "总资产(¥)",
  nav as "净值",
  nav_change_wow as "周变动",
  xirr as "XIRR%"
FROM "familyfund/son_fund/snapshots"
SORT date DESC
```

## 持仓盈亏

```dataview
TABLE
  asset_class as "类别",
  cost as "成本(¥)",
  market_value as "市值(¥)",
  pnl as "盈亏(¥)",
  pnl_pct as "盈亏%"
FROM "familyfund/son_fund/holdings"
SORT market_value DESC
```
"""


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    """将标的名称转为安全文件名（去除特殊字符）。"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def _fmt_yaml_str(val) -> str:
    """将值格式化为 YAML 安全字符串（含中文时加引号）。"""
    if val is None:
        return 'null'
    s = str(val)
    # 含中文、冒号、特殊符号时加引号
    if any(ord(c) > 127 for c in s) or ':' in s or '#' in s or s == '':
        return f'"{s}"'
    return s


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def _write_file_if_not_exists(path: str, content: str) -> None:
    """只在文件不存在时写入（保护用户自定义内容）。"""
    if not os.path.exists(path):
        _write_file(path, content)


def _get_vault_path() -> Optional[str]:
    """从环境变量或默认路径推断 Obsidian vault 路径。"""
    env = os.environ.get('OBSIDIAN_VAULT_PATH')
    if env:
        return env
    default = os.path.expanduser(
        '~/Library/Mobile Documents/iCloud~md~obsidian/Documents/vault'
    )
    if os.path.isdir(default):
        return default
    return None


# ── 快照文件生成 ───────────────────────────────────────────────────────────────

def _build_snapshot_frontmatter(
    date_str: str,
    fund_nav_df: pd.DataFrame,
    allocation_df: pd.DataFrame,
    xirr: Optional[float],
    max_drawdown: Optional[float],
    cash: float,
    weekly_dca: Optional[float],
) -> str:
    """生成 FamilyFund 主基金快照的 YAML frontmatter。"""

    latest = fund_nav_df.iloc[-1] if not fund_nav_df.empty else None
    prev = fund_nav_df.iloc[-2] if len(fund_nav_df) >= 2 else None

    nav = round(float(latest['NAV']), 4) if latest is not None else 'null'
    total_assets = int(float(latest['Total_Value'])) if latest is not None else 'null'

    # 周变动
    nav_change = 'null'
    if latest is not None and prev is not None:
        delta = float(latest['NAV']) - float(prev['NAV'])
        nav_change = f"{delta:+.4f}"

    # 各类别占比
    alloc = {}
    if allocation_df is not None and not allocation_df.empty:
        # 兼容 Market_Value / Total_Value 列名
        mv_col = 'Market_Value' if 'Market_Value' in allocation_df.columns else 'Total_Value'
        # 兼容已有百分比列（Allocation_Percent）或需自行计算
        if 'Allocation_Percent' in allocation_df.columns:
            for _, row in allocation_df.iterrows():
                alloc[row['Asset_Class']] = round(float(row['Allocation_Percent']) * 100, 1)
        else:
            total_mv = allocation_df[mv_col].sum()
            if total_mv > 0:
                for _, row in allocation_df.iterrows():
                    pct = round(float(row[mv_col]) / total_mv * 100, 1)
                    alloc[row['Asset_Class']] = pct

    def pct(cls):
        return alloc.get(cls, 0.0)

    xirr_val = round(xirr * 100, 2) if xirr is not None else 'null'
    mdd_val = round(max_drawdown * 100, 2) if max_drawdown is not None else 'null'
    dca_val = int(weekly_dca) if weekly_dca is not None else 'null'

    lines = [
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
        f'**{date_str} 周快照** | 总资产 ¥{total_assets:,} | 净值 {nav} | XIRR {xirr_val}%',
        '',
    ]
    return '\n'.join(lines)


def _build_son_snapshot_frontmatter(
    date_str: str,
    son_nav_df: pd.DataFrame,
    xirr: Optional[float],
    cash: float,
) -> str:
    """生成儿子基金快照的 YAML frontmatter。"""
    latest = son_nav_df.iloc[-1] if not son_nav_df.empty else None
    prev = son_nav_df.iloc[-2] if len(son_nav_df) >= 2 else None

    nav = round(float(latest['NAV']), 4) if latest is not None else 'null'
    total_assets = int(float(latest['Total_Value'])) if latest is not None else 'null'

    nav_change = 'null'
    if latest is not None and prev is not None:
        delta = float(latest['NAV']) - float(prev['NAV'])
        nav_change = f"{delta:+.4f}"

    xirr_val = round(xirr * 100, 2) if xirr is not None else 'null'

    lines = [
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
        f'**{date_str} Son Fund 快照** | 总资产 ¥{total_assets:,} | 净值 {nav}',
        '',
    ]
    return '\n'.join(lines)


# ── Holdings 文件生成 ──────────────────────────────────────────────────────────

def _load_decisions(data_dir: str) -> dict:
    """加载 decisions.json，返回 {folder_name: current_decision} 字典。"""
    path = os.path.join(data_dir, 'Finance Reports', '_meta', 'decisions.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    return {k: v.get('current', {}) for k, v in raw.items()}


def _build_holding_file(
    name: str,
    code: str,
    asset_class: str,
    cost: float,
    market_value: float,
    pnl: float,
    pnl_pct: float,
    decision: dict,
    date_str: str,
    target_num: Optional[int],
) -> str:
    """生成单个持仓标的的 md 文件内容。"""

    tier = _fmt_yaml_str(decision.get('tier', ''))
    style = _fmt_yaml_str(decision.get('style', ''))
    action = _fmt_yaml_str(decision.get('action', ''))
    add_trigger = _fmt_yaml_str(decision.get('add_trigger', ''))
    trim_trigger = _fmt_yaml_str(decision.get('trim_trigger', ''))
    target_pos = _fmt_yaml_str(decision.get('target_position', ''))
    summary = _fmt_yaml_str(decision.get('summary', ''))
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
        f'add_trigger: {add_trigger}',
        f'trim_trigger: {trim_trigger}',
        f'date: {date_str}',
        'tags: [familyfund, holding]',
        '---',
        '',
        f'## {name}',
        '',
        f'**决策**：{decision.get("action", "—")}　**层级**：{decision.get("tier", "—")}　**风格**：{decision.get("style", "—")}',
        '',
        f'**摘要**：{decision.get("summary", "—")}',
        '',
        '| 指标 | 数值 |',
        '|------|------|',
        f'| 成本 | ¥{int(cost):,} |',
        f'| 市值 | ¥{int(market_value):,} |',
        f'| 盈亏 | ¥{int(pnl):,}（{pnl_pct:+.1f}%）|',
        f'| 目标仓位 | {decision.get("target_position", "—")} |',
        '',
        f'**加仓触发**：{decision.get("add_trigger", "—")}',
        '',
        f'**减仓触发**：{decision.get("trim_trigger", "—")}',
        '',
    ]

    if source_doc:
        doc_name = source_doc.replace('.md', '')
        lines.append(f'**研报**：[[{doc_name}]]')
        lines.append('')

    return '\n'.join(lines)


def _build_son_holding_file(
    asset_class: str,
    cost: float,
    market_value: float,
    pnl: float,
    pnl_pct: float,
    date_str: str,
) -> str:
    lines = [
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
    ]
    return '\n'.join(lines)


# ── 目标仓位解析 ───────────────────────────────────────────────────────────────

def _parse_target_num(target_str: str) -> Optional[int]:
    """从 target_position 字符串解析数字（万 → 元）。"""
    if not target_str or target_str in ('0', '—', 'null', '观察'):
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*万', target_str)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r'(\d+)', target_str)
    if m:
        return int(m.group(1))
    return None


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
    son_nav_df: Optional[pd.DataFrame] = None,
    son_cost_df: Optional[pd.DataFrame] = None,
    son_xirr: Optional[float] = None,
    vault_path: Optional[str] = None,
) -> dict:
    """
    将 FamilyFund 数据同步到 Obsidian vault。

    Parameters
    ----------
    date_str      : 本周快照日期，格式 YYYY-MM-DD
    data_dir      : FamilyFund data 目录（decisions.json 所在目录的上级）
    fund_nav_df   : 主基金净值 DataFrame
    allocation_df : 资产配置 DataFrame
    cost_basis_df : 持仓盈亏 DataFrame
    xirr          : 主基金 XIRR（小数，如 0.082）
    max_drawdown  : 最大回撤（小数，如 -0.0315）
    weekly_dca    : 本周 DCA 建议金额
    son_nav_df    : 儿子基金净值 DataFrame（可选）
    son_cost_df   : 儿子基金持仓盈亏 DataFrame（可选）
    son_xirr      : 儿子基金 XIRR（可选）
    vault_path    : Obsidian vault 路径（默认自动推断）

    Returns
    -------
    dict: {'ok': bool, 'vault_dir': str, 'files_written': int, 'error': str|None}
    """
    if vault_path is None:
        vault_path = _get_vault_path()
    if vault_path is None:
        return {'ok': False, 'vault_dir': None, 'files_written': 0,
                'error': 'Obsidian vault 路径未找到，请设置 OBSIDIAN_VAULT_PATH 环境变量'}

    ff_dir = os.path.join(vault_path, VAULT_SUBDIR)
    files_written = 0

    try:
        # ── 1. 主看板（只建一次）────────────────────────────────────────────
        main_dash = os.path.join(ff_dir, '_dashboard.md')
        _write_file_if_not_exists(main_dash, _MAIN_DASHBOARD_TEMPLATE)

        # ── 2. 主基金快照（全历史追加）──────────────────────────────────────
        snap_dir = os.path.join(ff_dir, 'snapshots')
        os.makedirs(snap_dir, exist_ok=True)

        # 计算 cash
        cash = 0.0
        if cost_basis_df is not None and not cost_basis_df.empty:
            cash_rows = cost_basis_df[cost_basis_df['Asset_Class'] == 'Cash']
            if not cash_rows.empty:
                cash = float(cash_rows['Market_Value'].sum())

        snap_content = _build_snapshot_frontmatter(
            date_str, fund_nav_df, allocation_df, xirr, max_drawdown, cash, weekly_dca
        )
        snap_path = os.path.join(snap_dir, f'{date_str}.md')
        _write_file(snap_path, snap_content)
        files_written += 1

        # ── 3. Holdings（每周全量覆盖）──────────────────────────────────────
        holdings_dir = os.path.join(ff_dir, 'holdings')
        os.makedirs(holdings_dir, exist_ok=True)

        decisions = _load_decisions(data_dir)

        if cost_basis_df is not None and not cost_basis_df.empty:
            # 只展示个股类（排除 Cash、Fixed_Income）
            individual_classes = {'Individual_Stock', 'Company_Stock', 'Smart_Beta'}
            pl_df = cost_basis_df[
                (cost_basis_df['Asset_Class'].isin(individual_classes)) &
                (cost_basis_df['Market_Value'] > 0)
            ].copy()

            for _, row in pl_df.iterrows():
                code = str(row.get('Code', ''))
                # 兼容 Name / Asset 两种列名
                name = str(row.get('Name') or row.get('Asset') or code)
                asset_class = str(row.get('Asset_Class', ''))
                cost = float(row.get('Cost_Basis') or row.get('Cost') or 0)
                mv = float(row.get('Market_Value', 0))
                pnl = float(row.get('Profit_Loss') or (mv - cost))
                pnl_pct = float(row.get('Profit_Loss_Rate') or
                                ((pnl / cost * 100) if cost > 0 else 0.0))

                # 匹配 decisions：先按 code 精确找，再按 name 模糊找
                decision = {}
                for k, v in decisions.items():
                    if code and code in k:
                        decision = v
                        break
                if not decision:
                    for k, v in decisions.items():
                        if name and name in k:
                            decision = v
                            break

                target_num = _parse_target_num(decision.get('target_position', ''))

                file_content = _build_holding_file(
                    name=name, code=code, asset_class=asset_class,
                    cost=cost, market_value=mv, pnl=pnl, pnl_pct=pnl_pct,
                    decision=decision, date_str=date_str, target_num=target_num,
                )
                fname = _safe_filename(name) + '.md'
                _write_file(os.path.join(holdings_dir, fname), file_content)
                files_written += 1

        # ── 4. 儿子基金（可选）──────────────────────────────────────────────
        if son_nav_df is not None and not son_nav_df.empty:
            son_dir = os.path.join(ff_dir, 'son_fund')
            son_snap_dir = os.path.join(son_dir, 'snapshots')
            son_holdings_dir = os.path.join(son_dir, 'holdings')
            os.makedirs(son_snap_dir, exist_ok=True)
            os.makedirs(son_holdings_dir, exist_ok=True)

            # 儿子基金看板（只建一次）
            son_dash = os.path.join(son_dir, '_dashboard.md')
            _write_file_if_not_exists(son_dash, _SON_DASHBOARD_TEMPLATE)

            # 儿子基金 cash
            son_cash = 0.0
            if son_cost_df is not None and not son_cost_df.empty:
                _scr = son_cost_df[son_cost_df['Asset_Class'] == 'Cash']
                if not _scr.empty:
                    son_cash = float(_scr['Market_Value'].sum())

            son_snap = _build_son_snapshot_frontmatter(
                date_str, son_nav_df, son_xirr, son_cash
            )
            _write_file(os.path.join(son_snap_dir, f'{date_str}.md'), son_snap)
            files_written += 1

            # 儿子基金持仓（全量覆盖）
            if son_cost_df is not None and not son_cost_df.empty:
                son_pl = son_cost_df[
                    (son_cost_df['Asset_Class'] != 'Cash') &
                    (son_cost_df['Market_Value'] > 0)
                ].copy()
                # 兼容 Cost_Basis / Cost 列名
                cost_col = 'Cost_Basis' if 'Cost_Basis' in son_pl.columns else 'Cost'
                son_pl = son_pl.rename(columns={cost_col: '_cost'})
                son_agg = son_pl.groupby('Asset_Class').agg(
                    Cost=('_cost', 'sum'),
                    Market_Value=('Market_Value', 'sum')
                ).reset_index()
                for _, row in son_agg.iterrows():
                    cls = str(row['Asset_Class'])
                    c = float(row['Cost'])
                    mv = float(row['Market_Value'])
                    p = mv - c
                    pp = (p / c * 100) if c > 0 else 0.0
                    content = _build_son_holding_file(cls, c, mv, p, pp, date_str)
                    fname = _safe_filename(cls) + '.md'
                    _write_file(os.path.join(son_holdings_dir, fname), content)
                    files_written += 1

        return {
            'ok': True,
            'vault_dir': ff_dir,
            'files_written': files_written,
            'error': None,
        }

    except Exception as e:
        return {
            'ok': False,
            'vault_dir': ff_dir,
            'files_written': files_written,
            'error': str(e),
        }
