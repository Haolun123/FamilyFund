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
    {'code': '017641', 'name': '摩根标普500 A类'},
    {'code': '019172', 'name': '摩根纳指100 A类'},
    {'code': '539001', 'name': '建信纳指100 A类'},
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


# ── 回归测试: 份额含千分位逗号 (2026-05-30 bug) ──────────────────


# 份额超过 1000 时,银行/基金公司短信用千分位逗号分隔
# 之前 _PAT_B 份额正则 ([\d.]+) 不匹配逗号,导致整条解析失败
SMS_NANFANG_LARGE_SHARES = """【南方基金】尊敬的严浩伦先生，您5月26日定投南方中证A500ETF联接A基金1,680.00元于5月27日确认成功，确认份额1,266.20份，成交净值1.3268。份额持有时间自确认成功之日起计算。"""

# 假设场景:博时申购份额过千(虽然历史没出现,防御性测试 _PAT_A)
SMS_BOSHI_LARGE_SHARES = """【博时基金】尊敬的严浩伦，您于2026年05月06日通过博时直销申购博时标普500ETF联接E  6000元05月08日确认成功，份额为1,104.65份，净值为5.4308。"""

# 博时定投份额过千 (_PAT_A2)
SMS_BOSHI_DCA_LARGE = """【博时基金】尊敬的严浩伦，2026年05月26日您通过博时直销设置的定期定投（博时钱包支付）博时标普500ETF联接E 6000元确认成功，份额为1,087.05份，净值为5.5195。"""

# 南方申购份额过千 (_PAT_B2)
SMS_NANFANG_BUY_LARGE = """【南方基金】尊敬的严浩伦先生，您5月26日申购南方中证A500ETF联接A基金2,000.00元于5月27日确认成功，确认份额为1,507.42份，成交净值为1.3268。"""


class TestSharesWithComma:
    """份额含千分位逗号(超过 1000 份)各格式回归测试。
    2026-05-30 bug: A500 联接首次定投到 1266 份, _PAT_B 份额正则
    [\\d.]+ 不匹配逗号, 整条短信解析失败."""

    def test_pat_b_large_shares(self):
        """_PAT_B 南方定投: 1,266.20 份"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_NANFANG_LARGE_SHARES, HOLDINGS)[0]
        assert not r.get('parse_error'), "应解析成功"
        assert r['shares'] == pytest.approx(1266.20)
        assert r['amount'] == pytest.approx(1680.00)
        assert r['nav'] == pytest.approx(1.3268)

    def test_pat_a_large_shares(self):
        """_PAT_A 博时申购: 1,104.65 份"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI_LARGE_SHARES, HOLDINGS)[0]
        assert not r.get('parse_error'), "应解析成功"
        assert r['shares'] == pytest.approx(1104.65)

    def test_pat_a2_large_shares(self):
        """_PAT_A2 博时定投: 1,087.05 份"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_BOSHI_DCA_LARGE, HOLDINGS)[0]
        assert not r.get('parse_error'), "应解析成功"
        assert r['shares'] == pytest.approx(1087.05)

    def test_pat_b2_large_shares(self):
        """_PAT_B2 南方申购: 1,507.42 份"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_NANFANG_BUY_LARGE, HOLDINGS)[0]
        assert not r.get('parse_error'), "应解析成功"
        assert r['shares'] == pytest.approx(1507.42)


# ── 回归测试: 摩根基金格式 (2026-07-19 新增) ──────────────────

SMS_JPMORGAN_BUY = "【摩根基金】尊敬的客户，您于7月14日通过摩根基金成功申购摩根标普500人民币A300.00元，成交净值1.6808，确认份额178.49份，手续费0.00元，该笔份额持有时间从7月16日开始计算。"
SMS_JPMORGAN_DCA = "【摩根基金】尊敬的客户，您于7月15日通过摩根基金成功定期定额申购摩根标普500人民币A300.00元，成交净值1.6849，确认份额176.99份，该笔份额持有时间从7月17日开始计算。"


class TestParseJPMorgan:
    """摩根基金格式（格式E）回归测试。
    确认日 = 持有起始日 - 1天（持有起始日为T+2，确认日为T+1）。
    """

    def test_buy_confirm_date(self):
        """申购：持有从7月16日，确认日为7月15日"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_BUY, HOLDINGS)[0]
        assert not r.get('parse_error'), "应解析成功"
        assert r['confirm_date'] == '2026-07-15'

    def test_dca_confirm_date(self):
        """定期定额申购：持有从7月17日，确认日为7月16日"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_DCA, HOLDINGS)[0]
        assert not r.get('parse_error'), "应解析成功"
        assert r['confirm_date'] == '2026-07-16'

    def test_buy_amount(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_BUY, HOLDINGS)[0]
        assert r['amount'] == pytest.approx(300.0)

    def test_buy_shares(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_BUY, HOLDINGS)[0]
        assert r['shares'] == pytest.approx(178.49)

    def test_buy_nav(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_BUY, HOLDINGS)[0]
        assert r['nav'] == pytest.approx(1.6808)

    def test_dca_shares(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_DCA, HOLDINGS)[0]
        assert r['shares'] == pytest.approx(176.99)

    def test_fund_name_parsed(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_BUY, HOLDINGS)[0]
        assert '摩根标普500' in r['fund_name']

    def test_matched_code(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_BUY, HOLDINGS)[0]
        assert r['matched_code'] == '017641'

    def test_action_is_buy(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JPMORGAN_BUY, HOLDINGS)[0]
        assert r['action'] == '买入'



