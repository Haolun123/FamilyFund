"""test_synthetic_sp.py — 合成标普β定投工具测试。"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from synthetic_sp import (  # noqa: E402
    compute_synthetic_dca,
    estimate_coverage,
    load_prepaid,
    save_prepaid,
    consume_prepaid,
    topup_prepaid,
    _parse_multiplier_str,
)

# 默认配置（复用用户实际参数）
CONFIG = {
    'sp_target_base':  2500,
    'ndx_target_base': 2500,
    'sp_other_weekly':  550,
    'ndx_other_weekly': 1050,
    'synthetic_split': {'dow': 0.5, 'jianxin_ndx': 0.5},
}


class TestParseMultiplier:
    def test_x_suffix(self):
        assert _parse_multiplier_str('0.8x') == 0.8

    def test_dash_is_zero(self):
        assert _parse_multiplier_str('—') == 0.0

    def test_none_is_zero(self):
        assert _parse_multiplier_str(None) == 0.0

    def test_float_passthrough(self):
        assert _parse_multiplier_str(1.5) == 1.5


class TestComputeSplit:
    """拆腿计算与守恒校验。"""

    def test_base_case_0_3_0_8(self):
        """标普0.3x/纳指0.8x → 建信1050 + 道指100"""
        r = compute_synthetic_dca({}, CONFIG, sp_multiplier=0.3, ndx_multiplier=0.8)
        assert r['sp_target'] == 750
        assert r['ndx_target'] == 2000
        assert r['sp_gap'] == 200
        assert r['ndx_gap'] == 950
        assert r['jianxin_weekly'] == 1050  # 950 + 100
        assert r['dow_weekly'] == 100

    def test_conservation_sp(self):
        """标普守恒: sp_other + jianxin_sp_leg + dow_weekly == sp_target"""
        r = compute_synthetic_dca({}, CONFIG, sp_multiplier=0.3, ndx_multiplier=0.8)
        assert CONFIG['sp_other_weekly'] + r['jianxin_sp_leg'] + r['dow_weekly'] == r['sp_target']

    def test_conservation_ndx(self):
        """纳指守恒: ndx_other + jianxin_ndx_leg == ndx_target"""
        r = compute_synthetic_dca({}, CONFIG, sp_multiplier=0.3, ndx_multiplier=0.8)
        assert CONFIG['ndx_other_weekly'] + r['jianxin_ndx_leg'] == r['ndx_target']

    def test_high_multiplier(self):
        """标普2.0x → 目标5000 缺口4450 道指腿2220(取整) 建信sp腿2230"""
        r = compute_synthetic_dca({}, CONFIG, sp_multiplier=2.0, ndx_multiplier=1.0)
        assert r['sp_target'] == 5000
        assert r['sp_gap'] == 4450
        assert r['dow_weekly'] == 2220  # round(4450*0.5/10)*10
        assert r['jianxin_sp_leg'] == 2230  # 缺口反推,保证守恒
        # 守恒(两腿之和精确等于缺口)
        assert CONFIG['sp_other_weekly'] + r['jianxin_sp_leg'] + r['dow_weekly'] == r['sp_target']

    def test_gap_zero_when_other_covers(self):
        """标普倍数低到场外已够 → 缺口0, 道指腿=建信sp腿=0"""
        r = compute_synthetic_dca({}, CONFIG, sp_multiplier=0.2, ndx_multiplier=0.4)
        # 标普目标 500 < 场外550 → 缺口0
        assert r['sp_target'] == 500
        assert r['sp_gap'] == 0
        assert r['dow_weekly'] == 0
        assert r['jianxin_sp_leg'] == 0
        # 纳指目标 1000 < 场外1050 → 缺口0
        assert r['ndx_gap'] == 0
        assert r['jianxin_weekly'] == 0

    def test_configurable_split_40_60(self):
        """拆腿比例改 40:60"""
        cfg = dict(CONFIG)
        cfg['synthetic_split'] = {'dow': 0.4, 'jianxin_ndx': 0.6}
        r = compute_synthetic_dca({}, cfg, sp_multiplier=0.3, ndx_multiplier=0.8)
        # 缺口200 → 道指80, 建信sp腿120
        assert r['dow_weekly'] == 80
        assert r['jianxin_sp_leg'] == 120
        assert r['jianxin_weekly'] == 950 + 120


class TestCoverage:
    def test_coverage_50_weeks(self):
        assert estimate_coverage(5000, 100) == 50

    def test_coverage_zero_weekly(self):
        """道指周配额0 → 返回-1(无需消耗)"""
        assert estimate_coverage(5000, 0) == -1

    def test_coverage_partial(self):
        assert estimate_coverage(5000, 725) == 6  # 5000//725


class TestPrepaidState:
    """预付池状态读写与消耗。"""

    def _tmp_dir(self):
        return tempfile.mkdtemp()

    def test_load_default_when_missing(self):
        d = self._tmp_dir()
        data = load_prepaid(d)
        assert data['dow_prepaid']['balance'] == 0.0
        assert data['config']['sp_other_weekly'] == 550

    def test_topup_sets_balance(self):
        d = self._tmp_dir()
        r = topup_prepaid(d, '2026-08-10', 5000)
        assert r['balance'] == 5000.0
        # 持久化验证
        data = load_prepaid(d)
        assert data['dow_prepaid']['balance'] == 5000.0
        assert data['dow_prepaid']['last_topup_date'] == '2026-08-10'

    def test_consume_deducts(self):
        d = self._tmp_dir()
        topup_prepaid(d, '2026-08-10', 5000)
        r = consume_prepaid(d, '2026-08-17', dow_weekly=100, sp_multiplier=0.3)
        assert r['balance_after'] == 4900.0
        assert r['consumed'] == 100.0
        assert r['need_topup'] is False

    def test_consume_log_appended(self):
        d = self._tmp_dir()
        topup_prepaid(d, '2026-08-10', 5000)
        consume_prepaid(d, '2026-08-17', dow_weekly=100, sp_multiplier=0.3)
        consume_prepaid(d, '2026-08-24', dow_weekly=100, sp_multiplier=0.3)
        data = load_prepaid(d)
        log = data['dow_prepaid']['consumption_log']
        assert len(log) == 2
        assert log[-1]['balance_after'] == 4800.0

    def test_need_topup_when_depleted(self):
        d = self._tmp_dir()
        topup_prepaid(d, '2026-08-10', 100)
        r = consume_prepaid(d, '2026-08-17', dow_weekly=100)
        assert r['balance_after'] == 0.0
        assert r['need_topup'] is True

    def test_topup_accumulates(self):
        """补仓是累加，不是覆盖"""
        d = self._tmp_dir()
        topup_prepaid(d, '2026-08-10', 5000)
        consume_prepaid(d, '2026-08-17', dow_weekly=1000)  # 余4000
        topup_prepaid(d, '2026-08-24', 5000)               # 累加到9000
        data = load_prepaid(d)
        assert data['dow_prepaid']['balance'] == 9000.0
