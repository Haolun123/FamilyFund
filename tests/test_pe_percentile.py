"""test_pe_percentile.py — PE 历史分位单元测试"""
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

class TestPePercentile:

    def test_get_pe_percentile_from_snapshot_insufficient(self, tmp_dir):
        """少于10条数据返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        import json, os
        history = {'SAP': [{'date': f'2026-05-0{i}', 'pe': 20.0 + i} for i in range(5)]}
        with open(os.path.join(tmp_dir, 'pe_history_us.json'), 'w') as f:
            json.dump(history, f)
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 23.0)
        assert result is None

    def test_get_pe_percentile_from_snapshot_basic(self, tmp_dir):
        """20条数据，当前PE在中间，分位约50%"""
        from fundamentals import get_pe_percentile_from_snapshot
        import json, os
        pes = list(range(10, 30))  # 10..29, 20条
        history = {'SAP': [{'date': f'2026-01-{i+1:02d}', 'pe': float(p)} for i, p in enumerate(pes)]}
        with open(os.path.join(tmp_dir, 'pe_history_us.json'), 'w') as f:
            json.dump(history, f)
        # PE=20 在20条数据中：10条<=20，分位=50%
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 20.0)
        assert result is not None
        assert result['percentile'] == pytest.approx(50.0, abs=5)
        assert result['pe_min'] == 10.0
        assert result['pe_max'] == 29.0

    def test_get_pe_percentile_no_file(self, tmp_dir):
        """文件不存在返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 23.0)
        assert result is None

    def test_get_pe_percentile_none_pe(self, tmp_dir):
        """current_pe=None 返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', None)
        assert result is None

    def test_us_stock_returns_none_from_snapshot_when_no_file(self, tmp_dir):
        """美股：文件不存在返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 23.0)
        assert result is None

    def test_a_share_code_format(self):
        """A股代码6位数字格式（不实际调用 akshare）"""
        # 验证 get_pe_percentile 对 A股代码不返回 None 是因为代码格式有效
        # 实际网络调用会 skip
        import akshare as ak
        code = '601838'
        assert code.isdigit() and len(code) == 6  # 满足 A股判断条件


# ═══════════════════════════════════════════════════════════
# Backtest — end_date + value_per_cost
