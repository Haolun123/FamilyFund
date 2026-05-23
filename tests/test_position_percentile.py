"""tests/test_position_percentile.py — F4 PB/PE 历史分位单元测试。

不依赖网络的部分测试（缓存读写、归一化、格式化）。
依赖网络的部分（_fetch_a_share / _fetch_hk_share）用 pytest.skip 处理。
"""

import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestSymbolNormalize:

    def test_a_share_with_suffix(self):
        from position_percentile import _normalize_symbol
        assert _normalize_symbol('600309.SS') == ('A', '600309')
        assert _normalize_symbol('002202.SZ') == ('A', '002202')

    def test_hk_share(self):
        from position_percentile import _normalize_symbol
        assert _normalize_symbol('00700.HK') == ('HK', '00700.HK')
        assert _normalize_symbol('0700.HK') == ('HK', '0700.HK')

    def test_six_digit_default_a(self):
        from position_percentile import _normalize_symbol
        assert _normalize_symbol('601838') == ('A', '601838')

    def test_other(self):
        from position_percentile import _normalize_symbol
        assert _normalize_symbol('SAP') == ('OTHER', 'SAP')
        assert _normalize_symbol('') == ('OTHER', '')


class TestCache:

    def test_is_fresh_within_ttl(self):
        from position_percentile import _is_fresh
        # 1 天前
        ts = (datetime.now() - timedelta(days=1)).isoformat()
        assert _is_fresh({'updated': ts}) is True

    def test_is_fresh_expired(self):
        from position_percentile import _is_fresh
        # 8 天前（TTL=7）
        ts = (datetime.now() - timedelta(days=8)).isoformat()
        assert _is_fresh({'updated': ts}) is False

    def test_is_fresh_missing_field(self):
        from position_percentile import _is_fresh
        assert _is_fresh({}) is False
        assert _is_fresh({'updated': ''}) is False

    def test_is_fresh_invalid_format(self):
        from position_percentile import _is_fresh
        assert _is_fresh({'updated': 'not-a-date'}) is False


class TestCacheRoundTrip:

    def test_save_and_load(self, tmp_path, monkeypatch):
        """缓存保存后能正确读回。"""
        cache_file = tmp_path / 'cache.json'
        monkeypatch.setenv('FAMILYFUND_DATA', str(tmp_path))
        # 让 _cache_path 返回 tmp_path/position_percentile_cache.json
        from position_percentile import _save_cache, _load_cache, _cache_path
        # _cache_path 默认返回 $FAMILYFUND_DATA/position_percentile_cache.json
        # tmp_path 已经是 FAMILYFUND_DATA
        data = {'600309.SS': {'symbol': '600309', 'updated': '2026-05-23'}}
        _save_cache(data)
        loaded = _load_cache()
        assert loaded == data


class TestFetchAShare:
    """A 股拉取（依赖网络 + akshare）。"""

    def test_a_share_format(self):
        """成都银行 PB 应该返回合理格式。"""
        try:
            from position_percentile import _fetch_a_share
            r = _fetch_a_share('601838')
        except Exception:
            pytest.skip('akshare not available')
        if r is None:
            pytest.skip('akshare 拉取失败（网络）')
        # 验证字段完整
        assert r['market'] == 'A'
        assert r['symbol'] == '601838'
        # PE 可能 NaN（成都银行 PB 0.85 ROE 15% 合理），但至少应该有
        if r['current_pb'] is not None:
            assert 0 < r['current_pb'] < 100
        if r['pb_pct_5y'] is not None:
            assert 0 <= r['pb_pct_5y'] <= 100


class TestFetchHKShare:
    """港股拉取（依赖网络 + yfinance）。"""

    def test_hk_share_normalize_leading_zero(self):
        """yf_symbol '00700.HK' 应该归一化为 yfinance 的 '0700.HK'。"""
        try:
            from position_percentile import _fetch_hk_share
            r = _fetch_hk_share('00700.HK')
        except Exception:
            pytest.skip('yfinance not available')
        if r is None:
            pytest.skip('yfinance 拉取失败（网络/前导 0 未识别）')
        assert r['market'] == 'HK'
        assert r['yf_code'] == '0700.HK'  # 归一化结果
        assert r['current_price'] is not None
        assert 0 <= r['price_pct_5y'] <= 100


class TestGetPositionData:
    """get_position_data 的缓存逻辑（不依赖网络）。"""

    def test_other_symbol_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv('FAMILYFUND_DATA', str(tmp_path))
        from position_percentile import get_position_data
        # SAP 是 OTHER 类型 → None
        r = get_position_data('SAP')
        assert r is None

    def test_uses_cache_within_ttl(self, tmp_path, monkeypatch):
        """缓存内的数据应直接返回，不重新拉取。"""
        monkeypatch.setenv('FAMILYFUND_DATA', str(tmp_path))
        from position_percentile import get_position_data, _save_cache

        # 预写一份"新鲜"缓存
        cached = {
            '600309.SS': {
                'symbol': '600309',
                'market': 'A',
                'current_pb': 2.19,
                'pb_pct_5y': 14.96,
                'updated': datetime.now().isoformat(),
            }
        }
        _save_cache(cached)

        # 调用应该直接返回缓存值（不会触发网络）
        r = get_position_data('600309.SS', force_refresh=False)
        assert r is not None
        assert r['current_pb'] == 2.19
        assert r['pb_pct_5y'] == 14.96


class TestEniuReference:
    """eniu 长期 PB/PE 参考（依赖网络 + akshare）。"""

    def test_eniu_reference_returns_pb_stats(self):
        try:
            from position_percentile import _fetch_eniu_reference
            r = _fetch_eniu_reference('00700.HK')
        except Exception:
            pytest.skip('akshare/eniu 不可用')
        if not r or 'eniu_pb_min' not in r:
            pytest.skip('eniu 数据未拉到（网络/源已下线）')
        # 腾讯 18 年 PB 区间应包含合理范围
        assert r['eniu_pb_min'] > 0
        assert r['eniu_pb_max'] > r['eniu_pb_min']
        assert r['eniu_pb_min'] <= r['eniu_pb_median'] <= r['eniu_pb_max']
        # 数据应停在 2022-07 左右（eniu 已 stale）
        assert r['eniu_data_end'].startswith('2022')

    def test_eniu_reference_handles_empty_symbol(self):
        from position_percentile import _fetch_eniu_reference
        r = _fetch_eniu_reference('')
        # 空 symbol 不会 crash，返回空 dict
        assert isinstance(r, dict)


class TestHKWithEniuIntegration:
    """_fetch_hk_share 应该返回 eniu 字段（如果可用）。"""

    def test_hk_share_includes_eniu_fields(self):
        try:
            from position_percentile import _fetch_hk_share
            r = _fetch_hk_share('00700.HK')
        except Exception:
            pytest.skip('yfinance/akshare 不可用')
        if r is None:
            pytest.skip('yfinance 拉取失败')
        # 即使 eniu 失败，主流程也应该返回（含 yfinance 数据）
        assert r['market'] == 'HK'
        # eniu 字段如果存在则应有合理值
        if 'eniu_pb_median' in r:
            assert r['eniu_pb_median'] > 0
            assert r['eniu_data_end'] is not None
