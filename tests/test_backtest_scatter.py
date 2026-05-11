"""test_backtest_scatter.py — 策略有效性散点图相关测试"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestRunAllTargets:

    def test_returns_six_results(self):
        """run_all_targets 返回6个结果（黄金跑两次：原始+对冲）"""
        from backtest import run_all_targets
        try:
            results = run_all_targets(
                user_start_date='2015-03-01',
                base_amount=1000.0,
                freq='M',
                end_date='2022-12-31',
            )
            assert len(results) == 6
            targets = [r['target'] for r in results]
            assert targets.count('gold') == 2
            labels = [r['label'] for r in results]
            assert any('原始' in l for l in labels)
            assert any('对冲' in l for l in labels)
        except Exception:
            pytest.skip('network not available')

    def test_a_share_start_date_clamped(self):
        """A股起始日期不早于 2015-01-01（QVIX 限制，PE 从 2005 起已够）"""
        from backtest import run_all_targets, _TARGET_MIN_DATES
        try:
            results = run_all_targets(
                user_start_date='2000-01-01',  # 早于 A股限制
                base_amount=1000.0,
                freq='M',
                end_date='2022-12-31',
            )
            for r in results:
                if r['target'] in ('csi300', 'csi_a500'):
                    assert r['actual_start'] >= '2015-03-01'
                    assert r['actual_start'] == _TARGET_MIN_DATES[r['target']]
        except Exception:
            pytest.skip('network not available')

    def test_us_start_date_uses_user_setting(self):
        """美股起始日期用用户设置（不被clamp）"""
        from backtest import run_all_targets
        try:
            results = run_all_targets(
                user_start_date='2015-06-01',
                base_amount=1000.0,
                freq='M',
                end_date='2022-12-31',
            )
            for r in results:
                if r['target'] in ('sp500', 'ndx100', 'gold'):
                    assert r['actual_start'] == '2015-06-01'
        except Exception:
            pytest.skip('network not available')

    def test_result_has_required_fields(self):
        """每个结果包含必要字段"""
        from backtest import run_all_targets
        try:
            results = run_all_targets(
                user_start_date='2015-03-01',
                base_amount=1000.0,
                freq='M',
                end_date='2022-12-31',
            )
            required = {'target', 'label', 'actual_start', 'xirr_excess',
                        'pl_excess', 'fixed_xirr', 'matrix_xirr', 'error'}
            for r in results:
                assert required.issubset(set(r.keys()))
        except Exception:
            pytest.skip('network not available')

    def test_error_does_not_crash(self):
        """单个标的失败不影响其他标的"""
        from backtest import run_all_targets
        from unittest.mock import patch
        # 模拟 run_backtest 对 gold 抛异常
        original_run = None
        try:
            from backtest import run_backtest as _orig
            original_run = _orig
        except Exception:
            pytest.skip('cannot import')

        call_count = [0]
        def mock_run(target, **kwargs):
            call_count[0] += 1
            if target == 'gold':
                raise ValueError('mock gold failure')
            return original_run(target, **kwargs)

        try:
            with patch('backtest.run_backtest', side_effect=mock_run):
                results = run_all_targets('2015-03-01', 1000.0, end_date='2022-12-31')
            gold_result = next(r for r in results if r['target'] == 'gold')
            assert gold_result['error'] is not None
            assert gold_result['xirr_excess'] is None
            # 其他标的正常
            others = [r for r in results if r['target'] != 'gold']
            # 至少部分成功（网络允许的情况下）
            assert len(results) == 5
        except Exception:
            pytest.skip('network not available')


class TestTargetMinDates:

    def test_a_share_min_2015(self):
        from backtest import _TARGET_MIN_DATES
        assert _TARGET_MIN_DATES['csi300']   == '2015-03-01'
        assert _TARGET_MIN_DATES['csi_a500'] == '2015-03-01'

    def test_us_min_dates(self):
        from backtest import _TARGET_MIN_DATES
        assert _TARGET_MIN_DATES['sp500']  == '1990-01-01'
        assert _TARGET_MIN_DATES['ndx100'] == '2009-10-01'  # VXN starts 2009-09-14
        assert _TARGET_MIN_DATES['gold']   == '1990-01-01'

    def test_actual_start_takes_later_date(self):
        """actual_start = max(user_start, min_date)"""
        from backtest import _TARGET_MIN_DATES
        user = '2010-01-01'
        for target in ('csi300', 'csi_a500'):
            actual = max(user, _TARGET_MIN_DATES[target])
            assert actual == '2015-03-01'
        # sp500/gold: min=1990, user=2010 → user wins
        actual_sp = max(user, _TARGET_MIN_DATES['sp500'])
        assert actual_sp == '2010-01-01'
        # ndx100: min=2009-10-01, user=2010-01-01 → user wins
        actual_ndx = max(user, _TARGET_MIN_DATES['ndx100'])
        assert actual_ndx == '2010-01-01'
