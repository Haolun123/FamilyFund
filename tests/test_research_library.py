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
    # 决策相关
    load_decisions,
    get_decision,
    get_decision_history,
    update_decision,
    format_decision_badge,
    DECISION_ACTIONS,
    DECISION_MARKETS,
    SUMMARY_MAX_LEN,
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


# ── 决策映射测试 ─────────────────────────────────────────

def test_load_decisions_missing(tmp_path):
    """文件不存在时返回空 dict，不抛异常。"""
    assert load_decisions(str(tmp_path)) == {}


def test_load_decisions_corrupt(tmp_path):
    """JSON 损坏时返回空 dict。"""
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "decisions.json").write_text("not json", encoding='utf-8')
    assert load_decisions(str(tmp_path)) == {}


def test_get_decision_unknown(tmp_path):
    """未评估标的返回 None。"""
    assert get_decision(str(tmp_path), "未评估标的") is None


def test_update_decision_first_time(tmp_path):
    """首次写入：current 有值，history 为空。"""
    update_decision(str(tmp_path), "招商银行（600036.SS）",
                    "买入", "A股", "2026-05-21", "PB 0.83x 龙头折价", "x.md")
    cur = get_decision(str(tmp_path), "招商银行（600036.SS）")
    assert cur['action'] == "买入"
    assert cur['market'] == "A股"
    assert cur['date'] == "2026-05-21"
    assert cur['summary'] == "PB 0.83x 龙头折价"
    assert cur['source_doc'] == "x.md"
    assert get_decision_history(str(tmp_path), "招商银行（600036.SS）") == []


def test_update_decision_archives_history(tmp_path):
    """更新决策时旧 current 归档到 history。"""
    update_decision(str(tmp_path), "成都银行（601838.SS）",
                    "买入", "A股", "2026-04-10", "建仓", "v1.md")
    update_decision(str(tmp_path), "成都银行（601838.SS）",
                    "持有", "A股", "2026-05-18", "外资股东不动", "v2.md")
    cur = get_decision(str(tmp_path), "成都银行（601838.SS）")
    history = get_decision_history(str(tmp_path), "成都银行（601838.SS）")
    assert cur['action'] == "持有"
    assert cur['date'] == "2026-05-18"
    assert len(history) == 1
    assert history[0]['action'] == "买入"
    assert history[0]['date'] == "2026-04-10"


def test_update_decision_history_sorted_desc(tmp_path):
    """history 按日期降序（新→旧）排序。"""
    folder = "测试（TEST）"
    update_decision(str(tmp_path), folder, "买入", "A股", "2026-01-01", "v1", "")
    update_decision(str(tmp_path), folder, "持有", "A股", "2026-03-01", "v2", "")
    update_decision(str(tmp_path), folder, "加仓", "A股", "2026-05-01", "v3", "")
    history = get_decision_history(str(tmp_path), folder)
    assert [h['date'] for h in history] == ["2026-03-01", "2026-01-01"]


def test_update_decision_invalid_action(tmp_path):
    with pytest.raises(ValueError, match="action"):
        update_decision(str(tmp_path), "x", "瞎搞", "A股", "2026-05-21", "s", "")


def test_update_decision_invalid_market(tmp_path):
    with pytest.raises(ValueError, match="market"):
        update_decision(str(tmp_path), "x", "买入", "美股", "2026-05-21", "s", "")


def test_update_decision_summary_too_long(tmp_path):
    summary = "a" * (SUMMARY_MAX_LEN + 1)
    with pytest.raises(ValueError, match="summary"):
        update_decision(str(tmp_path), "x", "买入", "A股", "2026-05-21", summary, "")


def test_update_decision_summary_exact_max(tmp_path):
    """恰好 50 字可以保存。"""
    summary = "a" * SUMMARY_MAX_LEN
    update_decision(str(tmp_path), "x", "买入", "A股", "2026-05-21", summary, "")
    assert get_decision(str(tmp_path), "x")['summary'] == summary


