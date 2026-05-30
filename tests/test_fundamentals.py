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
    get_fundamentals_by_yf_symbol,
)


class TestGetFundamentalsByYfSymbol:
    """通过 yf_symbol 直接获取（A 股 fundamentals 接入芒格面板用）。"""

    def test_returns_none_when_empty_symbol(self, tmp_dir):
        assert get_fundamentals_by_yf_symbol(tmp_dir, '') is None
        assert get_fundamentals_by_yf_symbol(tmp_dir, None) is None

    def test_uses_cache_within_today(self, tmp_dir, monkeypatch):
        """同一天有 _cache 数据时直接返回，不调用 yfinance。"""
        from datetime import date as _date
        today = _date.today().isoformat()
        # 写一份 yf_symbols.json 含 _cache
        path = os.path.join(tmp_dir, 'yf_symbols.json')
        data = {
            '600309': {'yf_symbol': '600309.SS', 'show_fundamentals': True},
            '_cache': {
                '600309.SS': {
                    'trailingPE': 18.5,
                    'priceToBook': 2.19,
                    'returnOnEquity': 0.13,
                    'updated': today,
                }
            }
        }
        with open(path, 'w') as f:
            json.dump(data, f)

        # mock yfinance（如果调用就报错）
        def _should_not_call(*args, **kwargs):
            raise RuntimeError("不应调用 yfinance（应走缓存）")
        monkeypatch.setattr('yfinance.Ticker', _should_not_call)

        result = get_fundamentals_by_yf_symbol(tmp_dir, '600309.SS')
        assert result is not None
        assert result['returnOnEquity'] == 0.13
        assert result['trailingPE'] == 18.5
        # updated 字段应被剥离
        assert 'updated' not in result


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


# ── 回归测试: 港股 5→4 位归一化(2026-05-28 bug) ──────────────────


from fundamentals import _normalize_yf_symbol


class TestNormalizeYfSymbol:
    """港股代码归一化:yfinance 对 5 位 .HK 代码安静失败,
    项目 ticker_map 历史用 5 位,需要在数据层归一化。"""

    def test_hk_5digit_to_4digit(self):
        """5 位港股代码去前导 0 到 4 位。"""
        assert _normalize_yf_symbol('00700.HK') == '0700.HK'
        assert _normalize_yf_symbol('09992.HK') == '9992.HK'
        assert _normalize_yf_symbol('00883.HK') == '0883.HK'
        assert _normalize_yf_symbol('00981.HK') == '0981.HK'

    def test_hk_4digit_unchanged(self):
        """已经 4 位的港股代码不变。"""
        assert _normalize_yf_symbol('0700.HK') == '0700.HK'
        assert _normalize_yf_symbol('9992.HK') == '9992.HK'

    def test_hk_5digit_no_leading_zero(self):
        """5 位不以 0 开头的港股(罕见,如 99988.HK 这种假设场景),不动。"""
        # 实际港股没有这种,但保证函数不破坏不规范输入
        assert _normalize_yf_symbol('12345.HK') == '12345.HK'

    def test_a_share_unchanged(self):
        """A 股代码不动。"""
        assert _normalize_yf_symbol('601838.SS') == '601838.SS'
        assert _normalize_yf_symbol('002202.SZ') == '002202.SZ'

    def test_us_stock_unchanged(self):
        """美股 / ADR / 无后缀代码不动。"""
        assert _normalize_yf_symbol('SAP') == 'SAP'
        assert _normalize_yf_symbol('AAPL') == 'AAPL'
        assert _normalize_yf_symbol('SAP.DE') == 'SAP.DE'

    def test_empty_or_none(self):
        """空输入不报错。"""
        assert _normalize_yf_symbol('') == ''
        assert _normalize_yf_symbol(None) is None

    def test_whitespace(self):
        """带空格的输入归一化。"""
        assert _normalize_yf_symbol(' 00700.HK ') == '0700.HK'

    def test_history_query_uses_normalization(self, tmp_dir):
        """get_fundamentals_history 用 5 位查询时,能命中 4 位 key 数据。"""
        from fundamentals import get_fundamentals_history
        path = os.path.join(tmp_dir, 'fundamentals_history.json')
        with open(path, 'w') as f:
            json.dump({
                '0700.HK': [{'date': '2026-05-23', 'roe': 0.205, 'pe': 15.2}],
            }, f)
        # 用 ticker_map 里的 5 位代码查
        result = get_fundamentals_history(tmp_dir, '00700.HK')
        assert len(result) == 1
        assert result[0]['roe'] == 0.205


