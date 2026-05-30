"""research_library.py — 研报库文件系统访问层。

目录约定：
  $FINANCE_REPORTS_DIR/
  ├── <中文名（代码）>/          ← 持仓标的
  │   ├── analysis/             ← .md 分析文档
  │   └── reports/              ← .pdf 原始财报
  ├── _watchlist/
  │   └── <中文名（代码）>/      ← 观察标的，同上结构
  └── _meta/
      └── ticker_map.json       ← 标的映射配置

ticker_map.json 结构：
  {
    "持仓": { "<文件夹名>": {"portfolio_codes": [...], "yf_symbol": "...", "full_name": "..."} },
    "观察": { ... }
  }
"""

import json
import os
import re


def get_reports_dir(data_dir: str) -> str:
    """从 FAMILYFUND_DATA 路径推导 Finance Reports 目录。

    优先读取环境变量 FINANCE_REPORTS_DIR；否则从 data_dir 上级推导：
    .../FamilyFund/data/ → .../FamilyFund/data/Finance Reports/
    """
    env = os.environ.get('FINANCE_REPORTS_DIR', '')
    if env and os.path.isdir(env):
        return env
    candidate = os.path.join(data_dir, 'Finance Reports')
    return candidate


def load_ticker_map(reports_dir: str) -> dict:
    """读取 _meta/ticker_map.json。

    Returns:
        {"持仓": {folder: {...}}, "观察": {folder: {...}}}
        加载失败时返回空结构。
    """
    path = os.path.join(reports_dir, '_meta', 'ticker_map.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return {
            '持仓': data.get('持仓', {}),
            '观察': data.get('观察', {}),
        }
    except Exception:
        return {'持仓': {}, '观察': {}}


def list_tickers(reports_dir: str) -> dict:
    """返回持仓和观察标的文件夹名列表。

    Returns:
        {"持仓": ["成都银行（601838.SS）", ...], "观察": [...]}
        顺序与 ticker_map.json 一致；若文件夹实际不存在则跳过。
    """
    tm = load_ticker_map(reports_dir)
    result = {}
    for group, entries in tm.items():
        if group == '观察':
            base = os.path.join(reports_dir, '_watchlist')
        else:
            base = reports_dir
        result[group] = [
            folder for folder in entries
            if os.path.isdir(os.path.join(base, folder))
        ]
    return result


def _ticker_base_dir(reports_dir: str, folder_name: str, ticker_map: dict = None) -> str:
    """返回标的文件夹的实际路径（自动区分持仓/watchlist）。"""
    if ticker_map is None:
        ticker_map = load_ticker_map(reports_dir)
    if folder_name in ticker_map.get('观察', {}):
        return os.path.join(reports_dir, '_watchlist', folder_name)
    return os.path.join(reports_dir, folder_name)


def list_ticker_files(reports_dir: str, folder_name: str) -> dict:
    """列出某标的下的分析文档和原始财报。

    Returns:
        {
            "analysis": ["文件名.md", ...],   # 按文件名排序
            "reports":  ["文件名.pdf", ...],  # 按文件名排序
        }
    """
    base = _ticker_base_dir(reports_dir, folder_name)
    analysis_dir = os.path.join(base, 'analysis')
    reports_dir_inner = os.path.join(base, 'reports')

    def _list(path, exts):
        if not os.path.isdir(path):
            return []
        return sorted(
            f for f in os.listdir(path)
            if os.path.splitext(f)[1].lower() in exts
        )

    return {
        'analysis': _list(analysis_dir, {'.md'}),
        'reports':  _list(reports_dir_inner, {'.pdf', '.PDF'}),
    }


def read_analysis_md(reports_dir: str, folder_name: str, filename: str) -> str:
    """读取某分析文档的 Markdown 文本。

    Returns:
        文档文本，失败时返回空字符串。
    """
    base = _ticker_base_dir(reports_dir, folder_name)
    path = os.path.join(base, 'analysis', filename)
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''


def get_report_path(reports_dir: str, folder_name: str, filename: str) -> str:
    """返回原始财报 PDF 的完整路径。"""
    base = _ticker_base_dir(reports_dir, folder_name)
    return os.path.join(base, 'reports', filename)


def find_folder_by_code(reports_dir: str, code: str) -> str | None:
    """从 portfolio.csv Code 字段反查文件夹名。

    Returns:
        文件夹名字符串，找不到时返回 None。
    """
    tm = load_ticker_map(reports_dir)
    for group in ('持仓', '观察'):
        for folder, info in tm.get(group, {}).items():
            if code in info.get('portfolio_codes', []):
                return folder
    return None