def test_format_decision_badge_with_market():
    decision = {"action": "买入", "market": "A股", "date": "2026-05-21",
                "summary": "x", "source_doc": ""}
    assert format_decision_badge(decision) == "🟢 买入·A股"


def test_format_decision_badge_na_market():
    decision = {"action": "持有", "market": "N/A", "date": "2026-05-21",
                "summary": "x", "source_doc": ""}
    assert format_decision_badge(decision) == "🔵 持有"


def test_format_decision_badge_none():
    """未评估返回固定标签。"""
    assert format_decision_badge(None) == "❓ 未评估"


@pytest.mark.parametrize("action,expected_icon", [
    ("买入", "🟢"),
    ("加仓", "🟢"),
    ("持有", "🔵"),
    ("观察", "🟡"),
    ("减仓", "🟠"),
    ("卖出", "🔴"),
    ("不感兴趣", "⚪"),
])
def test_format_decision_badge_all_actions(action, expected_icon):
    decision = {"action": action, "market": "N/A", "date": "2026-05-21",
                "summary": "x", "source_doc": ""}
    assert format_decision_badge(decision).startswith(expected_icon)


def test_decisions_persist_to_disk(tmp_path):
    """写入后磁盘文件存在且内容正确。"""
    update_decision(str(tmp_path), "x", "买入", "A股", "2026-05-21", "s", "doc.md")
    decisions_file = tmp_path / "_meta" / "decisions.json"
    assert decisions_file.exists()
    data = json.loads(decisions_file.read_text(encoding='utf-8'))
    assert data["x"]["current"]["action"] == "买入"


# ── 仓位汇总测试 ─────────────────────────────────────────

from research_library import (
    _normalize_code,
    _compute_holdings_pct,
    get_position_summary,
)


def test_normalize_code_strips_suffix():
    assert _normalize_code("601838.SS") == "601838"
    assert _normalize_code("002202.SZ") == "002202"
    assert _normalize_code("00700.HK") == "700"


def test_normalize_code_strips_hk_prefix():
    """portfolio.csv 用 HK0700，ticker_map 用 HK700/00700.HK，归一化后应一致。"""
    assert _normalize_code("HK0700") == _normalize_code("HK700") == _normalize_code("00700.HK")


def test_normalize_code_handles_empty():
    assert _normalize_code("") == ""
    assert _normalize_code(None) == ""


def test_normalize_code_keeps_letters():
    """SAP.DE 这种带后缀但不是 .SS/.SZ/.HK，保留。"""
    assert _normalize_code("SAP.DE") == "SAP.DE"


def test_compute_holdings_pct_basic():
    """两只持仓 + 一笔现金，权重应忽略现金、按市值占比。"""
    import pandas as pd
    df = pd.DataFrame([
        {"Date": "2026-05-15", "Code": "601838", "Total_Value": 22464.0, "Asset_Class": "ETF_Stock"},
        {"Date": "2026-05-15", "Code": "HK0700", "Total_Value": 39640.0, "Asset_Class": "ETF_Stock"},
        {"Date": "2026-05-15", "Code": "CASH", "Total_Value": 127787.79, "Asset_Class": "Cash"},
    ])
    pct = _compute_holdings_pct(df)
    # 应按总资产（含现金）算占比
    total = 22464.0 + 39640.0 + 127787.79
    assert pct["601838"] == pytest.approx(22464.0 / total)
    assert pct["700"] == pytest.approx(39640.0 / total)
    # 现金不计入
    assert "CASH" not in pct


def test_compute_holdings_pct_uses_latest_date():
    """有多个日期时只取最新。"""
    import pandas as pd
    df = pd.DataFrame([
        {"Date": "2026-05-08", "Code": "601838", "Total_Value": 100, "Asset_Class": "ETF_Stock"},
        {"Date": "2026-05-15", "Code": "601838", "Total_Value": 200, "Asset_Class": "ETF_Stock"},
    ])
    pct = _compute_holdings_pct(df)
    assert pct["601838"] == pytest.approx(1.0)  # 唯一持仓占 100%


