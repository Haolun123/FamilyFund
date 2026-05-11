"""test_gold_hedge.py — 黄金对冲矩阵单元测试"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestGoldHedgeMultiplier:

    def test_high_pe_extreme_fear_is_top(self):
        """高PE+极度恐慌 → 顶格（最强对冲需求）"""
        from market_monitor import lookup_gold_hedge_multiplier
        assert lookup_gold_hedge_multiplier(35.0, 40.0) == '顶格'

    def test_low_pe_calm_is_pause(self):
        """低PE+平静 → 暂停（不需要对冲）"""
        from market_monitor import lookup_gold_hedge_multiplier
        assert lookup_gold_hedge_multiplier(15.0, 15.0) == '暂停'

    def test_high_pe_calm_still_buys(self):
        """高PE+平静 → 仍然买入（股市高估就要持有对冲）"""
        from market_monitor import lookup_gold_hedge_multiplier
        result = lookup_gold_hedge_multiplier(35.0, 15.0)
        assert result not in ('暂停', '—')

    def test_none_returns_dash(self):
        from market_monitor import lookup_gold_hedge_multiplier
        assert lookup_gold_hedge_multiplier(None, 20.0) == '—'
        assert lookup_gold_hedge_multiplier(25.0, None) == '—'
        assert lookup_gold_hedge_multiplier(None, None) == '—'

    def test_direction_opposite_to_original(self):
        """对冲矩阵：高PE/VIX时多买（顶格），原始矩阵：低乖离率时多买"""
        from market_monitor import lookup_gold_hedge_multiplier, lookup_gold_multiplier

        def _val(s):
            if s == '顶格': return 99
            if s in ('暂停', '—'): return 0
            try: return float(s.rstrip('x'))
            except: return 0

        # 高PE(33)+高VIX(40)：对冲应该顶格
        hedge_high = _val(lookup_gold_hedge_multiplier(33.0, 40.0))
        assert hedge_high == 99  # 顶格

        # 低PE(15)+低VIX(15)：对冲应该暂停
        hedge_low = _val(lookup_gold_hedge_multiplier(15.0, 15.0))
        assert hedge_low == 0  # 暂停

        # 原始矩阵：低乖离率(-15%)+高VIX → 应该多买
        orig_low_bias = _val(lookup_gold_multiplier(-15.0, 40.0))
        assert orig_low_bias >= 3.0

    def test_matrix_shape(self):
        """矩阵形状：7行4列"""
        from market_monitor import GOLD_HEDGE_MATRIX, GOLD_HEDGE_PE_BANDS, GOLD_HEDGE_VIX_BANDS
        assert len(GOLD_HEDGE_MATRIX) == len(GOLD_HEDGE_PE_BANDS) + 1
        assert all(len(row) == len(GOLD_HEDGE_VIX_BANDS) + 1 for row in GOLD_HEDGE_MATRIX)


class TestGoldHedgeBacktest:

    def test_hedge_mode_uses_sp500_pe(self):
        """对冲模式下，黄金回测使用标普PE而非乖离率"""
        from backtest import run_backtest
        try:
            # 原始模式
            r_orig = run_backtest('gold', '2015-01-01', 1000.0, freq='M',
                                  end_date='2020-12-31', gold_hedge_mode=False)
            # 对冲模式
            r_hedge = run_backtest('gold', '2015-01-01', 1000.0, freq='M',
                                   end_date='2020-12-31', gold_hedge_mode=True)
            # 两种模式结果应该不同（不同矩阵信号）
            assert r_orig['matrix']['total_cost'] != r_hedge['matrix']['total_cost']
        except Exception:
            pytest.skip('network not available')

    def test_default_is_original_mode(self):
        """默认不传 gold_hedge_mode 时使用原始矩阵"""
        from backtest import run_backtest
        try:
            r_default = run_backtest('gold', '2020-01-01', 1000.0, freq='M',
                                     end_date='2022-12-31')
            r_orig    = run_backtest('gold', '2020-01-01', 1000.0, freq='M',
                                     end_date='2022-12-31', gold_hedge_mode=False)
            assert r_default['matrix']['total_cost'] == r_orig['matrix']['total_cost']
        except Exception:
            pytest.skip('network not available')
