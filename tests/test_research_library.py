"""tests/test_research_library.py — research_library.py 单元测试。

使用 tmp_path fixture 构造临时文件系统，不依赖 iCloud 路径。
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from research_library import (
    get_reports_dir,
    load_ticker_map,
    list_tickers,
    list_ticker_files,
    read_analysis_md,
    get_report_path,
    find_folder_by_code,
    extract_ticker_from_folder,
    _ticker_base_dir,
)


# ── Fixtures ──────────────────────────────────────────────

TICKER_MAP = {
    "持仓": {
        "成都银行（601838.SS）": {
            "portfolio_codes": ["601838.SS"],
            "yf_symbol": "601838.SS",
            "full_name": "成都银行股份有限公司",
        },
        "思爱普（SAP）": {
            "portfolio_codes": ["SAP.DE"],
            "yf_symbol": "SAP",
            "full_name": "SAP SE",
        },
    },
    "观察": {
        "阿里巴巴（09988.HK）": {
            "portfolio_codes": [],
            "yf_symbol": "09988.HK",
            "full_name": "阿里巴巴集团控股有限公司",
        },
    },
}


@pytest.fixture()
def reports_dir(tmp_path):
    """构造最小化的 Finance Reports 目录结构。"""
    base = tmp_path / "Finance Reports"

    # _meta
    meta = base / "_meta"
    meta.mkdir(parents=True)
    (meta / "ticker_map.json").write_text(
        json.dumps(TICKER_MAP, ensure_ascii=False), encoding="utf-8"
    )

    # 持仓：成都银行
    bocd = base / "成都银行（601838.SS）"
    (bocd / "analysis").mkdir(parents=True)
    (bocd / "reports").mkdir(parents=True)
    (bocd / "analysis" / "2026-05 芒格框架分析.md").write_text(
        "# 成都银行\n\nPB 0.85x", encoding="utf-8"
    )
    (bocd / "reports" / "2026Q1报告.pdf").write_bytes(b"%PDF-1.4 fake")

    # 持仓：思爱普（无文档）
    sap = base / "思爱普（SAP）"
    (sap / "analysis").mkdir(parents=True)
    (sap / "reports").mkdir(parents=True)

    # 观察：阿里巴巴
    watchlist = base / "_watchlist" / "阿里巴巴（09988.HK）"
    (watchlist / "analysis").mkdir(parents=True)
    (watchlist / "reports").mkdir(parents=True)
    (watchlist / "analysis" / "2026-05 阿里分析.md").write_text(
        "# 阿里巴巴", encoding="utf-8"
    )

    return str(base)


# ── get_reports_dir ────────────────────────────────────────

def test_get_reports_dir_env(tmp_path, monkeypatch):
    target = str(tmp_path / "MyReports")
    os.makedirs(target)
    monkeypatch.setenv("FINANCE_REPORTS_DIR", target)
    assert get_reports_dir("/some/data") == target


def test_get_reports_dir_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("FINANCE_REPORTS_DIR", raising=False)
    data_dir = str(tmp_path / "data")
    result = get_reports_dir(data_dir)
    assert result == os.path.join(data_dir, "Finance Reports")


# ── load_ticker_map ────────────────────────────────────────

def test_load_ticker_map_basic(reports_dir):
    tm = load_ticker_map(reports_dir)
    assert "持仓" in tm
    assert "观察" in tm
    assert "成都银行（601838.SS）" in tm["持仓"]
    assert "阿里巴巴（09988.HK）" in tm["观察"]


def test_load_ticker_map_missing(tmp_path):
    """目录不存在时返回空结构，不抛异常。"""
    tm = load_ticker_map(str(tmp_path / "nonexistent"))
    assert tm == {"持仓": {}, "观察": {}}


def test_load_ticker_map_corrupt_json(tmp_path):
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "ticker_map.json").write_text("not json", encoding="utf-8")
    tm = load_ticker_map(str(tmp_path))
    assert tm == {"持仓": {}, "观察": {}}


# ── list_tickers ───────────────────────────────────────────

def test_list_tickers_groups(reports_dir):
    result = list_tickers(reports_dir)
    assert set(result["持仓"]) == {"成都银行（601838.SS）", "思爱普（SAP）"}
    assert result["观察"] == ["阿里巴巴（09988.HK）"]


def test_list_tickers_skips_missing_folder(reports_dir):
    """ticker_map 里有但文件夹不存在的标的应被跳过。"""
    import shutil
    shutil.rmtree(os.path.join(reports_dir, "思爱普（SAP）"))
    result = list_tickers(reports_dir)
    assert "思爱普（SAP）" not in result["持仓"]


# ── _ticker_base_dir ───────────────────────────────────────

def test_ticker_base_dir_holding(reports_dir):
    path = _ticker_base_dir(reports_dir, "成都银行（601838.SS）")
    assert path == os.path.join(reports_dir, "成都银行（601838.SS）")


def test_ticker_base_dir_watchlist(reports_dir):
    path = _ticker_base_dir(reports_dir, "阿里巴巴（09988.HK）")
    assert path == os.path.join(reports_dir, "_watchlist", "阿里巴巴（09988.HK）")


# ── list_ticker_files ──────────────────────────────────────

def test_list_ticker_files_bocd(reports_dir):
    files = list_ticker_files(reports_dir, "成都银行（601838.SS）")
    assert files["analysis"] == ["2026-05 芒格框架分析.md"]
    assert files["reports"] == ["2026Q1报告.pdf"]


def test_list_ticker_files_empty(reports_dir):
    files = list_ticker_files(reports_dir, "思爱普（SAP）")
    assert files["analysis"] == []
    assert files["reports"] == []


def test_list_ticker_files_watchlist(reports_dir):
    files = list_ticker_files(reports_dir, "阿里巴巴（09988.HK）")
    assert files["analysis"] == ["2026-05 阿里分析.md"]
    assert files["reports"] == []


def test_list_ticker_files_sorted(reports_dir):
    """多文件时按文件名排序。"""
    analysis_dir = os.path.join(reports_dir, "成都银行（601838.SS）", "analysis")
    open(os.path.join(analysis_dir, "2025-11 快速扫描.md"), "w").close()
    files = list_ticker_files(reports_dir, "成都银行（601838.SS）")
    assert files["analysis"][0] == "2025-11 快速扫描.md"
    assert files["analysis"][1] == "2026-05 芒格框架分析.md"


# ── read_analysis_md ───────────────────────────────────────

def test_read_analysis_md_ok(reports_dir):
    text = read_analysis_md(reports_dir, "成都银行（601838.SS）", "2026-05 芒格框架分析.md")
    assert "PB 0.85x" in text


def test_read_analysis_md_watchlist(reports_dir):
    text = read_analysis_md(reports_dir, "阿里巴巴（09988.HK）", "2026-05 阿里分析.md")
    assert "阿里巴巴" in text


def test_read_analysis_md_missing(reports_dir):
    """文件不存在时返回空字符串，不抛异常。"""
    text = read_analysis_md(reports_dir, "成都银行（601838.SS）", "不存在.md")
    assert text == ""


# ── get_report_path ────────────────────────────────────────

def test_get_report_path(reports_dir):
    path = get_report_path(reports_dir, "成都银行（601838.SS）", "2026Q1报告.pdf")
    assert os.path.exists(path)
    assert path.endswith("2026Q1报告.pdf")


# ── find_folder_by_code ────────────────────────────────────

def test_find_folder_by_code_holding(reports_dir):
    assert find_folder_by_code(reports_dir, "601838.SS") == "成都银行（601838.SS）"


def test_find_folder_by_code_sap(reports_dir):
    """SAP 用 SAP.DE 作为 portfolio_code。"""
    assert find_folder_by_code(reports_dir, "SAP.DE") == "思爱普（SAP）"


def test_find_folder_by_code_not_found(reports_dir):
    assert find_folder_by_code(reports_dir, "UNKNOWN") is None


def test_find_folder_by_code_watchlist_empty(reports_dir):
    """观察中标的 portfolio_codes 为空，不应误匹配。"""
    assert find_folder_by_code(reports_dir, "09988.HK") is None


# ── extract_ticker_from_folder ─────────────────────────────

@pytest.mark.parametrize("folder,expected", [
    ("成都银行（601838.SS）", "601838.SS"),
    ("思爱普（SAP）", "SAP"),
    ("腾讯控股（00700.HK）", "00700.HK"),
    ("阿里巴巴（09988.HK）", "09988.HK"),
    ("无括号名称", None),
])
def test_extract_ticker_from_folder(folder, expected):
    assert extract_ticker_from_folder(folder) == expected
