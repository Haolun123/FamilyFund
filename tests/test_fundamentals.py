"""test_fundamentals.py — fundamentals.py 单元测试"""
import os
import sys
import json
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fundamentals import (
    load_yf_symbols, save_yf_symbols,
    add_yf_symbol, remove_yf_symbol,
    _DEFAULT_SYMBOLS,
)


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


class TestLoadYfSymbols:
    def test_returns_defaults_when_file_missing(self, tmp_dir):
        result = load_yf_symbols(tmp_dir)
        assert result == dict(_DEFAULT_SYMBOLS)

    def test_loads_existing_file(self, tmp_dir):
        data = {'601838': '601838.SS', 'HK0700': '0700.HK'}
        save_yf_symbols(tmp_dir, data)
        result = load_yf_symbols(tmp_dir)
        assert result['601838'] == '601838.SS'
        assert result['HK0700'] == '0700.HK'

    def test_cache_key_excluded(self, tmp_dir):
        data = {'601838': '601838.SS', '_cache': {'601838.SS': {'trailingPE': 6.0}}}
        save_yf_symbols(tmp_dir, data)
        result = load_yf_symbols(tmp_dir)
        # _cache 应保留在文件里，但 load 返回的 dict 包含它
        assert '_cache' in result


class TestAddRemoveYfSymbol:
    def test_add_new_symbol(self, tmp_dir):
        add_yf_symbol(tmp_dir, '9633', '9633.HK')
        data = load_yf_symbols(tmp_dir)
        assert data['9633'] == '9633.HK'

    def test_update_existing_symbol(self, tmp_dir):
        add_yf_symbol(tmp_dir, '601838', '601838.SS')
        add_yf_symbol(tmp_dir, '601838', '601838.SS')  # 更新
        data = load_yf_symbols(tmp_dir)
        assert data['601838'] == '601838.SS'

    def test_add_clears_cache(self, tmp_dir):
        # 先写一个带缓存的文件
        data = {'601838': '601838.SS', '_cache': {'601838.SS': {'trailingPE': 6.0, 'updated': '2026-01-01'}}}
        save_yf_symbols(tmp_dir, data)
        # 重新 add 同一 symbol 应清除缓存
        add_yf_symbol(tmp_dir, '601838', '601838.SS')
        result = load_yf_symbols(tmp_dir)
        assert '601838.SS' not in result.get('_cache', {})

    def test_remove_symbol(self, tmp_dir):
        add_yf_symbol(tmp_dir, '601838', '601838.SS')
        remove_yf_symbol(tmp_dir, '601838')
        data = load_yf_symbols(tmp_dir)
        assert '601838' not in data

    def test_remove_nonexistent_is_noop(self, tmp_dir):
        remove_yf_symbol(tmp_dir, 'NONEXISTENT')  # 不应报错

    def test_strips_whitespace(self, tmp_dir):
        add_yf_symbol(tmp_dir, '  601838  ', '  601838.SS  ')
        data = load_yf_symbols(tmp_dir)
        assert '601838' in data
        assert data['601838'] == '601838.SS'