def test_compute_holdings_pct_empty_df():
    import pandas as pd
    assert _compute_holdings_pct(None) == {}
    assert _compute_holdings_pct(pd.DataFrame()) == {}


def _seed_summary_fixture(tmp_path):
    """构建一个包含持仓+观察+未评估的小型 fixture。"""
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "ticker_map.json").write_text(json.dumps({
        "持仓": {
            "成都银行（601838.SS）": {"portfolio_codes": ["601838.SS"], "yf_symbol": "601838.SS"},
            "腾讯控股（00700.HK）": {"portfolio_codes": ["HK700"], "yf_symbol": "00700.HK"},
        },
        "观察": {
            "招商银行（600036.SS）": {"portfolio_codes": [], "yf_symbol": "600036.SS"},
            "未评估标的（XXX）": {"portfolio_codes": [], "yf_symbol": "XXX"},
        },
    }, ensure_ascii=False), encoding='utf-8')

    # decisions.json：3 个标的有决策，1 个未评估
    update_decision(str(tmp_path), "成都银行（601838.SS）", "持有", "A股",
                    "2026-05-18", "PB0.85x", "成都银行.md",
                    target_position="已持有", add_trigger="PB<0.7", trim_trigger="ROE<12%")
    update_decision(str(tmp_path), "腾讯控股（00700.HK）", "买入", "H股",
                    "2026-05-15", "Yes", "腾讯.md",
                    target_position="5-8%", add_trigger="PE<15", trim_trigger="广告负")
    update_decision(str(tmp_path), "招商银行（600036.SS）", "买入", "A股",
                    "2026-05-21", "PB0.83x", "招行.md",
                    target_position="3-5%", add_trigger="PB<0.75", trim_trigger="ROE<10%")


def test_get_position_summary_includes_unevaluated(tmp_path):
    """未评估标的也应在汇总中（evaluated=False）。"""
    _seed_summary_fixture(tmp_path)
    rows = get_position_summary(str(tmp_path))
    folders = [r['folder'] for r in rows]
    assert "未评估标的（XXX）" in folders
    unevaluated = next(r for r in rows if r['folder'] == "未评估标的（XXX）")
    assert unevaluated['evaluated'] is False
    assert unevaluated['action'] == ''


def test_get_position_summary_order(tmp_path):
    """先持仓后观察，组内按 ticker_map 顺序。"""
    _seed_summary_fixture(tmp_path)
    rows = get_position_summary(str(tmp_path))
    groups = [r['group'] for r in rows]
    assert groups == ['持仓', '持仓', '观察', '观察']
    assert rows[0]['folder'] == "成都银行（601838.SS）"
    assert rows[1]['folder'] == "腾讯控股（00700.HK）"


def test_get_position_summary_with_holdings(tmp_path):
    """传入 raw_df 时算出 current_position。"""
    import pandas as pd
    _seed_summary_fixture(tmp_path)
    df = pd.DataFrame([
        {"Date": "2026-05-15", "Code": "601838", "Total_Value": 22464.0, "Asset_Class": "ETF_Stock"},
        {"Date": "2026-05-15", "Code": "HK0700", "Total_Value": 39640.0, "Asset_Class": "ETF_Stock"},
        {"Date": "2026-05-15", "Code": "CASH", "Total_Value": 127787.79, "Asset_Class": "Cash"},
    ])
    rows = get_position_summary(str(tmp_path), raw_df=df)
    boc = next(r for r in rows if r['folder'] == "成都银行（601838.SS）")
    txn = next(r for r in rows if r['folder'] == "腾讯控股（00700.HK）")
    cmb = next(r for r in rows if r['folder'] == "招商银行（600036.SS）")
    total = 22464.0 + 39640.0 + 127787.79
    assert boc['current_position'] == pytest.approx(22464.0 / total)
    # 腾讯：portfolio_codes=['HK700'] 应该匹配 portfolio.csv 的 HK0700
    assert txn['current_position'] == pytest.approx(39640.0 / total)
    # 招行 portfolio_codes=[]，应为 None
    assert cmb['current_position'] is None