# ── 回归测试: 摩根合并短信(一条含多笔) (2026-07-25 bug) ──────────────
# 摩根会把当日确认的多笔交易(标普+纳指)塞进【同一条无空行短信】。
# 旧实现每个块只 _parse_one 一次, 只提取第一笔, 丢弃其余。
# 修复: parse_sms 对每个块用 _PAT_E.finditer 提取全部摩根交易。

# 一条短信含 2 笔: 7/21 纳指 + 7/21 标普
SMS_JPM_MERGED_1 = "【摩根基金】尊敬的客户，您于7月21日通过摩根基金成功申购摩根纳斯达克100人民币A300.00元，成交净值1.7338，确认份额173.03份，手续费0.00元，该笔份额持有时间从7月23日开始计算。您于7月21日通过摩根基金成功定期定额申购摩根标普500人民币A300.00元，成交净值1.6718，确认份额178.38份，该笔份额持有时间从7月23日开始计算。"

# 一条短信含 2 笔: 7/22 标普 + 7/22 纳指
SMS_JPM_MERGED_2 = "【摩根基金】尊敬的客户，您于7月22日通过摩根基金成功定期定额申购摩根标普500人民币A300.00元，成交净值1.6700，确认份额178.57份，该笔份额持有时间从7月24日开始计算。您于7月22日通过摩根基金成功定期定额申购摩根纳斯达克100人民币A300.00元，成交净值1.7256，确认份额172.82份，该笔份额持有时间从7月24日开始计算。"


class TestJPMorganMergedSms:
    """摩根合并短信: 一条短信含多笔交易, 必须全部解析出来。"""

    def test_single_sms_two_transactions(self):
        """一条短信含 2 笔, 应解析出 2 条"""
        from sms_parser import parse_sms
        results = parse_sms(SMS_JPM_MERGED_1, HOLDINGS)
        assert len(results) == 2, f"应解析出 2 笔, 实际 {len(results)}"

    def test_merged_no_transaction_dropped(self):
        """两条合并短信共 4 笔, 一笔都不能丢"""
        from sms_parser import parse_sms
        results = parse_sms(SMS_JPM_MERGED_1 + "\n\n" + SMS_JPM_MERGED_2, HOLDINGS)
        assert len(results) == 4, f"应解析出 4 笔, 实际 {len(results)}"
        assert all(not r.get('parse_error') for r in results)

    def test_both_funds_matched(self):
        """标普和纳指都要正确匹配到持仓"""
        from sms_parser import parse_sms
        results = parse_sms(SMS_JPM_MERGED_1, HOLDINGS)
        codes = {r['matched_code'] for r in results}
        assert codes == {'017641', '019172'}, f"匹配码应为标普+纳指, 实际 {codes}"

    def test_nasdaq_new_holding_matched(self):
        """摩根纳斯达克100人民币A → 匹配摩根纳指100 A类(019172)"""
        from sms_parser import parse_sms
        results = parse_sms(SMS_JPM_MERGED_1, HOLDINGS)
        ndx = [r for r in results if r['matched_code'] == '019172']
        assert len(ndx) == 1
        assert ndx[0]['nav'] == pytest.approx(1.7338)
        assert ndx[0]['shares'] == pytest.approx(173.03)

    def test_second_transaction_values(self):
        """验证第二笔(标普)的数值没有被第一笔污染"""
        from sms_parser import parse_sms
        results = parse_sms(SMS_JPM_MERGED_1, HOLDINGS)
        sp = [r for r in results if r['matched_code'] == '017641']
        assert len(sp) == 1
        assert sp[0]['nav'] == pytest.approx(1.6718)
        assert sp[0]['shares'] == pytest.approx(178.38)
        assert sp[0]['confirm_date'] == '2026-07-22'  # 持有 7/23 - 1 天


# ── 回归测试: 建信基金格式 F (2026-08-14 新增) ──────────────────
# 建信基金短信无净值字段，日期为 YYYYMMDD 8位数字格式，净值需从金额/份额反算。

SMS_JIANXIN_DCA = "【建信基金】您于20260812提交的建信纳斯达克100指数(QDII)A人民币500.00元定期定额申购申请已确认成功,确认份额143.78份。拒收请回复R"


class TestParseJianXin:
    """建信基金格式（格式F）回归测试。净值 = 金额 / 份额（反算）。"""

    def test_confirm_date(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JIANXIN_DCA, HOLDINGS)[0]
        assert not r.get('parse_error'), "应解析成功"
        assert r['confirm_date'] == '2026-08-12'

    def test_amount(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JIANXIN_DCA, HOLDINGS)[0]
        assert r['amount'] == pytest.approx(500.0)

    def test_shares(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JIANXIN_DCA, HOLDINGS)[0]
        assert r['shares'] == pytest.approx(143.78)

    def test_nav_computed(self):
        """净值 = 500 / 143.78 ≈ 3.4776"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_JIANXIN_DCA, HOLDINGS)[0]
        assert r['nav'] == pytest.approx(500.0 / 143.78, rel=1e-3)

    def test_matched_code(self):
        """新建仓时 holdings 里还没有该基金，matched_code 为 None 是正常的"""
        from sms_parser import parse_sms
        r = parse_sms(SMS_JIANXIN_DCA, HOLDINGS)[0]
        assert r['matched_code'] is None

    def test_action_is_buy(self):
        from sms_parser import parse_sms
        r = parse_sms(SMS_JIANXIN_DCA, HOLDINGS)[0]
        assert r['action'] == '买入'
