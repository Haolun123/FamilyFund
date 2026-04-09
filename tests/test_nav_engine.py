import os
import sys
import pytest
import pandas as pd
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from nav_engine import (
    load_portfolio, validate_portfolio, compute_fund_nav,
    compute_class_nav, compute_allocation, compute_cost_basis,
    plot_fund_nav, plot_class_nav, plot_allocation_pie, export_results,
    _run_nav_calculation, _atomic_write_csv, update_snapshot, delete_snapshot,
    VALID_ASSET_CLASSES,
)


# ─── Fixtures ───

@pytest.fixture
def sample_csv(tmp_path):
    """3-week sample with 3 holdings across 3 asset classes."""
    content = (
        "Date,Asset_Class,Platform,Name,Code,Currency,Exchange_Rate,Shares,Current_Price,Total_Value,Net_Cash_Flow\n"
        # Week 1 — Day 0 (establishment)
        "2024-04-01,ETF_Stock,中信,红利低波,512890,CNY,1.0,1000,10,10000,10000\n"
        "2024-04-01,Fixed_Income,招商,周周宝,GD040219,CNY,1.0,50000,1.01,50500,50500\n"
        "2024-04-01,Cash,招商,现金,,CNY,1.0,20000,1,20000,20000\n"
        # Week 2 — normal (no cash flow, prices change)
        "2024-04-08,ETF_Stock,中信,红利低波,512890,CNY,1.0,1000,10.5,10500,0\n"
        "2024-04-08,Fixed_Income,招商,周周宝,GD040219,CNY,1.0,50000,1.012,50600,0\n"
        "2024-04-08,Cash,招商,现金,,CNY,1.0,20000,1,20000,0\n"
        # Week 3 — injection into Fixed_Income (+5000), withdrawal from Cash (-3000)
        "2024-04-15,ETF_Stock,中信,红利低波,512890,CNY,1.0,1000,10.3,10300,0\n"
        "2024-04-15,Fixed_Income,招商,周周宝,GD040219,CNY,1.0,54950,1.013,55634.35,5000\n"
        "2024-04-15,Cash,招商,现金,,CNY,1.0,17000,1,17000,-3000\n"
    )
    p = tmp_path / "portfolio.csv"
    p.write_text(content)
    return str(p)


@pytest.fixture
def sample_df(sample_csv):
    return load_portfolio(sample_csv)


@pytest.fixture
def fund_nav(sample_df):
    return compute_fund_nav(sample_df)


@pytest.fixture
def class_nav(sample_df):
    return compute_class_nav(sample_df)


@pytest.fixture
def allocation(sample_df):
    return compute_allocation(sample_df)


# ─── Loading & Validation ───

