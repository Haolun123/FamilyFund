import os
import sys
import pytest
from datetime import date
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from market_monitor import (
    compute_bias,
    compute_vix_signal,
    compute_pe_signal,
    lookup_multiplier,
    TARGETS,
    SP500_MATRIX,
    NDX100_MATRIX,
)


# ─── compute_bias ───

class TestComputeBias:
    def test_normal_zone(self):
        result = compute_bias({'price': 100, 'ma60': 98, 'ma200': 95})
        assert abs(result['bias60']  - 2.04) < 0.01
        assert abs(result['bias200'] - 5.26) < 0.01
        assert result['signal60']  == '正常'
        assert result['signal200'] == '正常'
        assert result['emoji60']   == '⚪'
        assert result['emoji200']  == '⚪'

    def test_deep_oversold(self):
        result = compute_bias({'price': 85, 'ma60': 100, 'ma200': 100})
        assert result['bias60']   == -15.0
        assert result['signal60'] == '深度超卖'
        assert result['emoji60']  == '🔵'

    def test_oversold(self):
        result = compute_bias({'price': 93, 'ma60': 100, 'ma200': 100})
        assert result['bias60']   == -7.0
        assert result['signal60'] == '超卖'
        assert result['emoji60']  == '🟢'

    def test_upper_normal_boundary(self):
        # 正好 +8% 应为"正常"（≤8%）
        result = compute_bias({'price': 108, 'ma60': 100, 'ma200': 100})
        assert result['signal60'] == '正常'

    def test_high(self):
        result = compute_bias({'price': 112, 'ma60': 100, 'ma200': 100})
        assert result['signal60'] == '偏高'
        assert result['emoji60']  == '🟡'

    def test_overbought(self):
        result = compute_bias({'price': 120, 'ma60': 100, 'ma200': 100})
        assert result['signal60'] == '超买'
        assert result['emoji60']  == '🔴'

    def test_ma_none(self):
        result = compute_bias({'price': 100, 'ma60': None, 'ma200': None})
        assert result['bias60']   is None
        assert result['bias200']  is None
        assert result['signal60'] == '无数据'

    def test_price_none(self):
        result = compute_bias({'price': None, 'ma60': 100, 'ma200': 100})
        assert result['bias60'] is None

    def test_bias_exact_minus5(self):
        # -5% 边界：应为"超卖"（> -10% 且 ≤ -5% → 超卖）
        result = compute_bias({'price': 95, 'ma60': 100, 'ma200': 100})
        assert result['signal60'] == '超卖'

    def test_bias_exact_minus10(self):
        # -10% 边界：应为"深度超卖"（≤ -10%）
        result = compute_bias({'price': 90, 'ma60': 100, 'ma200': 100})
        assert result['signal60'] == '深度超卖'


# ─── compute_vix_signal ───

class TestVixSignal:
    def test_greed(self):
        label, emoji = compute_vix_signal(12.0)
        assert label == '贪婪/低波'
        assert emoji == '🔴'

    def test_normal(self):
        label, emoji = compute_vix_signal(18.0)
        assert label == '正常波动'
        assert emoji == '⚪'

    def test_alert(self):
        label, emoji = compute_vix_signal(25.0)
        assert label == '警觉'
        assert emoji == '🟡'

    def test_panic(self):
        label, emoji = compute_vix_signal(35.0)
        assert label == '恐慌'
        assert emoji == '🟢'

    def test_extreme_panic(self):
        label, emoji = compute_vix_signal(45.0)
        assert label == '极端恐慌'
        assert emoji == '🔵'

    def test_none(self):
        label, emoji = compute_vix_signal(None)
        assert label == '无数据'


# ─── compute_pe_signal ───

class TestPeSignal:
    def test_sp500_low(self):
        label, emoji = compute_pe_signal(15.0, 'sp500')
        assert label == '低估'
        assert emoji == '🟢'

    def test_sp500_fair(self):
        label, emoji = compute_pe_signal(20.0, 'sp500')
        assert label == '合理'

    def test_sp500_expensive(self):
        label, emoji = compute_pe_signal(25.0, 'sp500')
        assert label == '偏贵'
        assert emoji == '🟡'

    def test_sp500_overvalued(self):
        label, emoji = compute_pe_signal(30.0, 'sp500')
        assert label == '高估'
        assert emoji == '🔴'

    def test_ndx100_fair(self):
        label, emoji = compute_pe_signal(30.0, 'ndx100')
        assert label == '合理'

    def test_ndx100_expensive(self):
        label, emoji = compute_pe_signal(40.0, 'ndx100')
        assert label == '偏贵'

    def test_ndx100_overvalued(self):
        label, emoji = compute_pe_signal(50.0, 'ndx100')
        assert label == '高估'

    def test_none(self):
        label, _ = compute_pe_signal(None, 'sp500')
        assert label == '无数据'


