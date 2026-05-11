"""test_fundamentals.py — fundamentals.py 单元测试（v2 数据结构）"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fundamentals import (
    load_yf_symbols, save_yf_symbols,
    add_yf_symbol, remove_yf_symbol,
    get_yf_symbol, get_show_fundamentals, update_show_fundamentals,
    _DEFAULT_SYMBOLS,
)


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


class TestLoadYfSymbols:

    def test_returns_defaults_when_file_missing(self, tmp_dir):
        result = load_yf_symbols(tmp_dir)
        assert result == dict(_DEFAULT_SYMBOLS)

    def test_loads_new_format(self, tmp_dir):
        data = {
            '601838': {'yf_symbol': '601838.SS', 'show_fundamentals': True},
        }
        save_yf_symbols(tmp_dir, data)
        result = load_yf_symbols(tmp_dir)
        assert get_yf_symbol(result, '601838') == '601838.SS'
        assert get_show_fundamentals(result, '601838') is True

    def test_migrates_old_string_format(self, tmp_dir):
        """旧格式（值为字符串）自动迁移为新格式"""
        data = {'601838': '601838.SS', 'HK0700': '0700.HK'}
        save_yf_symbols(tmp_dir, data)
        result = load_yf_symbols(tmp_dir)
        # 迁移后应为 dict 格式
        assert isinstance(result['601838'], dict)
        assert get_yf_symbol(result, '601838') == '601838.SS'
        assert get_show_fundamentals(result, '601838') is True  # 迁移默认 True

    def test_migration_persisted(self, tmp_dir):
        """迁移后写回文件，下次加载不再需要迁移"""
        save_yf_symbols(tmp_dir, {'601838': '601838.SS'})
        load_yf_symbols(tmp_dir)  # 触发迁移
        with open(os.path.join(tmp_dir, 'yf_symbols.json')) as f:
            saved = json.load(f)
        assert isinstance(saved.get('601838'), dict)

    def test_cache_key_preserved(self, tmp_dir):
        data = {
            '601838': {'yf_symbol': '601838.SS', 'show_fundamentals': True},
            '_cache': {'601838.SS': {'trailingPE': 6.0}},
        }
        save_yf_symbols(tmp_dir, data)
        result = load_yf_symbols(tmp_dir)
        assert '_cache' in result


class TestGetHelpers:

    def test_get_yf_symbol_new_format(self, tmp_dir):
        add_yf_symbol(tmp_dir, '601838', '601838.SS')
        data = load_yf_symbols(tmp_dir)
        assert get_yf_symbol(data, '601838') == '601838.SS'

    def test_get_yf_symbol_missing(self, tmp_dir):
        data = load_yf_symbols(tmp_dir)
        assert get_yf_symbol(data, 'NONEXISTENT') is None

    def test_get_show_fundamentals_default_true(self, tmp_dir):
        add_yf_symbol(tmp_dir, '601838', '601838.SS')
        data = load_yf_symbols(tmp_dir)
        assert get_show_fundamentals(data, '601838') is True

    def test_get_show_fundamentals_false(self, tmp_dir):
        add_yf_symbol(tmp_dir, '512890', '512890.SS', show_fundamentals=False)
        data = load_yf_symbols(tmp_dir)
        assert get_show_fundamentals(data, '512890') is False

    def test_update_show_fundamentals(self, tmp_dir):
        add_yf_symbol(tmp_dir, '512890', '512890.SS', show_fundamentals=False)
        update_show_fundamentals(tmp_dir, '512890', True)
        data = load_yf_symbols(tmp_dir)
        assert get_show_fundamentals(data, '512890') is True


class TestAddRemoveYfSymbol:

    def test_add_new_symbol(self, tmp_dir):
        add_yf_symbol(tmp_dir, '9633', '9633.HK')
        data = load_yf_symbols(tmp_dir)
        assert get_yf_symbol(data, '9633') == '9633.HK'

    def test_add_with_show_false(self, tmp_dir):
        add_yf_symbol(tmp_dir, '512890', '512890.SS', show_fundamentals=False)
        data = load_yf_symbols(tmp_dir)
        assert get_show_fundamentals(data, '512890') is False

    def test_add_clears_cache(self, tmp_dir):
        data = {
            '601838': {'yf_symbol': '601838.SS', 'show_fundamentals': True},
            '_cache': {'601838.SS': {'trailingPE': 6.0, 'updated': '2026-01-01'}},
        }
        save_yf_symbols(tmp_dir, data)
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
        assert get_yf_symbol(data, '601838') == '601838.SS'
