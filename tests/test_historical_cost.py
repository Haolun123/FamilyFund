"""test_historical_cost.py — 历史成本跟踪模块测试"""
import os
import sys
import json
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _make_portfolio(tmp_path, rows):
    cols = ['Date', 'Asset_Class', 'Platform', 'Name', 'Code',
            'Currency', 'Exchange_Rate', 'Shares', 'Current_Price',
            'Total_Value', 'Net_Cash_Flow']
    pd.DataFrame(rows, columns=cols).to_csv(tmp_path / 'portfolio.csv', index=False)


def _make_hist_cost(tmp_path, data):
    with open(tmp_path / 'historical_cost.json', 'w') as f:
        json.dump(data, f)


class TestLoadSave:

    def test_load_empty(self, tmp_path):
        from historical_cost import load_historical_cost
        assert load_historical_cost(str(tmp_path)) == {}

    def test_load_filters_meta(self, tmp_path):
        _make_hist_cost(tmp_path, {
            '_note': 'meta', '_updated': '2026-01-01',
            '021000': {'avg_cost': 1.85, 'currency': 'CNY', 'name': '南方纳指'},
        })
        from historical_cost import load_historical_cost
        result = load_historical_cost(str(tmp_path))
        assert '021000' in result
        assert '_note' not in result

    def test_save_preserves_meta(self, tmp_path):
        _make_hist_cost(tmp_path, {'_note': '备注', '021000': {'avg_cost': 1.85}})
        from historical_cost import load_historical_cost, save_historical_cost
        cost_map = load_historical_cost(str(tmp_path))
        save_historical_cost(str(tmp_path), cost_map)
        with open(tmp_path / 'historical_cost.json') as f:
            saved = json.load(f)
        assert saved.get('_note') == '备注'
        assert '021000' in saved


class TestRollHistoricalCost:

    def test_no_post_ncf(self, tmp_path):
        """4月10日后无买入，均价不变"""
        _make_hist_cost(tmp_path, {
            '021000': {'avg_cost': 1.85, 'currency': 'CNY', 'name': '南方纳指'}
        })
        _make_portfolio(tmp_path, [
            ('2026-04-10', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.0, 20000.0, 20000.0),
        ])
        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), df)
        assert abs(result['021000']['avg_cost'] - 1.85) < 0.001

    def test_with_post_ncf(self, tmp_path):
        """4月10日后买入，均价应滚动更新"""
        _make_hist_cost(tmp_path, {
            '021000': {'avg_cost': 2.0, 'currency': 'CNY', 'name': '南方纳指'}
        })
        _make_portfolio(tmp_path, [
            # 建仓日：10000份，均价2.0，NCF=20000
            ('2026-04-10', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.0, 20000.0, 20000.0),
            # 后续买入：+2000份，花了5000元（均价2.5），NCF=5000
            ('2026-05-01', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 12000.0, 2.5, 30000.0, 5000.0),
        ])
        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), df)
        # 预期：(2.0×10000 + 5000) / 12000 = 25000/12000 ≈ 2.0833
        expected = (2.0 * 10000 + 5000) / 12000
        assert abs(result['021000']['avg_cost'] - expected) < 0.001


class TestComputeHistoricalPL:

    def test_basic_pl(self, tmp_path):
        """基本盈亏计算"""
        _make_hist_cost(tmp_path, {
            '021000': {'avg_cost': 2.0, 'currency': 'CNY', 'name': '南方纳指'}
        })
        _make_portfolio(tmp_path, [
            ('2026-04-10', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.0, 20000.0, 20000.0),
            ('2026-08-01', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.5, 25000.0, 0.0),
        ])
        from historical_cost import compute_historical_pl
        from nav_engine import load_portfolio
        df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = compute_historical_pl(str(tmp_path), df)
        assert len(result) == 1
        row = result.iloc[0]
        assert abs(row['Market_Value_CNY'] - 25000) < 1
        assert abs(row['Hist_Total_Cost_CNY'] - 20000) < 1
        assert abs(row['Hist_PL_CNY'] - 5000) < 1
        assert abs(row['Hist_PL_Rate'] - 25.0) < 0.1

    def test_loss_scenario(self, tmp_path):
        """亏损场景"""
        _make_hist_cost(tmp_path, {
            'HK0700': {'avg_cost': 500.0, 'currency': 'HKD', 'name': '腾讯'}
        })
        _make_portfolio(tmp_path, [
            ('2026-04-10', 'ETF_Stock', 'p', '腾讯', 'HK0700',
             'HKD', 0.9, 200.0, 450.0, 81000.0, 81000.0),
            ('2026-08-01', 'Individual_Stock', 'p', '腾讯', 'HK0700',
             'HKD', 0.9, 200.0, 450.0, 81000.0, 0.0),
        ])
        from historical_cost import compute_historical_pl
        from nav_engine import load_portfolio
        df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = compute_historical_pl(str(tmp_path), df)
        assert len(result) == 1
        row = result.iloc[0]
        # 历史成本 = 500 HKD × 0.9 × 200 = 90000 CNY
        # 市值 = 450 × 0.9 × 200 = 81000 CNY
        assert abs(row['Hist_Total_Cost_CNY'] - 90000) < 1
        assert abs(row['Hist_PL_CNY'] - (-9000)) < 1

    def test_missing_code_skipped(self, tmp_path):
        """historical_cost.json 里没有的标的不出现在结果里"""
        _make_hist_cost(tmp_path, {
            '021000': {'avg_cost': 2.0, 'currency': 'CNY', 'name': '南方纳指'}
        })
        _make_portfolio(tmp_path, [
            ('2026-04-10', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.0, 20000.0, 20000.0),
            ('2026-04-10', 'Gold', 'p', '黄金', 'GOLD',
             'CNY', 1.0, 100.0, 500.0, 50000.0, 50000.0),
            ('2026-08-01', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.5, 25000.0, 0.0),
            ('2026-08-01', 'Gold', 'p', '黄金', 'GOLD',
             'CNY', 1.0, 100.0, 600.0, 60000.0, 0.0),
        ])
        from historical_cost import compute_historical_pl
        from nav_engine import load_portfolio
        df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = compute_historical_pl(str(tmp_path), df)
        codes = set(result['Code'].tolist())
        assert '021000' in codes
        assert 'GOLD' not in codes  # 没填历史成本，跳过
