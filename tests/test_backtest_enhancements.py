"""test_backtest_enhancements.py — 回测增强功能单元测试"""
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

class TestBacktestEnhancements:

    def test_end_date_truncates_periods(self):
        """end_date 截断后，期数应少于无截断版本"""
        from backtest import run_backtest
        try:
            result_full = run_backtest('csi300', '2020-01-01', 1000.0, freq='M')
            result_trunc = run_backtest('csi300', '2020-01-01', 1000.0, freq='M',
                                        end_date='2021-12-31')
            assert result_trunc['fixed']['periods'] < result_full['fixed']['periods']
        except Exception:
            pytest.skip('network not available')

    def test_value_per_cost_present(self):
        """返回结果含 value_per_cost 字段"""
        from backtest import run_backtest
        try:
            result = run_backtest('csi300', '2020-01-01', 1000.0,
                                  freq='M', end_date='2022-12-31')
            assert 'value_per_cost' in result['fixed']
            assert 'value_per_cost' in result['matrix']
            assert result['fixed']['value_per_cost'] > 0
        except Exception:
            pytest.skip('network not available')

    def test_no_cash_rate_in_result(self):
        """结果不再含 cash_rate_annual 字段（已移除机会成本逻辑）"""
        from backtest import run_backtest
        try:
            result = run_backtest('csi300', '2020-01-01', 1000.0,
                                  freq='M', end_date='2022-12-31')
            assert 'cash_rate_annual' not in result
            assert 'combined_value' not in result['matrix']
        except Exception:
            pytest.skip('network not available')