def test_get_position_summary_no_df(tmp_path):
    """不传 raw_df 时所有 current_position 为 None。"""
    _seed_summary_fixture(tmp_path)
    rows = get_position_summary(str(tmp_path))
    for r in rows:
        assert r['current_position'] is None


def test_get_position_summary_target_position_field(tmp_path):
    """target_position / add_trigger / trim_trigger 应正确返回。"""
    _seed_summary_fixture(tmp_path)
    rows = get_position_summary(str(tmp_path))
    txn = next(r for r in rows if r['folder'] == "腾讯控股（00700.HK）")
    assert txn['target_position'] == "5-8%"
    assert txn['add_trigger'] == "PE<15"
    assert txn['trim_trigger'] == "广告负"


# ── 扩展字段（tier/style/pace/...）+ "不进池" action ─────────


def test_decision_action_includes_pool_exclusion():
    """'不进池' 应在 DECISION_ACTIONS 枚举内。"""
    assert "不进池" in DECISION_ACTIONS


def test_decision_color_for_pool_exclusion():
    decision = {"action": "不进池", "market": "A股", "date": "2026-05-22",
                "summary": "x", "source_doc": ""}
    badge = format_decision_badge(decision)
    assert badge.startswith("⚫")


def test_update_decision_with_extended_fields(tmp_path):
    """update_decision 接受 tier/style/pace/position_signal 字段。"""
    update_decision(
        str(tmp_path), "x", "买入", "A股", "2026-05-22", "s", "doc.md",
        target_position="5万", add_trigger="add", trim_trigger="trim",
        tier="核心", style="高股息+周期", pace="6 月分批",
        position_signal="PB 历史分位 + 油价",
    )
    cur = get_decision(str(tmp_path), "x")
    assert cur['tier'] == "核心"
    assert cur['style'] == "高股息+周期"
    assert cur['pace'] == "6 月分批"
    assert cur['position_signal'] == "PB 历史分位 + 油价"


def test_update_decision_pool_exclusion_action(tmp_path):
    """'不进池' 是合法 action。"""
    update_decision(str(tmp_path), "招行", "不进池", "A股",
                    "2026-05-22", "与成都银行同行业，名额留给非银行", "x.md",
                    tier="不进池", target_position="0")
    cur = get_decision(str(tmp_path), "招行")
    assert cur['action'] == "不进池"
    assert cur['tier'] == "不进池"


def test_position_summary_returns_extended_fields(tmp_path):
    """get_position_summary 返回 tier/style/pace/position_signal。"""
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "ticker_map.json").write_text(json.dumps({
        "持仓": {"成都银行（601838.SS）": {"portfolio_codes": ["601838.SS"], "yf_symbol": "601838.SS"}},
        "观察": {},
    }, ensure_ascii=False), encoding='utf-8')
    update_decision(
        str(tmp_path), "成都银行（601838.SS）", "持有", "A股",
        "2026-05-22", "PB 0.85", "成都.md",
        target_position="4万", tier="卫星", style="高股息",
        pace="3-4 月分批", position_signal="PB 历史分位",
    )
    rows = get_position_summary(str(tmp_path))
    boc = rows[0]
    assert boc['tier'] == "卫星"
    assert boc['style'] == "高股息"
    assert boc['pace'] == "3-4 月分批"
    assert boc['position_signal'] == "PB 历史分位"
    assert boc['target_position'] == "4万"


