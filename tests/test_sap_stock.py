import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from sap_stock import (
    load_own_sap, load_move_sap,
    own_sap_summary, move_sap_summary,
    compute_sap_cost_basis,
)


# ─── Fixtures ───

@pytest.fixture
def own_sap_csv(tmp_path):
    """Small Own SAP dataset for testing."""
    content = (
        "Date,Activity,Price_EUR,Quantity,Discount_Ratio,CNY,Cost_CNY\n"
        "2024-01-05,Match,200.0,1.0,0.25,1600.0,400.0\n"
        "2024-01-05,Purchase,200.0,2.5,0.75,4000.0,3000.0\n"
        "2024-02-05,Match,210.0,0.95,0.25,1600.0,400.0\n"
        "2024-02-05,Purchase,210.0,2.38,0.75,4000.0,3000.0\n"
        "2024-05-20,Dividend,220.0,0.5,1.0,880.0,880.0\n"
        "2024-06-15,Sell,230.0,-2.0,1.0,-3680.0,-3680.0\n"
    )
    p = tmp_path / "own_sap.csv"
    p.write_text(content)
    return str(p)


@pytest.fixture
def move_sap_csv(tmp_path):
    """Small Move SAP dataset for testing."""
    content = (
        "Date,Activity,Price_EUR,Quantity,FX_Rate,CNY\n"
        "2024-03-11,Award,180.0,6.0,8.0,8640.0\n"
        "2024-03-11,Award,180.0,4.0,8.0,5760.0\n"
        "2024-05-20,Dividend,190.0,0.3,8.0,456.0\n"
    )
    p = tmp_path / "move_sap.csv"
    p.write_text(content)
    return str(p)


# ─── Load ───

