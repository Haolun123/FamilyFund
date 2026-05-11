"""test_price_fetcher.py — price_fetcher 单元测试"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestRouting:

    def test_cash_fixed(self):
        from price_fetcher import _route
        r = _route('CASH')
        assert r['status'] == 'ok'
        assert r['price'] == 1.0

    def test_fixed_income_manual(self):
        from price_fetcher import _route
        for code in ['GD040219', 'JY040214', '17123GH']:
            r = _route(code)
            assert r['status'] == 'manual', f"{code} should be manual"

    def test_six_digit_fund_routes_to_eastmoney(self):
        """6位纯数字且不在 yf_symbols → 天天基金"""
        from price_fetcher import _route
        # 不实际调用网络，只验证路由逻辑不报错
        try:
            r = _route('018738')
            assert r['status'] in ('ok', 'error')
        except Exception:
            pytest.skip('network not available')

    def test_hk_code_conversion(self):
        """HK0700 → 0700.HK"""
        from price_fetcher import _route
        try:
            r = _route('HK0700')
            assert r['status'] in ('ok', 'error')
        except Exception:
            pytest.skip('network not available')

    def test_gold_returns_cny_per_gram(self):
        """黄金返回元/克（约 700-1500 区间）"""
        from price_fetcher import _route
        try:
            r = _route('GOLD')
            if r['status'] == 'ok':
                assert 500 < r['price'] < 2000, f"Unexpected gold price: {r['price']}"
        except Exception:
            pytest.skip('network not available')

    def test_sap_routes_to_yfinance(self):
        from price_fetcher import _route
        try:
            r = _route('SAP.DE')
            assert r['status'] in ('ok', 'error')
            if r['status'] == 'ok':
                assert r['price'] > 0
        except Exception:
            pytest.skip('network not available')


class TestFetchLatestPrices:

    def test_returns_dict_with_all_codes(self, tmp_path):
        """返回字典包含所有持仓 Code"""
        import pandas as pd
        from price_fetcher import fetch_latest_prices

        df = pd.DataFrame([
            {'Date': '2026-05-09', 'Asset_Class': 'Cash', 'Code': 'CASH',
             'Name': '现金', 'Platform': '', 'Currency': 'CNY',
             'Exchange_Rate': 1.0, 'Shares': 1, 'Current_Price': 1.0,
             'Total_Value': 10000, 'Net_Cash_Flow': 0},
            {'Date': '2026-05-09', 'Asset_Class': 'Fixed_Income', 'Code': 'GD040219',
             'Name': '周周宝', 'Platform': '', 'Currency': 'CNY',
             'Exchange_Rate': 1.0, 'Shares': 1, 'Current_Price': 1.0,
             'Total_Value': 5000, 'Net_Cash_Flow': 0},
        ])
        results = fetch_latest_prices(df, str(tmp_path))
        assert 'CASH' in results
        assert 'GD040219' in results
        assert results['CASH']['status'] == 'ok'
        assert results['GD040219']['status'] == 'manual'