def test_pool_pct_calculated_for_core_satellite(tmp_path):
    """tier ∈ {核心, 卫星} 且已持仓时，pool_pct 应有值。"""
    import pandas as pd
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "ticker_map.json").write_text(json.dumps({
        "持仓": {
            "腾讯（HK00700）": {"portfolio_codes": ["HK700"], "yf_symbol": "00700.HK"},
            "招行（A）": {"portfolio_codes": ["600036.SS"], "yf_symbol": "600036.SS"},
        },
        "观察": {},
    }, ensure_ascii=False), encoding='utf-8')
    # 腾讯 = 核心仓
    update_decision(str(tmp_path), "腾讯（HK00700）", "买入", "H股",
                    "2026-05-22", "成长", "x.md",
                    tier="核心", target_position="7万")
    # 招行 = 不进池
    update_decision(str(tmp_path), "招行（A）", "不进池", "A股",
                    "2026-05-22", "不进池", "x.md",
                    tier="不进池", target_position="0")

    df = pd.DataFrame([
        {"Date": "2026-05-15", "Code": "HK0700", "Total_Value": 80000.0, "Asset_Class": "ETF_Stock"},
        {"Date": "2026-05-15", "Code": "600036", "Total_Value": 0.0, "Asset_Class": "ETF_Stock"},
    ])
    rows = get_position_summary(str(tmp_path), raw_df=df)
    txn = next(r for r in rows if r['folder'] == "腾讯（HK00700）")
    cmb = next(r for r in rows if r['folder'] == "招行（A）")

    # 腾讯：核心仓 + 持仓 8 万 → pool_pct = 8/30 = 0.267
    assert txn['pool_pct'] == pytest.approx(80000.0 / 300000.0)
    assert txn['current_position_cny'] == pytest.approx(80000.0)
    # 招行：不进池 → pool_pct 为 None（即使 portfolio_codes 匹配但市值 0）
    assert cmb['pool_pct'] is None


def test_pool_pct_none_when_not_in_pool(tmp_path):
    """tier 不是 核心/卫星 时 pool_pct 应为 None。"""
    import pandas as pd
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "ticker_map.json").write_text(json.dumps({
        "持仓": {"SAP": {"portfolio_codes": ["SAP.DE"], "yf_symbol": "SAP"}},
        "观察": {},
    }, ensure_ascii=False), encoding='utf-8')
    update_decision(str(tmp_path), "SAP", "持有", "N/A",
                    "2026-05-22", "战略", "x.md",
                    tier="战略持仓（不计入个股池）", target_position="ESPP 持续")
    df = pd.DataFrame([
        {"Date": "2026-05-15", "Code": "SAP.DE", "Total_Value": 480000.0, "Asset_Class": "Company_Stock"},
    ])
    rows = get_position_summary(str(tmp_path), raw_df=df)
    sap = rows[0]
    # current_cny 有值（已持仓），但 pool_pct 为 None（不在池内）
    assert sap['current_position_cny'] == pytest.approx(480000.0)
    assert sap['pool_pct'] is None


# ── E1 + E2-A 测试 ────────────────────────────────────────

from research_library import (
    _pace_to_target_amount, get_pool_action_list, get_style_exposure,
)


def test_pace_to_target_amount_simple():
    assert _pace_to_target_amount("5万") == (50000.0, 50000.0)
    assert _pace_to_target_amount("7万") == (70000.0, 70000.0)


def test_pace_to_target_amount_range():
    assert _pace_to_target_amount("3-4万") == (30000.0, 40000.0)
    assert _pace_to_target_amount("2-3万") == (20000.0, 30000.0)


def test_pace_to_target_amount_unparseable():
    assert _pace_to_target_amount("0") == (None, None)
    assert _pace_to_target_amount("观察") == (None, None)
    assert _pace_to_target_amount("ESPP 持续被动") == (None, None)
    assert _pace_to_target_amount("") == (None, None)
    assert _pace_to_target_amount(None) == (None, None)


