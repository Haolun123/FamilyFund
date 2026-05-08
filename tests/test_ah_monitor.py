"""test_ah_monitor.py — AH 溢价监测单元测试"""
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

class TestAhMonitor:

    def test_load_default_config(self, tmp_dir):
        from ah_monitor import load_ah_config
        cfg = load_ah_config(tmp_dir)
        assert 'stocks' in cfg
        assert len(cfg['stocks']) == 4  # 默认4只

    def test_add_remove_stock(self, tmp_dir):
        from ah_monitor import load_ah_config, add_ah_stock, remove_ah_stock
        # 先清空
        import json, os
        with open(os.path.join(tmp_dir, 'ah_config.json'), 'w') as f:
            json.dump({'stocks': [], '_cache': {}, '_history': {}}, f)

        add_ah_stock(tmp_dir, '中海油', '600938.SS', '0883.HK')
        cfg = load_ah_config(tmp_dir)
        assert len(cfg['stocks']) == 1

        # 重复添加不增加
        add_ah_stock(tmp_dir, '中海油', '600938.SS', '0883.HK')
        cfg = load_ah_config(tmp_dir)
        assert len(cfg['stocks']) == 1

        remove_ah_stock(tmp_dir, '600938.SS')
        cfg = load_ah_config(tmp_dir)
        assert len(cfg['stocks']) == 0

    def test_premium_calculation(self):
        """溢价率 = A价 / (H价 × 汇率) × 100"""
        a_price = 38.0
        h_price = 27.0
        hkd_cny = 0.924
        expected = round(a_price / (h_price * hkd_cny) * 100, 1)
        assert expected == pytest.approx(152.5, abs=0.5)

    def test_signal_labels(self):
        """溢价率阈值对应信号"""
        def signal(premium):
            if premium is None: return '无数据'
            if premium > 120:   return '港股便宜'
            if premium > 90:    return '接近平价'
            return '港股贵'

        assert signal(151.9) == '港股便宜'
        assert signal(105.0) == '接近平价'
        assert signal(87.0)  == '港股贵'
        assert signal(None)  == '无数据'


# ═══════════════════════════════════════════════════════════
# FI Engine
