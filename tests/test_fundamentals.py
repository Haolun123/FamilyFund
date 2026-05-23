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


# ── watch_symbols.json + fundamentals_history.json ──────────


from fundamentals import (
    load_watch_symbols,
    _collect_yf_symbols_for_history,
    append_fundamentals_snapshot,
    get_fundamentals_history,
)


class TestWatchSymbols:

    def test_load_returns_empty_when_missing(self, tmp_dir):
        data = load_watch_symbols(tmp_dir)
        assert data == {'watch': {}}

    def test_load_existing_file(self, tmp_dir):
        path = os.path.join(tmp_dir, 'watch_symbols.json')
        with open(path, 'w') as f:
            json.dump({
                'watch': {
                    'VOO': {'name': '标普500'},
                    'QQQ': {'name': '纳指100'},
                }
            }, f)
        data = load_watch_symbols(tmp_dir)
        assert 'VOO' in data['watch']
        assert data['watch']['QQQ']['name'] == '纳指100'


class TestCollectSymbolsForHistory:
    """合并 yf_symbols + watch_symbols 并排除 A 股。"""

    def test_excludes_a_share(self, tmp_dir):
        # 写 yf_symbols：含一个 A 股
        add_yf_symbol(tmp_dir, '601838', '601838.SS')
        add_yf_symbol(tmp_dir, 'HK0700', '0700.HK')
        symbols = _collect_yf_symbols_for_history(tmp_dir)
        assert '0700.HK' in symbols
        assert '601838.SS' not in symbols  # A 股排除

    def test_merges_watch_symbols(self, tmp_dir):
        # yf_symbols 有腾讯（add_yf_symbol 会在新文件时初始化 _DEFAULT_SYMBOLS, 含 SAP/512890）
        add_yf_symbol(tmp_dir, 'HK0700', '0700.HK')
        # watch_symbols 有 VOO/QQQ
        watch_path = os.path.join(tmp_dir, 'watch_symbols.json')
        with open(watch_path, 'w') as f:
            json.dump({
                'watch': {'VOO': {}, 'QQQ': {}}
            }, f)
        symbols = _collect_yf_symbols_for_history(tmp_dir)
        # 来自 yf_symbols 的：0700.HK + SAP（默认） + 512890.SS（默认，但被 A 股过滤掉）
        # 来自 watch_symbols：VOO + QQQ
        assert '0700.HK' in symbols
        assert 'VOO' in symbols
        assert 'QQQ' in symbols
        # 不应包含 A 股代码
        assert not any(s.endswith('.SS') or s.endswith('.SZ') for s in symbols)

    def test_dedup_when_overlap(self, tmp_dir):
        """yf_symbols 和 watch_symbols 重叠时去重。"""
        add_yf_symbol(tmp_dir, 'SAP.DE', 'SAP')
        watch_path = os.path.join(tmp_dir, 'watch_symbols.json')
        with open(watch_path, 'w') as f:
            json.dump({'watch': {'SAP': {}}}, f)
        symbols = _collect_yf_symbols_for_history(tmp_dir)
        assert symbols.count('SAP') == 1


class TestFundamentalsHistory:

    def test_get_empty_when_missing(self, tmp_dir):
        assert get_fundamentals_history(tmp_dir) == {}
        assert get_fundamentals_history(tmp_dir, 'VOO') == []

    def test_get_specific_symbol(self, tmp_dir):
        # 手写一份历史
        path = os.path.join(tmp_dir, 'fundamentals_history.json')
        with open(path, 'w') as f:
            json.dump({
                'VOO': [{'date': '2026-05-22', 'pe': 28.0, 'pb': 1.75}],
                'QQQ': [{'date': '2026-05-22', 'pe': 35.0}],
            }, f)
        voo = get_fundamentals_history(tmp_dir, 'VOO')
        assert len(voo) == 1
        assert voo[0]['pe'] == 28.0

        nonexistent = get_fundamentals_history(tmp_dir, 'XXX')
        assert nonexistent == []

    def test_get_full_dict(self, tmp_dir):
        path = os.path.join(tmp_dir, 'fundamentals_history.json')
        with open(path, 'w') as f:
            json.dump({'VOO': [], 'QQQ': []}, f)
        full = get_fundamentals_history(tmp_dir)
        assert 'VOO' in full
        assert 'QQQ' in full


class TestAppendIdempotent:
    """append_fundamentals_snapshot 同一天不重复写。"""

    def test_skips_if_already_today(self, tmp_dir, monkeypatch):
        from datetime import date as _date
        today = _date.today().isoformat()

        # 写一份 yf_symbols，不让默认值进来
        ys_path = os.path.join(tmp_dir, 'yf_symbols.json')
        with open(ys_path, 'w') as f:
            json.dump({}, f)

        # 预写一份"今天"的 VOO 数据
        path = os.path.join(tmp_dir, 'fundamentals_history.json')
        with open(path, 'w') as f:
            json.dump({
                'VOO': [{'date': today, 'pe': 28.0}],
            }, f)
        # 写 watch_symbols.json 让 VOO 进入采集列表
        watch_path = os.path.join(tmp_dir, 'watch_symbols.json')
        with open(watch_path, 'w') as f:
            json.dump({'watch': {'VOO': {}}}, f)

        # mock yfinance（避免网络调用）
        class _MockTicker:
            @property
            def info(self):
                return {'trailingPE': 99.0}  # 故意填异常值，验证不会被写入
        monkeypatch.setattr('yfinance.Ticker', lambda s: _MockTicker())

        stats = append_fundamentals_snapshot(tmp_dir)
        assert stats['skipped_today'] == 1  # VOO 跳过
        assert stats['updated'] == 0

        # 验证未覆盖
        with open(path) as f:
            data = json.load(f)
        assert data['VOO'][0]['pe'] == 28.0  # 没被 99 覆盖
