"""test_dca_manager.py — DCA Manager 单元测试"""
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

class TestDcaManager:

    def test_load_default_config(self, tmp_dir):
        from dca_manager import load_dca_config
        cfg = load_dca_config(tmp_dir)
        assert 'plans' in cfg
        assert cfg['plans'] == []

    def test_add_and_remove_plan(self, tmp_dir):
        from dca_manager import add_plan, remove_plan, load_dca_config
        pid = add_plan(tmp_dir, {
            'name': '博时标普500', 'code': '018738',
            'asset_class': 'US_Blend_Fund', 'platform': '支付宝',
            'base_amount_cny': 800, 'frequency': 'weekly',
            'enabled': True, 'unit': 'cny', 'note': '',
        })
        cfg = load_dca_config(tmp_dir)
        assert len(cfg['plans']) == 1
        assert cfg['plans'][0]['id'] == pid

        remove_plan(tmp_dir, pid)
        cfg = load_dca_config(tmp_dir)
        assert len(cfg['plans']) == 0

    def test_parse_multiplier_str(self):
        from dca_manager import _parse_multiplier_str
        assert _parse_multiplier_str('1.5x') == 1.5
        assert _parse_multiplier_str('暂停') == 0.0
        assert _parse_multiplier_str('顶格') == 3.0
        assert _parse_multiplier_str('—') == 1.0
        assert _parse_multiplier_str('') == 1.0

    def test_gold_gram_pause_below_min_unit(self):
        """0.3x × 2g = 0.6g < 1g(min_unit) → 暂停(0g)"""
        from dca_manager import compute_suggestion
        plan = {
            'asset_class': 'Gold', 'unit': 'gram',
            'base_amount_unit': 2, 'min_unit': 1,
        }
        # mock market_data で multiplier=0.3 になるよう直接 monkeypatch せず
        # _parse_multiplier_str経由でテスト
        from dca_manager import _parse_multiplier_str
        from unittest.mock import patch
        with patch('market_monitor.lookup_gold_multiplier', return_value='0.3x'):
            market_data = {
                'gold': {'price': 3000, 'ma60': 2800, 'ma200': 2600},
                'vix': {'price': 18.0},
            }
            sug = compute_suggestion(plan, market_data)
        assert sug['suggested_unit'] == 0
        assert sug['unit'] == 'gram'

    def test_gold_gram_rounds_correctly(self):
        """0.5x × 2g = 1.0g >= 1g → 1g"""
        from dca_manager import compute_suggestion
        from unittest.mock import patch
        plan = {
            'asset_class': 'Gold', 'unit': 'gram',
            'base_amount_unit': 2, 'min_unit': 1,
        }
        with patch('market_monitor.lookup_gold_multiplier', return_value='0.5x'):
            market_data = {
                'gold': {'price': 3000, 'ma60': 2800, 'ma200': 2600},
                'vix': {'price': 18.0},
            }
            sug = compute_suggestion(plan, market_data)
        assert sug['suggested_unit'] == 1

    def test_cny_suggestion_rounds_to_10(self):
        """base=800, 1.5x → 1200（整10元）"""
        from dca_manager import compute_suggestion
        from unittest.mock import patch
        plan = {
            'asset_class': 'US_Blend_Fund', 'unit': 'cny',
            'base_amount_cny': 800,
        }
        with patch('market_monitor.lookup_multiplier', return_value='1.5x'):
            market_data = {
                'pe_sp500': {'value': 22.0},
                'vix': {'price': 18.0},
            }
            sug = compute_suggestion(plan, market_data)
        assert sug['suggested_cny'] == 1200
        assert sug['suggested_cny'] % 10 == 0


# ═══════════════════════════════════════════════════════════
# AH Monitor