# ─── lookup_multiplier ───

class TestLookupMultiplier:

    # 标普500
    def test_sp500_pause_high_pe_low_vix(self):
        assert lookup_multiplier(33, 15, 'sp500') == '暂停'

    def test_sp500_03x_pe2932_mid_vix(self):
        # PE=30 (29-32行), VIX=20 (18-25列) → 0.3x
        assert lookup_multiplier(30, 20, 'sp500') == '0.3x'

    def test_sp500_watch_high_pe_mid_vix(self):
        assert lookup_multiplier(33, 27, 'sp500') == '暂停'

    def test_sp500_03x_high_pe_high_vix(self):
        assert lookup_multiplier(33, 36, 'sp500') == '0.3x'

    def test_sp500_05x(self):
        assert lookup_multiplier(27, 20, 'sp500') == '0.5x'

    def test_sp500_12x(self):
        assert lookup_multiplier(27, 27, 'sp500') == '0.8x'

    def test_sp500_top(self):
        assert lookup_multiplier(13, 36, 'sp500') == '顶格'

    def test_sp500_top_low_pe(self):
        assert lookup_multiplier(10, 10, 'sp500') == '顶格'

    def test_sp500_10x(self):
        assert lookup_multiplier(18, 36, 'sp500') == '10.0x'

    # 纳指100
    def test_ndx100_pause(self):
        assert lookup_multiplier(38, 15, 'ndx100') == '暂停'

    def test_ndx100_03x(self):
        assert lookup_multiplier(33, 15, 'ndx100') == '0.3x'

    def test_ndx100_05x(self):
        assert lookup_multiplier(33, 20, 'ndx100') == '0.5x'

    def test_ndx100_top(self):
        assert lookup_multiplier(15, 32, 'ndx100') == '顶格'

    def test_ndx100_watch(self):
        assert lookup_multiplier(38, 27, 'ndx100') == '暂停'

    # 边界
    def test_none_pe(self):
        assert lookup_multiplier(None, 20, 'sp500') == '—'

    def test_none_vix(self):
        assert lookup_multiplier(25, None, 'sp500') == '—'

    # 验证矩阵维度完整
    def test_sp500_matrix_shape(self):
        assert len(SP500_MATRIX) == 8
        for row in SP500_MATRIX:
            assert len(row) == 4

    def test_ndx100_matrix_shape(self):
        assert len(NDX100_MATRIX) == 8
        for row in NDX100_MATRIX:
            assert len(row) == 4


# ─── TARGETS 配置完整性 ───

class TestTargetsConfig:
    def test_all_targets_have_required_keys(self):
        for key, cfg in TARGETS.items():
            assert 'name'       in cfg, f'{key} missing name'
            assert 'source'     in cfg, f'{key} missing source'
            assert 'symbol'     in cfg, f'{key} missing symbol'
            assert 'primary_ma' in cfg, f'{key} missing primary_ma'
            assert cfg['primary_ma'] in (60, 200), f'{key} primary_ma must be 60 or 200'

    def test_a_shares_use_ma60(self):
        assert TARGETS['csi300']['primary_ma']   == 60
        assert TARGETS['csi_a500']['primary_ma'] == 60

    def test_us_and_gold_use_ma200(self):
        assert TARGETS['gold']['primary_ma']   == 200
        assert TARGETS['ndx100']['primary_ma'] == 200
        assert TARGETS['sp500']['primary_ma']  == 200


# ─── _fetch_yfinance 单行返回回归测试（2026-06-28 修复）───
#
# Bug: yfinance.download 在某些情况（市场假期、symbol 数据稀缺）
# 只返回 1 行时,df['Close'].squeeze() 会退化为 numpy.float64 标量,
# 上游对它调用 len() 报 TypeError。^TNX（美债10Y）端午节假期触发。