def extract_ticker_from_folder(folder_name: str) -> str | None:
    """从文件夹名提取括号内的 ticker，如 '成都银行（601838.SS）' → '601838.SS'。"""
    m = re.search(r'[（(](.+?)[）)]', folder_name)
    return m.group(1) if m else None


# ── 决策映射 ─────────────────────────────────────────────

DECISION_ACTIONS = ["买入", "加仓", "持有", "观察", "减仓", "卖出", "不感兴趣", "不进池"]
DECISION_MARKETS = ["A股", "H股", "N/A"]
SUMMARY_MAX_LEN = 50

DECISION_COLORS = {
    "买入":     "🟢",
    "加仓":     "🟢",
    "持有":     "🔵",
    "观察":     "🟡",
    "减仓":     "🟠",
    "卖出":     "🔴",
    "不感兴趣": "⚪",
    "不进池":   "⚫",
}


def _decisions_path(reports_dir: str) -> str:
    return os.path.join(reports_dir, '_meta', 'decisions.json')


def load_decisions(reports_dir: str) -> dict:
    """读取 _meta/decisions.json，缺失或损坏时返回 {}。

    Returns:
        {folder_name: {"current": {...}, "history": [...]}, ...}
    """
    path = _decisions_path(reports_dir)
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_decision(reports_dir: str, folder_name: str) -> dict | None:
    """返回某标的的当前决策，未评估时返回 None。"""
    data = load_decisions(reports_dir)
    entry = data.get(folder_name)
    if not entry:
        return None
    return entry.get('current')


def get_decision_history(reports_dir: str, folder_name: str) -> list:
    """返回某标的的历史决策列表（不含当前），按时间从近到远排序。"""
    data = load_decisions(reports_dir)
    entry = data.get(folder_name)
    if not entry:
        return []
    history = entry.get('history', [])
    # 按 date 降序（新→旧）
    return sorted(history, key=lambda x: x.get('date', ''), reverse=True)