class TestLoad:
    def test_load_own_sap(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        assert df is not None
        assert len(df) == 6

    def test_load_move_sap(self, move_sap_csv):
        df = load_move_sap(move_sap_csv)
        assert df is not None
        assert len(df) == 3

    def test_load_missing_file(self):
        assert load_own_sap("/nonexistent.csv") is None
        assert load_move_sap("/nonexistent.csv") is None


# ─── Own SAP Summary ───

class TestOwnSapSummary:
    def test_total_shares(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        s = own_sap_summary(df, fx_rate=8.0)
        # 1.0 + 2.5 + 0.95 + 2.38 + 0.5 - 2.0 = 5.33
        assert s['total_shares'] == 5.33

    def test_total_cost(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        s = own_sap_summary(df, fx_rate=8.0)
        # 400 + 3000 + 400 + 3000 + 880 - 3680 = 4000
        assert s['total_cost'] == 4000.0

    def test_avg_cost(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        s = own_sap_summary(df, fx_rate=8.0)
        assert s['avg_cost_cny'] == round(4000.0 / 5.33, 2)

    def test_break_even(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        s = own_sap_summary(df, fx_rate=8.0)
        expected_be = round(s['avg_cost_cny'] / 8.0, 2)
        assert s['break_even_eur'] == expected_be

    def test_break_even_no_fx(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        s = own_sap_summary(df)
        assert s['break_even_eur'] is None


# ─── Move SAP Summary ───

class TestMoveSapSummary:
    def test_total_shares(self, move_sap_csv):
        df = load_move_sap(move_sap_csv)
        s = move_sap_summary(df, fx_rate=8.0)
        # 6.0 + 4.0 + 0.3 = 10.3
        assert s['total_shares'] == 10.3

    def test_total_cost(self, move_sap_csv):
        df = load_move_sap(move_sap_csv)
        s = move_sap_summary(df, fx_rate=8.0)
        # 8640 + 5760 + 456 = 14856
        assert s['total_cost'] == 14856.0


# ─── Own SAP Edge Cases ───

class TestOwnSapEdgeCases:
    def test_sell_reduces_shares(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        sell_rows = df[df['Activity'] == 'Sell']
        assert len(sell_rows) == 1
        assert sell_rows.iloc[0]['Quantity'] == -2.0

    def test_dividend_full_cost(self, own_sap_csv):
        df = load_own_sap(own_sap_csv)
        div_rows = df[df['Activity'] == 'Dividend']
        assert len(div_rows) == 1
        row = div_rows.iloc[0]
        assert row['Discount_Ratio'] == 1.0
        assert row['Cost_CNY'] == row['CNY']


# ─── Combined Cost Basis ───

class TestCombinedCostBasis:
    def test_compute_sap_cost_basis(self, own_sap_csv, move_sap_csv):
        result = compute_sap_cost_basis(own_sap_csv, move_sap_csv)
        assert 'own_sap' in result
        assert 'move_sap' in result
        assert result['own_sap']['total_cost'] == 4000.0
        assert result['own_sap']['total_shares'] == 5.33
        assert result['move_sap']['total_cost'] == 14856.0
        assert result['move_sap']['total_shares'] == 10.3

    def test_compute_with_only_own(self, own_sap_csv):
        result = compute_sap_cost_basis(own_csv=own_sap_csv)
        assert 'own_sap' in result
        assert 'move_sap' not in result

    def test_compute_with_only_move(self, move_sap_csv):
        result = compute_sap_cost_basis(move_csv=move_sap_csv)
        assert 'own_sap' not in result
        assert 'move_sap' in result

    def test_compute_no_files(self):
        result = compute_sap_cost_basis()
        assert result == {}


# ─── Integration with nav_engine ───

class TestNavEngineIntegration:
    def test_cost_basis_overrides_company_stock(self, own_sap_csv, move_sap_csv, tmp_path):
        """compute_cost_basis should use SAP data for Company_Stock rows."""
        from nav_engine import load_portfolio, compute_cost_basis

        # Create a minimal portfolio with Company_Stock rows
        content = (
            "Date,Asset_Class,Platform,Name,Code,Currency,Exchange_Rate,Shares,Current_Price,Total_Value,Net_Cash_Flow\n"
            "2024-01-01,Company_Stock,SAP,Own SAP,,EUR,8.0,5.0,200.0,8000.0,0\n"
            "2024-01-01,Company_Stock,SAP,Move SAP,,EUR,8.0,10.0,180.0,14400.0,0\n"
            "2024-01-01,Cash,Bank,Cash,,CNY,1.0,50000,1,50000,50000\n"
        )
        p = tmp_path / "portfolio.csv"
        p.write_text(content)
        df = load_portfolio(str(p))

        cb = compute_cost_basis(df, own_sap_csv=own_sap_csv, move_sap_csv=move_sap_csv)

        own_row = cb[cb['Name'] == 'Own SAP']
        assert len(own_row) == 1
        assert own_row.iloc[0]['Cost_Basis'] == 4000.0

        move_row = cb[cb['Name'] == 'Move SAP']
        assert len(move_row) == 1
        assert move_row.iloc[0]['Cost_Basis'] == 14856.0

    def test_cost_basis_without_sap(self, tmp_path):
        """Without SAP CSVs, Company_Stock uses regular NCF-based cost."""
        from nav_engine import load_portfolio, compute_cost_basis

        content = (
            "Date,Asset_Class,Platform,Name,Code,Currency,Exchange_Rate,Shares,Current_Price,Total_Value,Net_Cash_Flow\n"
            "2024-01-01,Cash,Bank,Cash,,CNY,1.0,50000,1,50000,50000\n"
        )
        p = tmp_path / "portfolio.csv"
        p.write_text(content)
        df = load_portfolio(str(p))

        cb = compute_cost_basis(df)
        assert len(cb) == 1
        assert cb.iloc[0]['Cost_Basis'] == 50000.0


# ─── XLSX Import (if CurrentAsset.xlsx exists) ───

class TestXlsxImport:
    @pytest.fixture
    def xlsx_path(self):
        p = os.path.join(os.path.dirname(__file__), '..', 'CurrentAsset.xlsx')
        if not os.path.exists(p):
            pytest.skip("CurrentAsset.xlsx not found")
        return p

    def test_import_own_sap_totals(self, xlsx_path, tmp_path):
        from import_sap_xlsx import import_own_sap
        df = import_own_sap(xlsx_path)
        assert len(df) == 132
        assert abs(df['Quantity'].sum() - 199.3838) < 0.01
        # Allow small rounding diff from per-row rounding
        assert abs(df['Cost_CNY'].sum() - 107413.12) < 1.0

    def test_import_move_sap_totals(self, xlsx_path, tmp_path):
        from import_sap_xlsx import import_move_sap
        df = import_move_sap(xlsx_path)
        assert len(df) == 43
        assert abs(df['Quantity'].sum() - 207.7744) < 0.01
        assert abs(df['CNY'].sum() - 316658.65) < 1.0

    def test_own_sap_activities(self, xlsx_path):
        from import_sap_xlsx import import_own_sap
        df = import_own_sap(xlsx_path)
        activities = set(df['Activity'].unique())
        assert activities == {'Match', 'Purchase', 'Dividend', 'Sell'}

    def test_move_sap_activities(self, xlsx_path):
        from import_sap_xlsx import import_move_sap
        df = import_move_sap(xlsx_path)
        activities = set(df['Activity'].unique())
        assert activities == {'Award', 'Dividend'}
