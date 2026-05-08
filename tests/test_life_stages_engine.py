"""test_life_stages_engine.py — 人生阶段规划引擎单元测试"""
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

class TestLifeStagesEngine:

    @pytest.fixture
    def sample_data(self):
        return {
            'milestones': [
                {
                    'id': 'early_childhood', 'name': '早期养育',
                    'enabled': True, 'start_year': 2026, 'end_year': 2030,
                    'scenarios': {
                        'base': {'annual_cny': 60000},
                        'pessimistic': {'annual_cny': 120000},
                        'optimistic': {'annual_cny': 30000},
                    },
                    'selected': 'base', 'inflation_rate': 0.0,
                },
                {
                    'id': 'property', 'name': '置业',
                    'enabled': True, 'target_year': 2030,
                    'scenarios': {
                        'base': {'down_payment_cny': 1500000, 'monthly_mortgage_cny': 8000},
                    },
                    'selected': 'base',
                },
            ]
        }

    def test_basic_expense_curve(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve = compute_expense_curve(sample_data, 'base')
        # 2026 应有早期养育支出
        assert curve[2026]['components'].get('early_childhood', 0) == pytest.approx(60000, rel=0.01)

    def test_property_down_payment_year(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve = compute_expense_curve(sample_data, 'base')
        # 2030 应有置业首付
        assert curve[2030]['components'].get('property', 0) > 1000000

    def test_property_mortgage_after_target(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve = compute_expense_curve(sample_data, 'base')
        # 2031 应有月供（年化）
        assert curve[2031]['components'].get('property', 0) == pytest.approx(8000 * 12, rel=0.01)

    def test_disabled_milestone_excluded(self, sample_data):
        from life_stages_engine import compute_expense_curve
        sample_data['milestones'][0]['enabled'] = False
        curve = compute_expense_curve(sample_data, 'base')
        assert curve[2026]['components'].get('early_childhood', 0) == 0

    def test_scenario_pessimistic_higher(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve_base = compute_expense_curve(sample_data, 'base')
        curve_pess = compute_expense_curve(sample_data, 'pessimistic')
        assert curve_pess[2026]['total'] > curve_base[2026]['total']

    def test_inflation_adjustment(self):
        from life_stages_engine import compute_expense_curve
        import datetime
        cur = datetime.date.today().year
        data = {'milestones': [{
            'id': 'early_childhood', 'enabled': True,
            'start_year': cur, 'end_year': cur + 5,
            'scenarios': {'base': {'annual_cny': 100000}},
            'selected': 'base', 'inflation_rate': 0.10,
        }]}
        curve = compute_expense_curve(data, 'base')
        # 第二年应高于第一年（通胀10%）
        assert curve[cur + 1]['total'] > curve[cur]['total']

    def test_higher_education_spread(self):
        from life_stages_engine import compute_expense_curve
        import datetime
        cur = datetime.date.today().year
        data = {'milestones': [{
            'id': 'higher_education', 'enabled': True,
            'start_year': cur, 'end_year': cur + 4,
            'scenarios': {'base': {'total_cny': 400000}},
            'selected': 'base', 'inflation_rate': 0.0,
        }]}
        curve = compute_expense_curve(data, 'base')
        # 总额平摊4年，每年约10万
        assert curve[cur]['total'] == pytest.approx(100000, rel=0.01)


# ═══════════════════════════════════════════════════════════
# Fundamentals PE Percentile
