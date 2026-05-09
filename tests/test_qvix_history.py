"""test_qvix_history.py — QVIX 历史快照与动态分位单元测试"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


class TestAppendVolSnapshot:

    def test_appends_qvix(self, tmp_dir):
        from market_monitor import append_vol_snapshot
        market_data = {'qvix': {'price': 16.5}}
        append_vol_snapshot(tmp_dir, market_data)
        with open(os.path.join(tmp_dir, 'vol_history.json')) as f:
            h = json.load(f)
        assert len(h['qvix']) == 1
        assert h['qvix'][0]['value'] == 16.5

    def test_idempotent_same_day(self, tmp_dir):
        """同一天追加两次，只写一条"""
        from market_monitor import append_vol_snapshot
        market_data = {'qvix': {'price': 16.5}}
        append_vol_snapshot(tmp_dir, market_data)
        append_vol_snapshot(tmp_dir, market_data)
        with open(os.path.join(tmp_dir, 'vol_history.json')) as f:
            h = json.load(f)
        assert len(h['qvix']) == 1

    def test_skips_if_qvix_none(self, tmp_dir):
        """QVIX 无数据时不写文件"""
        from market_monitor import append_vol_snapshot
        append_vol_snapshot(tmp_dir, {})
        assert not os.path.exists(os.path.join(tmp_dir, 'vol_history.json'))

    def test_accumulates_multiple_days(self, tmp_dir):
        """模拟多天追加"""
        from market_monitor import append_vol_snapshot
        import datetime
        p = os.path.join(tmp_dir, 'vol_history.json')
        # 手动写入过去几天
        history = {'qvix': [
            {'date': '2026-05-01', 'value': 15.0},
            {'date': '2026-05-02', 'value': 16.0},
        ]}
        with open(p, 'w') as f:
            json.dump(history, f)
        # 今天新增
        append_vol_snapshot(tmp_dir, {'qvix': {'price': 17.0}})
        with open(p) as f:
            h = json.load(f)
        assert len(h['qvix']) == 3


class TestGetQvixPercentile:

    def _write_history(self, tmp_dir, values):
        from datetime import date, timedelta
        records = [
            {'date': (date(2026, 1, 1) + timedelta(days=i)).isoformat(), 'value': v}
            for i, v in enumerate(values)
        ]
        p = os.path.join(tmp_dir, 'vol_history.json')
        with open(p, 'w') as f:
            json.dump({'qvix': records}, f)

    def test_insufficient_data_returns_none(self, tmp_dir):
        from market_monitor import get_qvix_percentile
        self._write_history(tmp_dir, [15.0] * 5)
        assert get_qvix_percentile(tmp_dir, 15.0) is None

    def test_no_file_returns_none(self, tmp_dir):
        from market_monitor import get_qvix_percentile
        assert get_qvix_percentile(tmp_dir, 15.0) is None

    def test_none_qvix_returns_none(self, tmp_dir):
        from market_monitor import get_qvix_percentile
        self._write_history(tmp_dir, list(range(10, 30)))
        assert get_qvix_percentile(tmp_dir, None) is None

    def test_median_percentile(self, tmp_dir):
        """值在中间，分位约50%"""
        from market_monitor import get_qvix_percentile
        values = list(range(10, 30))  # 10..29, 20条
        self._write_history(tmp_dir, values)
        result = get_qvix_percentile(tmp_dir, 20.0)
        assert result is not None
        assert result['percentile'] == pytest.approx(55.0, abs=10)
        assert result['days'] == 20
        assert result['min'] == 10.0
        assert result['max'] == 29.0

    def test_low_value_low_percentile(self, tmp_dir):
        """极低值分位接近0%"""
        from market_monitor import get_qvix_percentile
        self._write_history(tmp_dir, list(range(15, 35)))
        result = get_qvix_percentile(tmp_dir, 10.0)
        assert result['percentile'] == 0.0

    def test_high_value_high_percentile(self, tmp_dir):
        """极高值分位接近100%"""
        from market_monitor import get_qvix_percentile
        self._write_history(tmp_dir, list(range(15, 35)))
        result = get_qvix_percentile(tmp_dir, 50.0)
        assert result['percentile'] == 100.0
