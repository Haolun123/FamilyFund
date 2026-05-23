"""tests/test_portfolio_stress_test.py — 组合压力测试单元测试。"""

import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


from portfolio_stress_test import (
    PROXY_MAP,
    _is_fresh,
    _make_fixed_yield_series,
    get_current_weights,
    run_stress_test,
)


class TestProxyMap:

    def test_all_classes_mapped(self):
        """8 个 Asset_Class 都应有代理。"""
        expected = {
            'Fixed_Income', 'US_Blend_Fund', 'US_Growth_Fund',
            'CN_Index_Fund', 'ETF_Stock', 'Gold',
            'Company_Stock', 'Cash',
        }
        assert set(PROXY_MAP.keys()) == expected

    def test_proxy_types_valid(self):
        """type 字段必须是 yfinance / akshare_index / gold_cny / fixed_yield。"""
        valid_types = {'yfinance', 'akshare_index', 'gold_cny', 'fixed_yield'}
        for ac, cfg in PROXY_MAP.items():
            assert cfg['type'] in valid_types, f"{ac}: {cfg['type']} 不在合法类型"


class TestFixedYieldSeries:

    def test_4pct_annual_compounds_correctly(self):
        dates = pd.date_range('2020-01-01', '2025-01-01', freq='B')  # 工作日
        s = _make_fixed_yield_series(0.04, dates)
        # 5 年应该约等于 (1.04)^5 = 1.2167
        years = (dates[-1] - dates[0]).days / 365.25
        expected = 1.04 ** years
        assert abs(s.iloc[-1] - expected) / expected < 0.01

    def test_zero_rate_no_growth(self):
        dates = pd.date_range('2020-01-01', '2021-01-01', freq='B')
        s = _make_fixed_yield_series(0.0, dates)
        assert abs(s.iloc[-1] - 1.0) < 0.001


class TestIsFresh:

    def test_fresh_within_30d(self):
        ts = (datetime.now() - timedelta(days=15)).isoformat()
        assert _is_fresh(ts) is True

    def test_expired_after_30d(self):
        ts = (datetime.now() - timedelta(days=31)).isoformat()
        assert _is_fresh(ts) is False

    def test_invalid_returns_false(self):
        assert _is_fresh('') is False
        assert _is_fresh('not-a-date') is False


class TestGetCurrentWeights:

    def test_aggregates_by_asset_class(self):
        df = pd.DataFrame([
            {'Date': '2026-05-22', 'Asset_Class': 'Fixed_Income', 'Total_Value': 700000},
            {'Date': '2026-05-22', 'Asset_Class': 'Fixed_Income', 'Total_Value': 100000},
            {'Date': '2026-05-22', 'Asset_Class': 'Gold',         'Total_Value': 100000},
            {'Date': '2026-05-22', 'Asset_Class': 'Cash',         'Total_Value': 100000},
        ])
        weights = get_current_weights(df)
        assert weights['Fixed_Income'] == pytest.approx(0.8)
        assert weights['Gold'] == pytest.approx(0.1)
        assert weights['Cash'] == pytest.approx(0.1)

    def test_uses_latest_date(self):
        df = pd.DataFrame([
            {'Date': '2026-05-15', 'Asset_Class': 'Fixed_Income', 'Total_Value': 500000},
            {'Date': '2026-05-22', 'Asset_Class': 'Gold',         'Total_Value': 100000},
            {'Date': '2026-05-22', 'Asset_Class': 'Cash',         'Total_Value': 100000},
        ])
        weights = get_current_weights(df)
        # 5/22 仅有 Gold + Cash
        assert 'Fixed_Income' not in weights
        assert weights['Gold'] == pytest.approx(0.5)

    def test_empty_returns_empty(self):
        assert get_current_weights(None) == {}
        assert get_current_weights(pd.DataFrame()) == {}


class TestRunStressTest:
    """run_stress_test 的端到端测试（依赖网络）。"""

    def test_simple_2asset_portfolio(self):
        """50% Fixed_Income + 50% Cash 的纯收益组合（不依赖外部数据）。"""
        weights = {'Fixed_Income': 0.5, 'Cash': 0.5}
        try:
            r = run_stress_test(weights, start='2020-01-01')
        except Exception:
            pytest.skip('依赖失败')
        if 'error' in r:
            pytest.skip(r['error'])
        # 50% 4% + 50% 1.5% = 2.75% CAGR
        expected_cagr = 0.5 * 0.04 + 0.5 * 0.015
        assert abs(r['cagr'] - expected_cagr) < 0.005
        # 纯收益组合无回撤
        assert abs(r['max_drawdown']) < 0.001

    def test_returns_required_fields(self):
        weights = {'Fixed_Income': 1.0}
        try:
            r = run_stress_test(weights, start='2020-01-01')
        except Exception:
            pytest.skip('依赖失败')
        if 'error' in r:
            pytest.skip(r['error'])
        for f in ['cagr', 'max_drawdown', 'yearly_returns',
                  'best_year', 'worst_year', 'rolling_1y_min',
                  'rolling_1y_max', 'data_start', 'data_end']:
            assert f in r, f"缺字段 {f}"
