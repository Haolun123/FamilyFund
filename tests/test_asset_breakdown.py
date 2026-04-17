import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from asset_breakdown import (
    parse_asset_xlsx,
    classify_assets,
    compute_summary,
    generate_pie_chart,
    export_breakdown_csv,
    export_summary_csv,
    CLASS_DISPLAY_NAMES,
)


# --- Fixtures ---

@pytest.fixture(scope="module")
def xlsx_path():
    """Path to the real CurrentAsset.xlsx."""
    path = os.path.join(os.path.dirname(__file__), '..', 'CurrentAsset.xlsx')
    if not os.path.exists(path):
        pytest.skip("CurrentAsset.xlsx not found")
    return path


@pytest.fixture(scope="module")
def holdings(xlsx_path):
    return parse_asset_xlsx(xlsx_path)


@pytest.fixture(scope="module")
def classified(holdings):
    return classify_assets(holdings)


@pytest.fixture(scope="module")
def summary(classified):
    return compute_summary(classified)


# --- Parsing Tests ---

class TestParsing:
    def test_returns_list(self, holdings):
        assert isinstance(holdings, list)

    def test_holdings_not_empty(self, holdings):
        assert len(holdings) > 0

    def test_holding_has_required_fields(self, holdings):
        required = {'platform', 'name', 'total_cost', 'current_value', 'total_value', 'pnl_amount'}
        for h in holdings:
            assert required.issubset(h.keys()), f"Missing fields in {h['name']}: {required - h.keys()}"

    def test_missing_file_returns_none(self):
        result = parse_asset_xlsx("/nonexistent/path.xlsx")
        assert result is None

    def test_known_holdings_present(self, holdings):
        """Check that key holdings from the XLSX are parsed."""
        names = {h['name'] for h in holdings}
        expected = {'红利低波ETF', 'SAP', '周周宝', '现金（中行）', '现金（招行）'}
        for exp in expected:
            assert exp in names, f"Expected holding '{exp}' not found. Got: {names}"


# --- Classification Tests ---

class TestClassification:
    def test_all_classes_are_valid(self, classified):
        for cls in classified:
            assert cls in CLASS_DISPLAY_NAMES

    def test_us_blend_fund(self, classified):
        # 标普场外 → US_Blend_Fund
        assert len(classified['US_Blend_Fund']) > 0
        platforms = {h['platform'] for h in classified['US_Blend_Fund']}
        assert '标普场外' in platforms

    def test_us_growth_fund(self, classified):
        # 纳指场外 → US_Growth_Fund
        assert len(classified['US_Growth_Fund']) > 0
        platforms = {h['platform'] for h in classified['US_Growth_Fund']}
        assert '纳指场外' in platforms

    def test_cash_holdings(self, classified):
        names = {h['name'] for h in classified['Cash']}
        assert '现金（中行）' in names
        assert '现金（招行）' in names
        assert '现金' in names  # 中信证券现金

    def test_gold_includes_physical(self, classified):
        platforms = {h['platform'] for h in classified['Gold']}
        assert '实物' in platforms, "Physical gold should be in Gold class"

    def test_gold_includes_financial(self, classified):
        names = {h['name'] for h in classified['Gold']}
        assert '黄金' in names

    def test_sap_in_company_stock(self, classified):
        names = {h['name'] for h in classified['Company_Stock']}
        assert 'SAP' in names

    def test_fixed_income_contains_bonds(self, classified):
        names = {h['name'] for h in classified['Fixed_Income']}
        assert '周周宝' in names
        assert '招银招睿' in names

    def test_no_holding_lost(self, holdings, classified):
        """Every parsed holding should end up in some class."""
        total_classified = sum(len(v) for v in classified.values())
        assert total_classified == len(holdings)


# --- Summary Tests ---

class TestSummary:
    def test_summary_has_entries(self, summary):
        assert len(summary) > 0

    def test_allocation_sums_to_one(self, summary):
        total_alloc = sum(s['Allocation_Percent'] for s in summary)
        assert abs(total_alloc - 1.0) < 0.01, f"Allocation sums to {total_alloc}, expected ~1.0"

    def test_grand_total_reasonable(self, summary):
        """Total assets should be in a reasonable range (> 1M RMB based on the XLSX)."""
        grand = sum(s['Total_Value'] for s in summary)
        assert grand > 1_000_000, f"Grand total {grand} seems too low"

    def test_pnl_sign_consistency(self, summary):
        """PnL amount and percent should have the same sign."""
        for s in summary:
            if s['Total_Cost'] > 0 and s['PnL_Amount'] != 0:
                assert (s['PnL_Amount'] > 0) == (s['PnL_Percent'] > 0), \
                    f"Sign mismatch for {s['Asset_Class']}: amount={s['PnL_Amount']}, pct={s['PnL_Percent']}"


# --- Output Tests ---

class TestOutputs:
    def test_breakdown_csv(self, classified, tmp_path):
        path = str(tmp_path / "breakdown.csv")
        df = export_breakdown_csv(classified, output_path=path)
        assert os.path.exists(path)
        expected_cols = ['Asset_Class', 'Platform', 'Name', 'Code', 'Cost_Price',
                         'Current_Price', 'Shares', 'Total_Cost', 'Current_Value',
                         'Pending_Amount', 'Total_Value', 'PnL_Amount', 'PnL_Percent']
        assert list(df.columns) == expected_cols

    def test_summary_csv(self, summary, tmp_path):
        path = str(tmp_path / "summary.csv")
        df = export_summary_csv(summary, output_path=path)
        assert os.path.exists(path)
        assert 'Asset_Class' in df.columns
        assert 'Allocation_Percent' in df.columns

    def test_pie_chart(self, summary, tmp_path):
        path = str(tmp_path / "pie.png")
        generate_pie_chart(summary, output_path=path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0
