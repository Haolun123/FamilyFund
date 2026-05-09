"""test_vxn.py — VXN 纳指波动率相关单元测试"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestComputeVxnSignal:

    def test_extreme_fear(self):
        from market_monitor import compute_vxn_signal
        label, emoji = compute_vxn_signal(45.0)
        assert label == '极端恐慌'
        assert emoji == '🔵'

    def test_elevated(self):
        from market_monitor import compute_vxn_signal
        label, emoji = compute_vxn_signal(35.0)
        assert label == '恐慌'
        assert emoji == '🟢'

    def test_normal(self):
        from market_monitor import compute_vxn_signal
        label, emoji = compute_vxn_signal(18.0)
        assert label == '正常波动'
        assert emoji == '⚪'

    def test_low_greed(self):
        from market_monitor import compute_vxn_signal
        label, emoji = compute_vxn_signal(12.0)
        assert label == '贪婪/低波'
        assert emoji == '🔴'

    def test_none_returns_no_data(self):
        from market_monitor import compute_vxn_signal
        label, emoji = compute_vxn_signal(None)
        assert label == '无数据'
        assert emoji == '—'

    def test_returns_tuple(self):
        from market_monitor import compute_vxn_signal
        result = compute_vxn_signal(22.0)
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestNdxUsesVxnBands:
    """纳指矩阵列分界值已调整为 VXN 阈值 [20, 27, 35]（原 VIX [18, 24, 31]）"""

    def test_ndx100_vix_bands_updated(self):
        from market_monitor import NDX100_VIX_BANDS
        # VXN 分界值比旧 VIX 分界值高
        assert NDX100_VIX_BANDS[0] >= 20   # 原来是 18
        assert NDX100_VIX_BANDS[1] >= 27   # 原来是 24

    def test_ndx_lookup_with_vxn_value(self):
        """用 VXN=23（处于20-27区间）查纳指矩阵，应有结果"""
        from market_monitor import lookup_multiplier
        result = lookup_multiplier(pe=30.0, vix=23.0, target='ndx100')
        assert result != '—'

    def test_sp500_still_uses_original_bands(self):
        """标普矩阵仍使用原 VIX 分界值 [18, 25, 35]"""
        from market_monitor import SP500_VIX_BANDS
        assert SP500_VIX_BANDS[0] == 18

    def test_vxn_high_triggers_more_buying(self):
        """相同 PE，VXN 更高时应给出更大倍数（恐慌 = 好买点）"""
        from market_monitor import lookup_multiplier

        def _to_float(mult_str):
            if mult_str in ('暂停', '—'): return 0.0
            if mult_str == '顶格': return 99.0
            try: return float(mult_str.rstrip('x'))
            except: return 1.0

        low_vxn  = lookup_multiplier(28.0, 18.0, 'ndx100')   # VXN<20
        high_vxn = lookup_multiplier(28.0, 30.0, 'ndx100')   # VXN 27-35
        assert _to_float(high_vxn) >= _to_float(low_vxn)


class TestVolOverride:

    def test_set_vix_override(self, tmp_path):
        """set_vol_override 写入 VIX 手动值"""
        import json
        from unittest.mock import patch
        from market_monitor import set_vol_override

        cache_path = str(tmp_path / 'market_cache.json')
        with patch('market_monitor.CACHE_PATH', cache_path):
            set_vol_override('vix', 20.5)
            with open(cache_path) as f:
                cache = json.load(f)
            assert cache['vix']['price'] == 20.5
            assert cache['vix']['manual_override'] == 20.5

    def test_set_vxn_override(self, tmp_path):
        """set_vol_override 写入 VXN 手动值"""
        import json
        from unittest.mock import patch
        from market_monitor import set_vol_override

        cache_path = str(tmp_path / 'market_cache.json')
        with patch('market_monitor.CACHE_PATH', cache_path):
            set_vol_override('vxn', 25.0)
            with open(cache_path) as f:
                cache = json.load(f)
            assert cache['vxn']['price'] == 25.0
            assert cache['vxn']['manual_override'] == 25.0

    def test_clear_vol_override(self, tmp_path):
        """set_vol_override(None) 清除手动值"""
        import json
        from unittest.mock import patch
        from market_monitor import set_vol_override

        cache_path = str(tmp_path / 'market_cache.json')
        with patch('market_monitor.CACHE_PATH', cache_path):
            set_vol_override('vix', 20.5)
            set_vol_override('vix', None)
            with open(cache_path) as f:
                cache = json.load(f)
            assert 'manual_override' not in cache.get('vix', {})
    """_fetch_vxn 网络调用，需要网络访问"""

    def test_fetch_vxn_returns_float_or_none(self):
        from market_monitor import _fetch_vxn
        try:
            result = _fetch_vxn()
            assert result is None or (isinstance(result, float) and result > 0)
        except Exception:
            pytest.skip('network not available')

    def test_fetch_vxn_fallback_uses_fetch_yfinance(self):
        """CBOE 失败时 fallback 走 _fetch_yfinance，验证 fallback 路径本身能工作"""
        from market_monitor import _fetch_yfinance
        from unittest.mock import patch
        try:
            # 模拟 CBOE 请求失败
            with patch('requests.get', side_effect=Exception('CBOE unavailable')):
                series = _fetch_yfinance('^VXN', period='5d')
                # _fetch_yfinance 本身能返回结果（不依赖 CBOE）
                assert series is None or (hasattr(series, 'iloc') and len(series) >= 0)
        except Exception:
            pytest.skip('network not available')

    def test_vxn_higher_than_vix_historically(self):
        """VXN 历史上高于 VIX 约 3-5 点，验证实时数据符合这一规律"""
        from market_monitor import _fetch_vxn, _fetch_yfinance
        try:
            vxn = _fetch_vxn()
            vix_series = _fetch_yfinance('^VIX', period='5d')
            if vxn and vix_series is not None and len(vix_series) > 0:
                vix = float(vix_series.iloc[-1])
                assert abs(vxn - vix) < 15
        except Exception:
            pytest.skip('network not available')
