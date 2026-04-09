import os
import tempfile
import pytest
import pandas as pd

# Add src to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fund_calculator import calculate_nav, plot_nav_trend


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample input CSV matching the architecture data contract."""
    csv_content = (
        "Date,Total_Market_Value,Net_Cash_Flow\n"
        "2024-04-01,3600000,3600000\n"
        "2024-04-08,3650000,0\n"
        "2024-04-15,3850000,233000\n"
        "2024-04-22,3820000,-50000\n"
    )
    csv_path = tmp_path / "input.csv"
    csv_path.write_text(csv_content)
    return str(csv_path)


@pytest.fixture
def result_df(sample_csv, monkeypatch):
    """Run calculate_nav on sample data and return the DataFrame."""
    # Redirect output CSV to temp dir
    output_csv = os.path.join(os.path.dirname(sample_csv), "output.csv")
    monkeypatch.setattr("fund_calculator.OUTPUT_CSV", output_csv)
    df = calculate_nav(input_csv=sample_csv)
    return df


class TestDay0Initialization:
    """Test Day 0 (fund establishment) logic."""

    def test_nav_is_one(self, result_df):
        assert result_df.iloc[0]['NAV'] == 1.0

    def test_shares_equal_total_market_value(self, result_df):
        row = result_df.iloc[0]
        assert row['Total_Shares'] == row['Total_Market_Value']

    def test_cumulative_return_is_zero(self, result_df):
        assert result_df.iloc[0]['Cumulative_Return(%)'] == 0.0


class TestNoCashFlow:
    """Test a normal period with zero cash flow — NAV should reflect pure market movement."""

    def test_nav_reflects_market_change(self, result_df):
        # Week 2: TMV went from 3,600,000 to 3,650,000 with no cash flow
        nav = result_df.iloc[1]['NAV']
        expected = 3650000 / 3600000
        assert abs(nav - round(expected, 4)) < 0.0001

    def test_shares_unchanged(self, result_df):
        # No cash flow => shares should not change
        assert result_df.iloc[1]['Total_Shares'] == result_df.iloc[0]['Total_Shares']


class TestCashInjection:
    """Test period with positive cash flow (external money in)."""

    def test_nav_excludes_injection(self, result_df):
        # Week 3: TMV=3,850,000, NCF=233,000 => real value = 3,617,000
        row = result_df.iloc[2]
        prev_shares = result_df.iloc[1]['Total_Shares']
        expected_nav = (3850000 - 233000) / prev_shares
        assert abs(row['NAV'] - round(expected_nav, 4)) < 0.0001

    def test_shares_increase(self, result_df):
        # Cash injection should increase total shares
        assert result_df.iloc[2]['Total_Shares'] > result_df.iloc[1]['Total_Shares']


class TestCashWithdrawal:
    """Test period with negative cash flow (money taken out)."""

    def test_nav_excludes_withdrawal(self, result_df):
        # Week 4: TMV=3,820,000, NCF=-50,000 => real value = 3,870,000
        row = result_df.iloc[3]
        prev_shares = result_df.iloc[2]['Total_Shares']
        expected_nav = (3820000 - (-50000)) / prev_shares
        assert abs(row['NAV'] - round(expected_nav, 4)) < 0.0001

    def test_shares_decrease(self, result_df):
        # Withdrawal should reduce total shares
        assert result_df.iloc[3]['Total_Shares'] < result_df.iloc[2]['Total_Shares']


class TestCumulativeReturn:
    """Test cumulative return calculation."""

    def test_return_matches_nav(self, result_df):
        for _, row in result_df.iterrows():
            expected = round((row['NAV'] - 1.0) * 100, 2)
            assert row['Cumulative_Return(%)'] == expected


class TestInputValidation:
    """Test edge cases and error handling."""

    def test_missing_input_file(self, tmp_path, monkeypatch):
        output_csv = str(tmp_path / "output.csv")
        monkeypatch.setattr("fund_calculator.OUTPUT_CSV", output_csv)
        result = calculate_nav(input_csv=str(tmp_path / "nonexistent.csv"))
        assert result is None

    def test_single_row(self, tmp_path, monkeypatch):
        """Single row (Day 0 only) should work."""
        csv_content = "Date,Total_Market_Value,Net_Cash_Flow\n2024-01-01,1000000,1000000\n"
        csv_path = tmp_path / "single.csv"
        csv_path.write_text(csv_content)
        output_csv = str(tmp_path / "output.csv")
        monkeypatch.setattr("fund_calculator.OUTPUT_CSV", output_csv)
        df = calculate_nav(input_csv=str(csv_path))
        assert len(df) == 1
        assert df.iloc[0]['NAV'] == 1.0


class TestOutputFiles:
    """Test that output files are generated correctly."""

    def test_output_csv_created(self, sample_csv, tmp_path, monkeypatch):
        output_csv = str(tmp_path / "output.csv")
        monkeypatch.setattr("fund_calculator.OUTPUT_CSV", output_csv)
        calculate_nav(input_csv=sample_csv)
        assert os.path.exists(output_csv)

    def test_output_csv_columns(self, sample_csv, tmp_path, monkeypatch):
        output_csv = str(tmp_path / "output.csv")
        monkeypatch.setattr("fund_calculator.OUTPUT_CSV", output_csv)
        calculate_nav(input_csv=sample_csv)
        df = pd.read_csv(output_csv)
        expected_cols = ['Date', 'Total_Market_Value', 'Net_Cash_Flow', 'NAV', 'Total_Shares', 'Cumulative_Return(%)']
        assert list(df.columns) == expected_cols

    def test_chart_generated(self, sample_csv, tmp_path, monkeypatch):
        output_csv = str(tmp_path / "output.csv")
        chart_path = str(tmp_path / "chart.png")
        monkeypatch.setattr("fund_calculator.OUTPUT_CSV", output_csv)
        df = calculate_nav(input_csv=sample_csv)
        plot_nav_trend(df, output_chart=chart_path)
        assert os.path.exists(chart_path)
        assert os.path.getsize(chart_path) > 0


class TestEndToEnd:
    """Integration test using the full sample data from the project."""

    def test_full_run(self, tmp_path, monkeypatch):
        """Run on the project's actual sample data."""
        project_input = os.path.join(os.path.dirname(__file__), '..', 'data', 'input_fund_data.csv')
        if not os.path.exists(project_input):
            pytest.skip("Project sample data not found")

        output_csv = str(tmp_path / "output.csv")
        chart_path = str(tmp_path / "chart.png")
        monkeypatch.setattr("fund_calculator.OUTPUT_CSV", output_csv)

        df = calculate_nav(input_csv=project_input)
        assert df is not None
        assert len(df) == 12  # 12 rows in sample data

        # NAV should be positive and reasonable
        assert all(df['NAV'] > 0)
        assert all(df['NAV'] < 10)  # sanity check

        # Day 0 invariants
        assert df.iloc[0]['NAV'] == 1.0
        assert df.iloc[0]['Cumulative_Return(%)'] == 0.0

        # Chart generation
        plot_nav_trend(df, output_chart=chart_path)
        assert os.path.exists(chart_path)