class TestFetchYfinanceSingleRow:
    """覆盖 _fetch_yfinance 在 yfinance 返回 1 行/0 行/多行 的三种场景。"""

    def _mock_yf_download(self, monkeypatch, df):
        import market_monitor
        # 替换 _fetch_yfinance 内部的 yfinance.download
        class FakeYF:
            @staticmethod
            def download(*args, **kwargs):
                return df
        monkeypatch.setitem(__import__('sys').modules, 'yfinance', FakeYF)

    def test_single_row_returns_series_not_scalar(self, monkeypatch):
        """1 行数据时返回值必须是 Series，不能是标量。"""
        import pandas as pd
        from market_monitor import _fetch_yfinance

        df = pd.DataFrame(
            {'Close': [4.25]},
            index=pd.to_datetime(['2026-06-27']),
        )
        self._mock_yf_download(monkeypatch, df)

        result = _fetch_yfinance('^TNX', period='5d')
        assert result is not None
        assert isinstance(result, pd.Series), \
            f'Expected pd.Series, got {type(result)}'
        assert len(result) == 1
        assert float(result.iloc[-1]) == 4.25

    def test_multi_row_returns_series(self, monkeypatch):
        """多行数据时返回值是 Series。"""
        import pandas as pd
        from market_monitor import _fetch_yfinance

        df = pd.DataFrame(
            {'Close': [4.20, 4.22, 4.25]},
            index=pd.to_datetime(['2026-06-25', '2026-06-26', '2026-06-27']),
        )
        self._mock_yf_download(monkeypatch, df)

        result = _fetch_yfinance('^TNX', period='5d')
        assert result is not None
        assert isinstance(result, pd.Series)
        assert len(result) == 3
        assert float(result.iloc[-1]) == 4.25

    def test_empty_dataframe_returns_none(self, monkeypatch):
        """空 DataFrame 返回 None。"""
        import pandas as pd
        from market_monitor import _fetch_yfinance

        df = pd.DataFrame({'Close': []})
        self._mock_yf_download(monkeypatch, df)

        result = _fetch_yfinance('^TNX', period='5d')
        assert result is None

    def test_single_row_result_can_be_indexed(self, monkeypatch):
        """1 行返回值必须支持 len() 和 iloc[-1]（调用方依赖）。"""
        import pandas as pd
        from market_monitor import _fetch_yfinance

        df = pd.DataFrame(
            {'Close': [4.25]},
            index=pd.to_datetime(['2026-06-27']),
        )
        self._mock_yf_download(monkeypatch, df)

        result = _fetch_yfinance('^TNX', period='5d')
        # 这正是 get_market_data 第 439 行的调用模式
        assert len(result) > 0  # 之前这里 TypeError
        latest = round(float(result.iloc[-1]), 3)
        assert latest == 4.25


@pytest.fixture
def market_refresh_env(tmp_path, monkeypatch):
    """隔离缓存与所有外部接口，检验完整刷新流程。"""
    import market_monitor as m
    monkeypatch.setattr(m, 'CACHE_PATH', str(tmp_path / 'market_cache.json'))
    for name in ('_fetch_akshare', '_fetch_yfinance', '_fetch_vxn', '_fetch_qvix',
                 '_fetch_pe', '_fetch_akshare_pe'):
        monkeypatch.setattr(m, name, lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(
        bond_zh_us_rate=lambda **kwargs: pd.DataFrame(),
    ))
    return m


class TestQvixMissingRows:
    @pytest.mark.parametrize('bad', [float('nan'), float('inf'), 0, -1, None, ''])
    def test_trailing_invalid_close_uses_latest_valid_date(self, monkeypatch, bad):
        import market_monitor as m
        frame = pd.DataFrame({
            'date': ['2026-11-18', '2026-11-17', '2026-11-19'],
            'close': [20.46, 20.3, bad],
        })
        monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(
            index_option_300etf_qvix=lambda: frame,
        ))
        assert m._fetch_qvix() == {'price': 20.46, 'price_date': '2026-11-18'}

    def test_all_invalid_closes_return_no_data(self, monkeypatch):
        import market_monitor as m
        frame = pd.DataFrame({'date': ['2026-11-19'], 'close': [float('nan')]})
        monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(
            index_option_300etf_qvix=lambda: frame,
        ))
        assert m._fetch_qvix() is None