class TestLoadAndValidate:
    def test_load_returns_dataframe(self, sample_df):
        assert isinstance(sample_df, pd.DataFrame)
        assert len(sample_df) == 9

    def test_load_missing_file(self):
        assert load_portfolio("/nonexistent.csv") is None

    def test_validate_clean_data(self, sample_df):
        errors, warnings = validate_portfolio(sample_df)
        assert len(errors) == 0

    def test_validate_missing_column(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("Date,Name\n2024-01-01,Test\n")
        df = pd.read_csv(str(p))
        errors, _ = validate_portfolio(df)
        assert any("缺少必需列" in e for e in errors)

    def test_validate_invalid_asset_class(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text(
            "Date,Asset_Class,Platform,Name,Code,Currency,Exchange_Rate,Shares,Current_Price,Total_Value,Net_Cash_Flow\n"
            "2024-01-01,INVALID,X,Y,,CNY,1.0,1,1,1,1\n"
        )
        df = load_portfolio(str(p))
        errors, _ = validate_portfolio(df)
        assert any("无效的 Asset_Class" in e for e in errors)

    def test_validate_day0_warning(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text(
            "Date,Asset_Class,Platform,Name,Code,Currency,Exchange_Rate,Shares,Current_Price,Total_Value,Net_Cash_Flow\n"
            "2024-01-01,Cash,X,Y,,CNY,1.0,1000,1,1000,500\n"
        )
        df = load_portfolio(str(p))
        _, warnings = validate_portfolio(df)
        assert any("建仓日" in w for w in warnings)


# ─── Core NAV Algorithm ───

class TestCoreNav:
    def test_day0_nav_is_one(self):
        records = _run_nav_calculation(['2024-01-01'], [100000], [100000])
        assert records[0]['NAV'] == 1.0

    def test_day0_shares_equal_value(self):
        records = _run_nav_calculation(['2024-01-01'], [100000], [100000])
        assert records[0]['Total_Shares'] == 100000.0

    def test_no_cash_flow_nav_reflects_market(self):
        records = _run_nav_calculation(
            ['2024-01-01', '2024-01-08'],
            [100000, 105000],
            [100000, 0],
        )
        assert records[1]['NAV'] == round(105000 / 100000, 4)

    def test_cash_injection_increases_shares(self):
        records = _run_nav_calculation(
            ['2024-01-01', '2024-01-08'],
            [100000, 120000],
            [100000, 10000],
        )
        # NAV based on 120000 - 10000 = 110000 / 100000 shares = 1.1
        assert records[1]['NAV'] == 1.1
        # New shares = 10000 / 1.1 ≈ 9090.91
        expected_shares = 100000 + 10000 / 1.1
        assert abs(records[1]['Total_Shares'] - round(expected_shares, 2)) < 0.01

    def test_cash_withdrawal_decreases_shares(self):
        records = _run_nav_calculation(
            ['2024-01-01', '2024-01-08'],
            [100000, 95000],
            [100000, -5000],
        )
        # NAV = (95000 - (-5000)) / 100000 = 1.0
        assert records[1]['NAV'] == 1.0
        # Shares = 100000 + (-5000 / 1.0) = 95000
        assert records[1]['Total_Shares'] == 95000.0


# ─── Fund-Level NAV ───

class TestFundNav:
    def test_day0_nav(self, fund_nav):
        assert fund_nav.iloc[0]['NAV'] == 1.0

    def test_day0_return(self, fund_nav):
        assert fund_nav.iloc[0]['Cumulative_Return(%)'] == 0.0

    def test_day0_total_value(self, fund_nav):
        # 10000 + 50500 + 20000 = 80500
        assert fund_nav.iloc[0]['Total_Value'] == 80500.0

    def test_three_periods(self, fund_nav):
        assert len(fund_nav) == 3

    def test_week2_no_cash_flow(self, fund_nav):
        assert fund_nav.iloc[1]['Net_Cash_Flow'] == 0.0

    def test_week3_net_cash_flow(self, fund_nav):
        # +5000 - 3000 = 2000
        assert fund_nav.iloc[2]['Net_Cash_Flow'] == 2000.0


# ─── Per-Class NAV ───

class TestClassNav:
    def test_all_classes_present(self, class_nav):
        assert set(class_nav.keys()) == {'ETF_Stock', 'Fixed_Income', 'Cash'}

    def test_each_class_day0_nav(self, class_nav):
        for cls, nav_df in class_nav.items():
            assert nav_df.iloc[0]['NAV'] == 1.0, f"{cls} Day 0 NAV != 1.0"

    def test_etf_stock_nav_independent(self, class_nav):
        """Cash injection into Fixed_Income should NOT affect ETF_Stock's NAV."""
        etf = class_nav['ETF_Stock']
        # Week 3: ETF value=10300, NCF=0 → NAV = 10300 / (10000 shares adjusted for week 2)
        # Week 2 shares: 10000 (unchanged, NCF=0)
        # Week 2 NAV: 10500/10000 = 1.05
        # Week 3 NAV: 10300/10000 = 1.03
        assert etf.iloc[2]['NAV'] == round(10300 / 10000, 4)

    def test_fixed_income_cash_flow(self, class_nav):
        """Fixed_Income received +5000 in week 3 — should change shares, not NAV."""
        fi = class_nav['Fixed_Income']
        # Week 2: NAV = 50600 / 50500 = 1.00198...
        week2_nav = fi.iloc[1]['NAV']
        # Week 3: value=55634.35, NCF=5000
        # real_value = 55634.35 - 5000 = 50634.35
        # NAV = 50634.35 / week2_shares
        # Week 2 shares = 50500 (no NCF in week 2)
        expected_nav = round((55634.35 - 5000) / 50500, 4)
        assert fi.iloc[2]['NAV'] == expected_nav

    def test_cash_withdrawal(self, class_nav):
        """Cash had -3000 withdrawal in week 3."""
        cash = class_nav['Cash']
        # Week 2: NAV = 20000/20000 = 1.0
        # Week 3: value=17000, NCF=-3000 → real = 17000-(-3000) = 20000 → NAV = 1.0
        assert cash.iloc[2]['NAV'] == 1.0
        # Shares should decrease: 20000 + (-3000/1.0) = 17000
        assert cash.iloc[2]['Total_Shares'] == 17000.0

    def test_class_nav_has_asset_class_column(self, class_nav):
        for cls, nav_df in class_nav.items():
            assert 'Asset_Class' in nav_df.columns
            assert (nav_df['Asset_Class'] == cls).all()


# ─── Allocation ───

class TestAllocation:
    def test_sums_to_one(self, allocation):
        total = allocation['Allocation_Percent'].sum()
        assert abs(total - 1.0) < 0.01

    def test_correct_classes(self, allocation):
        assert set(allocation['Asset_Class']) == {'ETF_Stock', 'Fixed_Income', 'Cash'}

    def test_has_display_names(self, allocation):
        assert 'Display_Name' in allocation.columns


# ─── Outputs ───

class TestOutputs:
    def test_fund_nav_chart(self, fund_nav, tmp_path):
        p = str(tmp_path / "fund.png")
        plot_fund_nav(fund_nav, output_path=p)
        assert os.path.exists(p)
        assert os.path.getsize(p) > 0

    def test_class_nav_chart(self, class_nav, tmp_path):
        p = str(tmp_path / "class.png")
        plot_class_nav(class_nav, output_path=p)
        assert os.path.exists(p)
        assert os.path.getsize(p) > 0

    def test_allocation_pie(self, allocation, tmp_path):
        p = str(tmp_path / "pie.png")
        plot_allocation_pie(allocation, output_path=p)
        assert os.path.exists(p)
        assert os.path.getsize(p) > 0

    def test_csv_export(self, fund_nav, class_nav, allocation, tmp_path):
        fp = str(tmp_path / "fund.csv")
        cp = str(tmp_path / "class.csv")
        ap = str(tmp_path / "alloc.csv")
        export_results(fund_nav, class_nav, allocation,
                       fund_path=fp, class_path=cp, alloc_path=ap)
        assert os.path.exists(fp)
        assert os.path.exists(cp)
        assert os.path.exists(ap)

        fund_df = pd.read_csv(fp)
        assert 'NAV' in fund_df.columns

        class_df = pd.read_csv(cp)
        assert 'Asset_Class' in class_df.columns


# ─── End-to-End ───

class TestEndToEnd:
    def test_full_run_on_sample(self, tmp_path):
        """Run the full pipeline on portfolio_sample.csv."""
        sample = os.path.join(os.path.dirname(__file__), '..', 'data', 'portfolio_sample.csv')
        if not os.path.exists(sample):
            pytest.skip("portfolio_sample.csv not found")

        df = load_portfolio(sample)
        assert df is not None

        errors, _ = validate_portfolio(df)
        assert len(errors) == 0

        fund_nav = compute_fund_nav(df)
        class_nav = compute_class_nav(df)
        allocation = compute_allocation(df)

        # Fund-level checks
        assert fund_nav.iloc[0]['NAV'] == 1.0
        assert len(fund_nav) == 3  # 3 weeks
        assert all(fund_nav['NAV'] > 0)

        # All 7 asset classes present
        assert len(class_nav) == 7
        for cls in class_nav:
            assert cls in VALID_ASSET_CLASSES

        # Allocation sums to 1
        assert abs(allocation['Allocation_Percent'].sum() - 1.0) < 0.01

    def test_fund_nav_matches_manual_aggregate(self, sample_csv):
        """Cross-check: fund NAV should equal manual aggregate calculation."""
        df = load_portfolio(sample_csv)
        fund_nav = compute_fund_nav(df)

        # Manually aggregate Day 0: TV=80500, NCF=80500 → NAV=1.0
        assert fund_nav.iloc[0]['NAV'] == 1.0

        # Week 2: TV = 10500+50600+20000 = 81100, NCF=0
        # NAV = 81100 / 80500 = 1.00745...
        expected_w2 = round(81100 / 80500, 4)
        assert fund_nav.iloc[1]['NAV'] == expected_w2

        # Week 3: TV = 10300+55634.35+17000 = 82934.35, NCF = 0+5000+(-3000) = 2000
        # real_value = 82934.35 - 2000 = 80934.35
        # shares_after_w2 = 80500 (no NCF in w2)
        # NAV = 80934.35 / 80500 = 1.00539...
        expected_w3 = round(80934.35 / 80500, 4)
        assert fund_nav.iloc[2]['NAV'] == expected_w3


# ─── Cost Basis & P/L ───

class TestCostBasis:
    def test_day0_cost_equals_value(self, sample_df):
        """On Day 0 only, cost basis = total value (NCF == TV)."""
        # Use only Day 0 data
        day0 = sample_df[sample_df['Date'] == '2024-04-01']
        cb = compute_cost_basis(day0)
        for _, row in cb.iterrows():
            assert abs(row['Cost_Basis'] - row['Market_Value']) < 0.01
            assert abs(row['Profit_Loss']) < 0.01

    def test_no_cashflow_pl_reflects_market(self, sample_df):
        """With NCF=0 in week 2, P/L reflects price change from Day 0."""
        # Use weeks 1-2 only (no NCF in week 2)
        w1w2 = sample_df[sample_df['Date'] <= '2024-04-08']
        cb = compute_cost_basis(w1w2)
        etf = cb[cb['Name'] == '红利低波']
        assert len(etf) == 1
        etf_row = etf.iloc[0]
        # Cost = 10000 (Day 0 NCF), Market = 10500 (week 2 TV)
        assert etf_row['Cost_Basis'] == 10000.0
        assert etf_row['Market_Value'] == 10500.0
        assert etf_row['Profit_Loss'] == 500.0
        assert abs(etf_row['Profit_Loss_Rate'] - 5.0) < 0.01

    def test_cash_injection_increases_cost_basis(self, sample_df):
        """Cash injection (NCF > 0) adds to cost basis."""
        cb = compute_cost_basis(sample_df)
        fi = cb[cb['Name'] == '周周宝']
        assert len(fi) == 1
        fi_row = fi.iloc[0]
        # Cost = 50500 (Day 0) + 5000 (week 3) = 55500
        assert fi_row['Cost_Basis'] == 55500.0

    def test_cash_withdrawal_decreases_cost_basis(self, sample_df):
        """Cash withdrawal (NCF < 0) reduces cost basis."""
        cb = compute_cost_basis(sample_df)
        cash = cb[cb['Name'] == '现金']
        assert len(cash) == 1
        cash_row = cash.iloc[0]
        # Cost = 20000 (Day 0) + (-3000) (week 3) = 17000
        assert cash_row['Cost_Basis'] == 17000.0

    def test_all_holdings_present(self, sample_df):
        """Cost basis should cover all holdings in latest snapshot."""
        cb = compute_cost_basis(sample_df)
        assert len(cb) == 3  # 红利低波, 周周宝, 现金


# ─── File Locking & Snapshot Operations ───

class TestFileOperations:
    def test_atomic_write(self, sample_csv):
        """_atomic_write_csv should write CSV atomically."""
        df = load_portfolio(sample_csv)
        new_path = sample_csv.replace('.csv', '_copy.csv')
        _atomic_write_csv(df, new_path)
        reloaded = pd.read_csv(new_path)
        assert len(reloaded) == len(df)

    def test_delete_snapshot(self, sample_csv):
        """delete_snapshot removes all rows for a date."""
        delete_snapshot(sample_csv, '2024-04-08')
        df = load_portfolio(sample_csv)
        assert '2024-04-08' not in df['Date'].values
        assert len(df) == 6  # 9 - 3 rows removed

    def test_update_snapshot(self, sample_csv):
        """update_snapshot replaces rows for a date."""
        df = load_portfolio(sample_csv)
        week2 = df[df['Date'] == '2024-04-08'].drop(columns=['Date']).copy()
        week2.loc[week2['Name'] == '红利低波', 'Total_Value'] = 99999.0
        update_snapshot(sample_csv, '2024-04-08', week2)
        reloaded = load_portfolio(sample_csv)
        updated_row = reloaded[(reloaded['Date'] == '2024-04-08') & (reloaded['Name'] == '红利低波')]
        assert updated_row.iloc[0]['Total_Value'] == 99999.0
