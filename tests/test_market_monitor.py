import os
import sys
import pytest

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
        assert lookup_multiplier(33, 27, 'sp500') == '观望'

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
        assert lookup_multiplier(38, 27, 'ndx100') == '观望'

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