def update_decision(
    reports_dir: str,
    folder_name: str,
    action: str,
    market: str,
    date: str,
    summary: str,
    source_doc: str = '',
    target_position: str = '',
    add_trigger: str = '',
    trim_trigger: str = '',
    tier: str = '',
    style: str = '',
    pace: str = '',
    position_signal: str = '',
) -> None:
    """写入新决策。旧 current 自动归档到 history（仅追加，不可删除）。

    Raises:
        ValueError: 参数非法（action/market 不在枚举内，或 summary 超长）
    """
    if action not in DECISION_ACTIONS:
        raise ValueError(f"action 必须是 {DECISION_ACTIONS} 之一，得到：{action}")
    if market not in DECISION_MARKETS:
        raise ValueError(f"market 必须是 {DECISION_MARKETS} 之一，得到：{market}")
    if len(summary) > SUMMARY_MAX_LEN:
        raise ValueError(f"summary 不能超过 {SUMMARY_MAX_LEN} 字，当前 {len(summary)} 字")

    path = _decisions_path(reports_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = load_decisions(reports_dir)

    new_decision = {
        'action': action,
        'market': market,
        'date': date,
        'summary': summary,
        'source_doc': source_doc,
        'tier': tier,
        'style': style,
        'target_position': target_position,
        'pace': pace,
        'position_signal': position_signal,
        'add_trigger': add_trigger,
        'trim_trigger': trim_trigger,
    }

    entry = data.get(folder_name, {'current': None, 'history': []})
    # 旧 current 归档到 history（仅追加）
    if entry.get('current'):
        entry.setdefault('history', []).append(entry['current'])
    entry['current'] = new_decision
    data[folder_name] = entry

    # 原子写入
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def format_decision_badge(decision: dict | None) -> str:
    """返回简短决策标签字符串。

    例：'🟢 买入·A股'、'🔵 持有'、'❓ 未评估'
    """
    if not decision:
        return '❓ 未评估'
    icon = DECISION_COLORS.get(decision.get('action', ''), '❓')
    action = decision.get('action', '')
    market = decision.get('market', 'N/A')
    if market == 'N/A' or not market:
        return f"{icon} {action}"
    return f"{icon} {action}·{market}"


# ── 仓位汇总 ─────────────────────────────────────────────

def _normalize_code(code: str) -> str:
    """归一化代码用于跨数据源匹配。

    portfolio.csv 用 '601838' / 'HK0700' / 'SAP.DE'
    ticker_map.json 用 '601838.SS' / 'HK700' / 'SAP.DE'
    归一化策略：转大写、去 .SS / .SZ / .HK 后缀；HK 港股代码去 HK 前缀和补齐 0
    """
    s = (code or '').upper().strip()
    is_hk = False
    for suf in ('.SS', '.SZ', '.HK'):
        if s.endswith(suf):
            if suf == '.HK':
                is_hk = True
            s = s[:-len(suf)]
            break
    if s.startswith('HK'):
        is_hk = True
        s = s[2:]
    # 仅港股代码去开头 0（让 HK0700 / HK700 / 00700.HK 一致）
    if is_hk:
        s = s.lstrip('0')
    return s


def _compute_holdings_pct(raw_df) -> dict:
    """从 portfolio.csv 算出每个 Code 当前市值占总资产的比例。

    Args:
        raw_df: nav_engine.load_portfolio() 返回的 DataFrame
                需要列：Date, Code, Total_Value, Asset_Class

    Returns:
        {normalized_code: pct_float}, pct_float 为占比（0-1）
    """
    if raw_df is None or len(raw_df) == 0:
        return {}
    latest_date = raw_df['Date'].max()
    latest = raw_df[raw_df['Date'] == latest_date]
    total = latest['Total_Value'].sum()
    if total <= 0:
        return {}
    result = {}
    for _, row in latest.iterrows():
        code = str(row.get('Code', ''))
        if not code or row.get('Asset_Class') == 'Cash':
            continue
        norm = _normalize_code(code)
        if not norm:
            continue
        result[norm] = result.get(norm, 0.0) + float(row['Total_Value']) / total
    return result


def _compute_holdings_value(raw_df) -> dict:
    """从 portfolio.csv 算出每个 Code 当前市值（绝对值，元）。

    Returns:
        {normalized_code: value_cny}
    """
    if raw_df is None or len(raw_df) == 0:
        return {}
    latest_date = raw_df['Date'].max()
    latest = raw_df[raw_df['Date'] == latest_date]
    result = {}
    for _, row in latest.iterrows():
        code = str(row.get('Code', ''))
        if not code or row.get('Asset_Class') == 'Cash':
            continue
        norm = _normalize_code(code)
        if not norm:
            continue
        result[norm] = result.get(norm, 0.0) + float(row['Total_Value'])
    return result


def _pace_to_target_amount(target_position: str) -> tuple[float | None, float | None]:
    """从 target_position 字符串解析目标金额（元）。

    支持："5万" / "3-4万" / "7万" / "2-3万" / "0" / "ESPP 持续被动" / "观察"

    Returns:
        (low, high) 单位：元；不可解析返回 (None, None)
    """
    s = (target_position or '').strip()
    if not s or s == '0' or s == '观察' or 'ESPP' in s:
        return None, None
    # 匹配 "5万" 或 "3-4万"
    import re
    m = re.match(r'^(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?\s*万$', s)
    if not m:
        return None, None
    low = float(m.group(1)) * 10000
    high = float(m.group(2)) * 10000 if m.group(2) else low
    return low, high


def get_pool_action_list(reports_dir: str, raw_df=None) -> list[dict]:
    """生成"待行动清单"——基于目标 vs 当前持仓的差异。

    Returns:
        list of dict：
          - folder
          - tier
          - target_low / target_high (元)
          - current_cny (元)
          - gap_low / gap_high (元，正数=待加仓，负数=超配)
          - pace
          - status: "待加仓" / "已达标" / "超配" / "不进池"
    """
    rows = get_position_summary(reports_dir, raw_df=raw_df)
    actions = []
    for r in rows:
        tier = r.get('tier', '')
        if tier not in ('核心', '卫星'):
            continue  # 不进池/观察/战略持仓不在行动清单
        target_low, target_high = _pace_to_target_amount(r.get('target_position', ''))
        if target_low is None:
            continue
        cur = r.get('current_position_cny') or 0
        gap_low = target_low - cur
        gap_high = target_high - cur

        if cur > target_high * 1.1:  # 超配 > 10%
            status = "超配"
        elif cur >= target_low:
            status = "已达标"
        else:
            status = "待加仓"

        actions.append({
            'folder': r['folder'],
            'tier': tier,
            'style': r.get('style', ''),
            'target_low': target_low,
            'target_high': target_high,
            'current_cny': cur,
            'gap_low': gap_low,
            'gap_high': gap_high,
            'pace': r.get('pace', ''),
            'status': status,
        })
    return actions


def get_style_exposure(reports_dir: str, raw_df=None) -> dict:
    """聚合个股池内按风格的暴露（E2-A 轻量版）。

    不穿透 ETF —— 红利低波 ETF 单独作为一个"高股息+低波"整体。

    Returns:
        {
          'pool_by_style': {style: cny}，仅个股池内（tier ∈ 核心/卫星）
          'pool_by_style_target': {style: target_cny}
          'total_pool_cny': 当前池占用
          'total_pool_target': 30 万
        }
    """
    rows = get_position_summary(reports_dir, raw_df=raw_df)

    pool_by_style = {}
    pool_by_style_target = {}
    total_cur = 0
    total_target_low = 0

    for r in rows:
        tier = r.get('tier', '')
        if tier not in ('核心', '卫星'):
            continue
        style = r.get('style', '混合') or '混合'
        cur = r.get('current_position_cny') or 0
        target_low, target_high = _pace_to_target_amount(r.get('target_position', ''))

        pool_by_style[style] = pool_by_style.get(style, 0) + cur
        if target_low is not None:
            target_mid = (target_low + target_high) / 2
            pool_by_style_target[style] = pool_by_style_target.get(style, 0) + target_mid
            total_target_low += target_mid

        total_cur += cur

    return {
        'pool_by_style': pool_by_style,
        'pool_by_style_target': pool_by_style_target,
        'total_pool_cny': total_cur,
        'total_pool_target': STOCK_POOL_TOTAL_CNY,
        'total_target_amount': total_target_low,
    }
    """从 portfolio.csv 算出每个 Code 当前市值（绝对值，元）。

    Returns:
        {normalized_code: value_cny}
    """
    if raw_df is None or len(raw_df) == 0:
        return {}
    latest_date = raw_df['Date'].max()
    latest = raw_df[raw_df['Date'] == latest_date]
    result = {}
    for _, row in latest.iterrows():
        code = str(row.get('Code', ''))
        if not code or row.get('Asset_Class') == 'Cash':
            continue
        norm = _normalize_code(code)
        if not norm:
            continue
        result[norm] = result.get(norm, 0.0) + float(row['Total_Value'])
    return result


# 个股池总额度（来自 P6 决策，2026-05-22 写死）
STOCK_POOL_TOTAL_CNY = 350_000.0  # P6: 个股池总额度（写死，2026-05-22 设 30 万；2026-05-23 调整为 35 万——7 个标的进池后 30 万显得局促）


def get_position_summary(reports_dir: str, raw_df=None) -> list[dict]:
    """返回所有标的的决策与仓位汇总，供 Dashboard 表格展示。

    Args:
        reports_dir: Finance Reports 目录
        raw_df: portfolio.csv 加载后的 DataFrame（可选）。传入时会算"当前实际仓位"。

    Returns:
        list of dict，每条包含：
          - folder: 文件夹名（标的）
          - group: '持仓' | '观察'
          - action / market / date / summary
          - tier: 核心 / 卫星 / 不进池 / 观察 / 战略持仓 / ...
          - style: 高股息 / 成长 / 周期 / 防御 / 混合
          - target_position: 建议仓位（绝对金额，如 "5万"）
          - pace: 节奏（如 "6 月分批"）
          - position_signal: 触发信号（如 "PB 历史分位 + 油价"）
          - current_position_pct: 当前实际仓位（占总资产比例，0-1）；无持仓为 None
          - current_position_cny: 当前实际市值（元）；无持仓为 None
          - pool_pct: 占个股池比例（current_cny / 30 万）；不在池内或无持仓为 None
          - add_trigger / trim_trigger
          - source_doc
        顺序：先持仓后观察，组内按 ticker_map 顺序
    """
    tm = load_ticker_map(reports_dir)
    decisions = load_decisions(reports_dir)
    holdings_pct = _compute_holdings_pct(raw_df) if raw_df is not None else {}
    holdings_value = _compute_holdings_value(raw_df) if raw_df is not None else {}

    rows = []
    for group in ('持仓', '观察'):
        for folder, info in tm.get(group, {}).items():
            d = decisions.get(folder, {}).get('current') or {}
            tier = d.get('tier', '')

            # 当前实际仓位：portfolio_codes 中任一匹配则求和
            current_pct = None
            current_cny = None
            codes = info.get('portfolio_codes', [])
            if codes:
                pct = 0.0
                cny = 0.0
                matched = False
                for c in codes:
                    n = _normalize_code(c)
                    if n in holdings_pct:
                        pct += holdings_pct[n]
                        cny += holdings_value.get(n, 0.0)
                        matched = True
                if matched:
                    current_pct = pct
                    current_cny = cny

            # 占个股池比例（仅当 tier 是 核心/卫星 + 已持仓）
            pool_pct = None
            if current_cny is not None and tier in ('核心', '卫星'):
                pool_pct = current_cny / STOCK_POOL_TOTAL_CNY

            rows.append({
                'folder': folder,
                'group': group,
                'action': d.get('action', ''),
                'market': d.get('market', ''),
                'date': d.get('date', ''),
                'summary': d.get('summary', ''),
                'tier': tier,
                'style': d.get('style', ''),
                'target_position': d.get('target_position', ''),
                'pace': d.get('pace', ''),
                'position_signal': d.get('position_signal', ''),
                'current_position': current_pct,  # 保留向后兼容
                'current_position_pct': current_pct,
                'current_position_cny': current_cny,
                'pool_pct': pool_pct,
                'yf_symbol': info.get('yf_symbol', ''),
                'add_trigger': d.get('add_trigger', ''),
                'trim_trigger': d.get('trim_trigger', ''),
                'source_doc': d.get('source_doc', ''),
                'evaluated': bool(d),
            })
    return rows


# ── 研报库同步 ─────────────────────────────────────────────

def sync_new_holding(
    reports_dir: str,
    folder_name: str,
    portfolio_codes: list,
    yf_symbol: str = '',
    full_name: str = '',
) -> dict:
    """将标的从"观察"升级为"持仓"（幂等）。

    1. 移动目录：_watchlist/<folder> → <reports_dir>/<folder>
       若已在根目录则跳过（moved=False）
    2. ticker_map.json：把条目从 观察 移到 持仓，填 portfolio_codes
       若已在持仓则只更新 portfolio_codes
    3. decisions.json：若 current.action == '买入'，自动改为 '持有'
       旧 current 归档到 history，date 改为今天

    Returns:
        {'moved': bool, 'ticker_map_updated': bool, 'decision_updated': bool}
    """
    import datetime
    result = {'moved': False, 'ticker_map_updated': False, 'decision_updated': False}

    # 1. 移动目录
    watchlist_path = os.path.join(reports_dir, '_watchlist', folder_name)
    root_path = os.path.join(reports_dir, folder_name)
    if os.path.isdir(watchlist_path) and not os.path.isdir(root_path):
        try:
            os.rename(watchlist_path, root_path)
            result['moved'] = True
        except OSError:
            pass  # 移动失败不阻断后续步骤

    # 2. ticker_map.json
    tm_path = os.path.join(reports_dir, '_meta', 'ticker_map.json')
    try:
        with open(tm_path, encoding='utf-8') as f:
            tm = json.load(f)
    except Exception:
        tm = {'持仓': {}, '观察': {}}

    holding_map = tm.setdefault('持仓', {})
    watch_map = tm.setdefault('观察', {})

    if folder_name in watch_map:
        entry = watch_map.pop(folder_name)
    elif folder_name in holding_map:
        entry = holding_map[folder_name]
    else:
        entry = {}

    # 更新 portfolio_codes（追加不重复）
    existing_codes = entry.get('portfolio_codes', [])
    for c in portfolio_codes:
        if c and c not in existing_codes:
            existing_codes.append(c)
    entry['portfolio_codes'] = existing_codes
    if yf_symbol:
        entry['yf_symbol'] = yf_symbol
    if full_name:
        entry['full_name'] = full_name
    holding_map[folder_name] = entry

    tmp = tm_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(tm, f, ensure_ascii=False, indent=2)
    os.replace(tmp, tm_path)
    result['ticker_map_updated'] = True

    # 3. decisions.json：买入 → 持有
    dec_path = _decisions_path(reports_dir)
    data = load_decisions(reports_dir)
    dec_entry = data.get(folder_name)
    if dec_entry and dec_entry.get('current', {}).get('action') == '买入':
        old_current = dec_entry['current']
        dec_entry.setdefault('history', []).append(old_current)
        new_current = dict(old_current)
        new_current['action'] = '持有'
        new_current['date'] = datetime.date.today().isoformat()
        dec_entry['current'] = new_current
        data[folder_name] = dec_entry
        tmp = dec_path + '.tmp'
        os.makedirs(os.path.dirname(dec_path), exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dec_path)
        result['decision_updated'] = True

    return result

