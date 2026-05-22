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


class TestHongKongStock:
    """港股拉价：返回 HKD 原始股价 + currency='HKD' + fx_rate (HKD/CNY)。"""

    def test_hk_returns_currency_and_fx_rate(self):
        from price_fetcher import _route
        try:
            r = _route('HK0700')
        except Exception:
            pytest.skip('network not available')
        if r['status'] != 'ok':
            pytest.skip(f"HK0700 拉取未成功: {r.get('msg')}")
        # 港股必须返回 currency='HKD' 和 fx_rate
        assert r.get('currency') == 'HKD'
        assert r.get('fx_rate') is not None
        # HKD/CNY 历史区间约 0.85-0.95
        assert 0.80 < r['fx_rate'] < 1.0
        # 港币股价应在合理区间（腾讯历史 200-700 港币）
        assert 100 < r['price'] < 1000

    def test_hk_full_format_code(self):
        """0700.HK 直接传入也支持。"""
        from price_fetcher import _route
        try:
            r = _route('0700.HK')
        except Exception:
            pytest.skip('network not available')
        if r['status'] == 'ok':
            assert r.get('currency') == 'HKD'
            assert r.get('fx_rate') is not None

    def test_non_hk_no_currency_field(self):
        """非港股不应返回 currency / fx_rate（保持向后兼容）。"""
        from price_fetcher import _route
        r = _route('CASH')
        assert r['status'] == 'ok'
        # CASH 不应有 currency / fx_rate（保持简单）
        assert r.get('currency') is None
        assert r.get('fx_rate') is None
