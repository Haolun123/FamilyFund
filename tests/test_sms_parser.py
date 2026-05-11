"""test_sms_parser.py — SMS 解析器单元测试"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

HOLDINGS = [
    {'code': '018738', 'name': '博时标普500 E类'},
    {'code': '022434', 'name': '南方中证A500 A类'},
    {'code': '021000', 'name': '南方纳指100 I类'},
    {'code': 'GOLD',   'name': '黄金'},
]

SMS_BOSHI = """【博时基金】尊敬的严浩伦，您于2026年05月06日通过博时直销申购博时标普500ETF联接E  240元05月08日确认成功，份额为44.19份，净值为5.4308。温馨提示：若赎回请先查询了解基金赎回费。详询95105568。"""

SMS_NANFANG_A500 = """【南方基金】尊敬的严浩伦先生，您5月7日定投南方中证A500ETF联接A基金1,050.00元于5月8日确认成功，确认份额797.75份，成交净值1.3162。份额持有时间自确认成功之日起计算。"""

SMS_NANFANG_NDX = """【南方基金】尊敬的严浩伦先生，您4月30日定投南方纳斯达克100指数发起（QDII）I基金800.00元于5月7日确认成功，确认份额369.50份，成交净值2.1651。"""

SMS_GOLD = """【招商银行】尊敬的客户，您的129463836850001号黄金智能定投计划已于2026年04月30日扣款成功，为您成功定投1.7000克黄金份额，扣款金额人民币1727.12元，扣款账户尾号8097"""

SMS_INVALID = "这不是一条基金短信"


class TestParseBoShi:

    def test_confirm_date(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI, HOLDINGS)[0]
        assert r['confirm_date'] == '2026-05-08'

    def test_amount(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI, HOLDINGS)[0]
        assert r['amount'] == 240.0

    def test_shares(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI, HOLDINGS)[0]
        assert r['shares'] == pytest.approx(44.19)

    def test_nav(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI, HOLDINGS)[0]
        assert r['nav'] == pytest.approx(5.4308)

    def test_matched_code(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI, HOLDINGS)[0]
        assert r['matched_code'] == '018738'

    def test_action_is_buy(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI, HOLDINGS)[0]
        assert r['action'] == '买入'


class TestParseNanfang:

    def test_a500_amount_with_comma(self):
        """金额含逗号（1,050.00）正确解析"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_NANFANG_A500, HOLDINGS)[0]
        assert r['amount'] == pytest.approx(1050.0)

    def test_a500_shares(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_NANFANG_A500, HOLDINGS)[0]
        assert r['shares'] == pytest.approx(797.75)

    def test_a500_matched(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_NANFANG_A500, HOLDINGS)[0]
        assert r['matched_code'] == '022434'

    def test_ndx_matched(self):
        """南方纳斯达克100 → 匹配到 021000"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_NANFANG_NDX, HOLDINGS)[0]
        assert r['matched_code'] == '021000'

    def test_year_inferred(self):
        """南方基金短信无年份，自动推断"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_NANFANG_A500, HOLDINGS)[0]
        assert r['confirm_date'].startswith('2026-')


class TestParseGold:

    def test_is_gold_flag(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_GOLD, HOLDINGS)[0]
        assert r['is_gold'] is True

    def test_grams(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_GOLD, HOLDINGS)[0]
        assert r['shares'] == pytest.approx(1.7)

    def test_amount(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_GOLD, HOLDINGS)[0]
        assert r['amount'] == pytest.approx(1727.12)

    def test_nav_computed(self):
        """净值 = 金额 / 克数"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_GOLD, HOLDINGS)[0]
        assert r['nav'] == pytest.approx(1727.12 / 1.7, rel=1e-3)

    def test_matched_gold_code(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_GOLD, HOLDINGS)[0]
        assert r['matched_code'] == 'GOLD'

    def test_confirm_date(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_GOLD, HOLDINGS)[0]
        assert r['confirm_date'] == '2026-04-30'


class TestParseMultiple:

    def test_four_messages(self):
        from sms_parser import parse_sms
        combined = f"{SMS_BOSHI}\n\n{SMS_NANFANG_A500}\n\n{SMS_NANFANG_NDX}\n\n{SMS_GOLD}"
        results = parse_sms(combined, HOLDINGS)
        assert len(results) == 4

    def test_all_matched(self):
        from sms_parser import parse_sms
        combined = f"{SMS_BOSHI}\n\n{SMS_NANFANG_A500}\n\n{SMS_NANFANG_NDX}\n\n{SMS_GOLD}"
        results = parse_sms(combined, HOLDINGS)
        unmatched = [r for r in results if not r['matched_code'] and not r.get('parse_error')]
        assert len(unmatched) == 0

    def test_invalid_sms_returns_parse_error(self):
        from sms_parser import parse_sms
        results = parse_sms(SMS_INVALID, HOLDINGS)
        assert len(results) == 1
        assert results[0].get('parse_error') is True