class TestMarketRefresh:
    @pytest.mark.parametrize('key,setter', [
        ('vix', 'vol'), ('vxn', 'vol'), ('pe_sp500', 'pe'), ('pe_ndx100', 'pe'),
    ])
    def test_new_automatic_value_replaces_manual_on_force_refresh(self, market_refresh_env, monkeypatch, key, setter):
        m = market_refresh_env
        if setter == 'pe':
            m.set_pe_override(key.removeprefix('pe_'), 40)
        else:
            m.set_vol_override(key, 40)
        before = m._load_cache()[key]['updated_at']
        # 当日正常重绘保留刚录入的手动值；显式刷新不能被手动标志阻止。
        assert m.get_market_data()['refresh_status'][key] == 'manual'
        monkeypatch.setattr(m, '_fetch_yfinance', lambda *a, **k: pd.Series([22.5], index=['2026-11-20']))
        monkeypatch.setattr(m, '_fetch_vxn', lambda: 22.5)
        monkeypatch.setattr(m, '_fetch_pe', lambda ticker: 22.5)
        result = m.get_market_data(force_refresh=True)
        field = 'value' if setter == 'pe' else 'price'
        assert result[key][field] == 22.5
        assert not result[key].get('manual_override')
        assert result[key]['updated_at'] >= before
        assert result['refresh_status'][key] == 'updated'
        assert m._load_cache()[key] == result[key]

    @pytest.mark.parametrize('key', ['vix', 'vxn', 'pe_sp500', 'pe_ndx100'])
    def test_failed_refresh_keeps_manual_value_and_timestamp(self, market_refresh_env, key):
        m = market_refresh_env
        if key.startswith('pe_'):
            m.set_pe_override(key.removeprefix('pe_'), 40)
        else:
            m.set_vol_override(key, 40)
        before = m._load_cache()[key]
        result = m.get_market_data(force_refresh=True)
        assert result[key] == before
        assert result['refresh_status'][key] == 'fallback'

    def test_previous_day_manual_value_is_automatically_refreshed(self, market_refresh_env, monkeypatch):
        m = market_refresh_env
        m.set_vol_override('vix', 40)
        cache = m._load_cache()
        cache['vix_updated'] = '2020-01-01'
        m._save_cache(cache)
        monkeypatch.setattr(m, '_fetch_yfinance', lambda *a, **k: pd.Series([22.5], index=['2026-11-20']))
        result = m.get_market_data()
        assert result['vix']['price'] == 22.5
        assert result['refresh_status']['vix'] == 'updated'

    def test_older_automatic_timestamp_cannot_replace_newer_manual(self, market_refresh_env, monkeypatch):
        m = market_refresh_env
        monkeypatch.setattr(m, '_updated_at', lambda: '2030-01-01T12:00:00+00:00')
        m.set_vol_override('vix', 40)
        monkeypatch.setattr(m, '_updated_at', lambda: '2030-01-01T11:00:00+00:00')
        monkeypatch.setattr(m, '_fetch_yfinance', lambda *a, **k: pd.Series([22.5], index=['2026-11-20']))
        result = m.get_market_data(force_refresh=True)
        assert result['vix']['price'] == 40
        assert result['refresh_status']['vix'] == 'manual'

    def test_clear_pe_override_invalidates_today_cache(self, market_refresh_env, monkeypatch):
        m = market_refresh_env
        m.set_pe_override('ndx100', 40)
        m.set_pe_override('ndx100', None)
        monkeypatch.setattr(m, '_fetch_pe', lambda ticker: 25.06)
        result = m.get_market_data()
        assert result['pe_ndx100']['value'] == 25.06
        assert result['refresh_status']['pe_ndx100'] == 'updated'

    @pytest.mark.parametrize('bad', [float('nan'), float('inf'), None])
    def test_invalid_today_cache_is_retried(self, market_refresh_env, monkeypatch, bad):
        m = market_refresh_env
        m._save_cache({'qvix': {'price': bad}, 'qvix_updated': date.today().isoformat()})
        monkeypatch.setattr(m, '_fetch_qvix', lambda: {'price': 20.46, 'price_date': '2026-11-18'})
        result = m.get_market_data()
        assert result['qvix'] == m._load_cache()['qvix']
        assert result['qvix']['price'] == 20.46
        assert result['qvix']['price_date'] == '2026-11-18'
        assert result['refresh_status']['qvix'] == 'updated'

    def test_invalid_response_does_not_replace_valid_cache(self, market_refresh_env, monkeypatch):
        m = market_refresh_env
        old = {'price': 20.46, 'price_date': '2026-11-18'}
        m._save_cache({'qvix': old, 'qvix_updated': '2026-11-18'})
        monkeypatch.setattr(m, '_fetch_qvix', lambda: {'price': float('nan')})
        result = m.get_market_data(force_refresh=True)
        assert result['qvix'] == old
        assert result['meta']['qvix_updated'] == '2026-11-18'
        assert result['refresh_status']['qvix'] == 'fallback'

    def test_invalid_cache_and_failed_fetch_do_not_feed_matrix(self, market_refresh_env):
        m = market_refresh_env
        m._save_cache({'qvix': {'price': float('nan')}, 'qvix_updated': date.today().isoformat()})
        result = m.get_market_data()
        assert result['qvix'] is None
        assert result['meta']['qvix_updated'] == '未知'
        assert result['refresh_status']['qvix'] == 'unavailable'
        assert m.lookup_a_share_multiplier(15, (result['qvix'] or {}).get('price'), 'csi300') == '—'