def _seed_action_fixture(tmp_path):
    """3 个标的：核心已建仓不足、核心未建仓、不进池。"""
    import pandas as pd
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "ticker_map.json").write_text(json.dumps({
        "持仓": {
            "腾讯": {"portfolio_codes": ["HK700"], "yf_symbol": "00700.HK"},
        },
        "观察": {
            "中海油": {"portfolio_codes": ["00883.HK"], "yf_symbol": "00883.HK"},
            "阿里": {"portfolio_codes": [], "yf_symbol": "09988.HK"},
        },
    }, ensure_ascii=False), encoding='utf-8')
    # 腾讯：核心 7 万，已 4 万（待加 3 万）
    update_decision(str(tmp_path), "腾讯", "买入", "H股",
                    "2026-05-22", "成长", "x.md",
                    tier="核心", style="成长", target_position="7万",
                    pace="不再加仓")
    # 中海油：核心 5 万，未建仓
    update_decision(str(tmp_path), "中海油", "买入", "H股",
                    "2026-05-22", "高股息+周期", "x.md",
                    tier="核心", style="高股息+周期", target_position="5万",
                    pace="6 月分批")
    # 阿里：不进池
    update_decision(str(tmp_path), "阿里", "不感兴趣", "H股",
                    "2026-05-22", "Too Hard", "x.md",
                    tier="不进池", target_position="0")

    return pd.DataFrame([
        {"Date": "2026-05-22", "Code": "HK0700", "Total_Value": 40000.0, "Asset_Class": "ETF_Stock"},
    ])


def test_action_list_excludes_non_pool(tmp_path):
    """不进池/观察/战略持仓不出现在行动清单。"""
    df = _seed_action_fixture(tmp_path)
    actions = get_pool_action_list(str(tmp_path), raw_df=df)
    folders = [a['folder'] for a in actions]
    assert "腾讯" in folders
    assert "中海油" in folders
    assert "阿里" not in folders


def test_action_list_status(tmp_path):
    """status 字段正确：待加仓 / 已达标 / 超配。"""
    df = _seed_action_fixture(tmp_path)
    actions = get_pool_action_list(str(tmp_path), raw_df=df)
    txn = next(a for a in actions if a['folder'] == "腾讯")
    cnooc = next(a for a in actions if a['folder'] == "中海油")
    # 腾讯：4 万 < 7 万 → 待加仓
    assert txn['status'] == "待加仓"
    assert txn['gap_low'] == pytest.approx(30000.0)
    # 中海油：0 < 5 万 → 待加仓
    assert cnooc['status'] == "待加仓"
    assert cnooc['gap_low'] == pytest.approx(50000.0)


def test_action_list_status_overweight(tmp_path):
    """当前持仓 > target × 1.1 → 超配。"""
    import pandas as pd
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "ticker_map.json").write_text(json.dumps({
        "持仓": {"腾讯": {"portfolio_codes": ["HK700"], "yf_symbol": "00700.HK"}},
        "观察": {},
    }, ensure_ascii=False), encoding='utf-8')
    update_decision(str(tmp_path), "腾讯", "买入", "H股",
                    "2026-05-22", "成长", "x.md",
                    tier="核心", target_position="7万")
    df = pd.DataFrame([
        {"Date": "2026-05-22", "Code": "HK0700", "Total_Value": 80000.0, "Asset_Class": "ETF_Stock"},
    ])
    actions = get_pool_action_list(str(tmp_path), raw_df=df)
    txn = actions[0]
    # 8 万 > 7 万 × 1.1 = 7.7 万 → 超配
    assert txn['status'] == "超配"


def test_style_exposure_aggregates(tmp_path):
    """get_style_exposure 按 style 聚合。"""
    df = _seed_action_fixture(tmp_path)
    exp = get_style_exposure(str(tmp_path), raw_df=df)
    # 腾讯 4 万（成长），中海油 0（高股息+周期）
    assert exp['pool_by_style']['成长'] == pytest.approx(40000.0)
    assert exp['pool_by_style']['高股息+周期'] == pytest.approx(0.0)
    # 目标：成长 7 万，高股息+周期 5 万
    assert exp['pool_by_style_target']['成长'] == pytest.approx(70000.0)
    assert exp['pool_by_style_target']['高股息+周期'] == pytest.approx(50000.0)
    # 总计
    assert exp['total_pool_cny'] == pytest.approx(40000.0)
    assert exp['total_target_amount'] == pytest.approx(120000.0)
    assert exp['total_pool_target'] == pytest.approx(300000.0)
