"""test_fi_engine.py — 财务独立 & 储蓄率引擎单元测试"""
import os
import sys
import json
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def sample_raw_df():
    rows = [
        {'Date': '2026-04-10', 'Asset_Class': 'Gold',         'Platform': 'test', 'Name': '黄金', 'Code': 'GOLD', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 100, 'Current_Price': 500, 'Total_Value': 50000,  'Net_Cash_Flow': 50000},
        {'Date': '2026-04-10', 'Asset_Class': 'CN_Index_Fund','Platform': 'test', 'Name': 'A500', 'Code': 'A500', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 200, 'Current_Price': 250, 'Total_Value': 50000,  'Net_Cash_Flow': 50000},
        {'Date': '2026-04-10', 'Asset_Class': 'Cash',         'Platform': 'test', 'Name': '现金', 'Code': 'CASH', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 1,   'Current_Price': 1,   'Total_Value': 100000, 'Net_Cash_Flow': 100000},
        {'Date': '2026-04-17', 'Asset_Class': 'Gold',         'Platform': 'test', 'Name': '黄金', 'Code': 'GOLD', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 100, 'Current_Price': 550, 'Total_Value': 55000,  'Net_Cash_Flow': 0},
        {'Date': '2026-04-17', 'Asset_Class': 'CN_Index_Fund','Platform': 'test', 'Name': 'A500', 'Code': 'A500', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 200, 'Current_Price': 240, 'Total_Value': 48000,  'Net_Cash_Flow': 0},
        {'Date': '2026-04-17', 'Asset_Class': 'Cash',         'Platform': 'test', 'Name': '现金', 'Code': 'CASH', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 1,   'Current_Price': 1,   'Total_Value': 100000, 'Net_Cash_Flow': 0},
    ]
    return pd.DataFrame(rows)

class TestFiEngine:

    def test_fi_target(self):
        from fi_engine import compute_fi_target
        assert compute_fi_target(200000, 0.04) == pytest.approx(5000000)
        assert compute_fi_target(200000, 0.03) == pytest.approx(6666666, rel=1e-3)

    def test_years_to_fi_already_reached(self):
        from fi_engine import compute_years_to_fi
        assert compute_years_to_fi(6000000, 5000000, 15000, 0.06) == 0.0

    def test_years_to_fi_basic(self):
        from fi_engine import compute_years_to_fi
        years = compute_years_to_fi(500000, 5000000, 15000, 0.06)
        assert years is not None
        assert 10 < years < 20

    def test_years_to_fi_impossible(self):
        from fi_engine import compute_years_to_fi
        # 零储蓄、零收益，永远达不到
        result = compute_years_to_fi(0, 5000000, 0, 0.0)
        assert result is None

    def test_sensitivity_has_five_scenarios(self):
        from fi_engine import fi_sensitivity
        rows = fi_sensitivity(500000, 5000000, 15000, 0.06)
        assert len(rows) == 5
        labels = [r['label'] for r in rows]
        assert '基准' in labels
        assert '收益率+1%' in labels
        assert '储蓄-20%' in labels

    def test_sensitivity_ordering(self):
        """更高收益率 → 更少年数"""
        from fi_engine import fi_sensitivity
        rows = {r['label']: r for r in fi_sensitivity(500000, 5000000, 15000, 0.06)}
        assert rows['收益率+1%']['years'] < rows['基准']['years']
        assert rows['收益率-1%']['years'] > rows['基准']['years']

    def test_monthly_savings_extraction(self, sample_raw_df):
        from fi_engine import compute_monthly_savings
        result = compute_monthly_savings(sample_raw_df)
        # sample_raw_df 有 Cash NCF=100000 在 2026-04
        assert '2026-04' in result
        assert result['2026-04'] == pytest.approx(100000)

    def test_savings_rate(self):
        from fi_engine import compute_savings_rate
        monthly = {'2026-04': 15000, '2026-05': 20000}
        rates = compute_savings_rate(monthly, 50000)
        assert rates['2026-04'] == pytest.approx(0.30)
        assert rates['2026-05'] == pytest.approx(0.40)

    def test_savings_rate_zero_income(self):
        from fi_engine import compute_savings_rate
        assert compute_savings_rate({'2026-04': 10000}, 0) == {}

    def test_load_save_config(self, tmp_dir):
        from fi_engine import load_fi_config, save_fi_config
        cfg = load_fi_config(tmp_dir)
        assert cfg['withdrawal_rate'] == 0.04

        cfg['monthly_income_cny'] = 50000
        save_fi_config(tmp_dir, cfg)
        reloaded = load_fi_config(tmp_dir)
        assert reloaded['monthly_income_cny'] == 50000


# ═══════════════════════════════════════════════════════════
# Life Stages Engine
