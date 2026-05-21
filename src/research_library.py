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