class TestDirtyCacheInvalidation:
    """脏缓存防御:即使 today=updated,关键字段 None 也应触发重拉。
    2026-05-28 bug: yfinance 早上拉过返回 None,缓存写入 today,
    整天读到空数据,直到下一天。"""

    def test_dirty_cache_with_none_currentPrice_triggers_refetch(self, tmp_dir, monkeypatch):
        from datetime import date as _date
        today = _date.today().isoformat()

        # 写脏缓存:今天的,但 currentPrice / trailingPE 都是 None
        ys_path = os.path.join(tmp_dir, 'yf_symbols.json')
        with open(ys_path, 'w') as f:
            json.dump({
                '_cache': {
                    '0700.HK': {
                        'currentPrice': None,
                        'trailingPE': None,
                        'updated': today,
                    }
                }
            }, f)

        # mock yfinance 返回真实数据
        class _MockInfo(dict):
            pass
        class _MockTicker:
            def __init__(self, s): self._sym = s
            @property
            def info(self):
                return {
                    'currentPrice': 425.4,
                    'trailingPE': 15.25,
                    'priceToBook': 2.95,
                    'returnOnEquity': 0.205,
                }
        monkeypatch.setattr('yfinance.Ticker', _MockTicker)

        result = get_fundamentals_by_yf_symbol(tmp_dir, '0700.HK', force_refresh=False)
        assert result is not None
        # 关键:None 缓存被识别为脏,触发重拉,得到真实数据
        assert result['currentPrice'] == 425.4
        assert result['trailingPE'] == 15.25

    def test_valid_cache_skips_refetch(self, tmp_dir, monkeypatch):
        """缓存非脏时正常命中,不重拉。"""
        from datetime import date as _date
        today = _date.today().isoformat()

        ys_path = os.path.join(tmp_dir, 'yf_symbols.json')
        with open(ys_path, 'w') as f:
            json.dump({
                '_cache': {
                    '0700.HK': {
                        'currentPrice': 425.4,
                        'trailingPE': 15.25,
                        'updated': today,
                    }
                }
            }, f)

        # mock 一旦被调用就报错(应该走缓存,不调用)
        def _no_call(*a, **k):
            raise RuntimeError("不该调用 yfinance(应走缓存)")
        monkeypatch.setattr('yfinance.Ticker', _no_call)

        result = get_fundamentals_by_yf_symbol(tmp_dir, '0700.HK', force_refresh=False)
        assert result['currentPrice'] == 425.4


# ── 回归测试: 新增标的 UX 改进 (2026-05-30) ──────────────────


from fundamentals import infer_currency


class TestInferCurrency:
    """根据 portfolio.csv Code 推断 Currency,
    用于"新增标的"表单的智能默认值。"""

    def test_hk_prefix(self):
        """项目历史前缀 HK_ → HKD"""
        assert infer_currency('HK0700') == 'HKD'
        assert infer_currency('HK9992') == 'HKD'
        assert infer_currency('HK0883') == 'HKD'

    def test_hk_suffix(self):
        """yfinance 风格 .HK 后缀 → HKD"""
        assert infer_currency('0700.HK') == 'HKD'
        assert infer_currency('9992.HK') == 'HKD'

    def test_a_share_suffix(self):
        """.SS / .SZ 后缀 → CNY"""
        assert infer_currency('601838.SS') == 'CNY'
        assert infer_currency('002202.SZ') == 'CNY'

    def test_a_share_6digit(self):
        """6 位数字代码 → CNY (A 股 / 国内基金 / ETF)"""
        assert infer_currency('601838') == 'CNY'
        assert infer_currency('600036') == 'CNY'
        assert infer_currency('512890') == 'CNY'  # ETF
        assert infer_currency('017641') == 'CNY'  # 国内基金

    def test_sap_special_cases(self):
        """SAP 特例: SAP→USD(ADR), SAP.DE→EUR"""
        assert infer_currency('SAP') == 'USD'
        assert infer_currency('SAP.DE') == 'EUR'

    def test_empty_returns_default(self):
        """空 code 返回 CNY 兜底"""
        assert infer_currency('') == 'CNY'
        assert infer_currency(None) == 'CNY'

    def test_case_insensitive(self):
        """大小写不敏感"""
        assert infer_currency('hk0700') == 'HKD'
        assert infer_currency('0700.hk') == 'HKD'
        assert infer_currency('601838.ss') == 'CNY'

    def test_whitespace_handled(self):
        """前后空格处理"""
        assert infer_currency(' HK0700 ') == 'HKD'
        assert infer_currency(' 601838 ') == 'CNY'

    def test_unknown_returns_default(self):
        """无法识别的代码兜底返回 CNY"""
        assert infer_currency('XYZ123') == 'CNY'
        assert infer_currency('UNKNOWN') == 'CNY'

    def test_泡泡玛特_real_case(self):
        """实际场景:泡泡玛特进池 — 用户填 Code=HK9992 应自动得 HKD"""
        # 这是 2026-05-30 用户报告的真实场景
        assert infer_currency('HK9992') == 'HKD'

