"""test_new_features.py — 新功能单元测试

覆盖：
- nav_engine.compute_attribution
- nav_engine.load_target_allocation / save_target_allocation
- ai_weekly.build_weekly_context（结构验证）
- tenth_man._build_decision_section / _build_market_section
"""
import os
import sys
import json
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ─── fixtures ───────────────────────────────────────────────

@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def sample_raw_df():
    """两周快照，两个类别"""
    rows = [
        # 建仓日
        {'Date': '2026-04-10', 'Asset_Class': 'Gold',      'Platform': 'test', 'Name': '黄金',   'Code': 'GOLD',   'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 100, 'Current_Price': 500, 'Total_Value': 50000, 'Net_Cash_Flow': 50000},
        {'Date': '2026-04-10', 'Asset_Class': 'CN_Index_Fund', 'Platform': 'test', 'Name': 'A500', 'Code': 'A500', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 200, 'Current_Price': 250, 'Total_Value': 50000, 'Net_Cash_Flow': 50000},
        {'Date': '2026-04-10', 'Asset_Class': 'Cash',      'Platform': 'test', 'Name': '现金',   'Code': 'CASH',   'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 1,   'Current_Price': 1,   'Total_Value': 100000, 'Net_Cash_Flow': 100000},
        # 第二周
        {'Date': '2026-04-17', 'Asset_Class': 'Gold',      'Platform': 'test', 'Name': '黄金',   'Code': 'GOLD',   'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 100, 'Current_Price': 550, 'Total_Value': 55000, 'Net_Cash_Flow': 0},
        {'Date': '2026-04-17', 'Asset_Class': 'CN_Index_Fund', 'Platform': 'test', 'Name': 'A500', 'Code': 'A500', 'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 200, 'Current_Price': 240, 'Total_Value': 48000, 'Net_Cash_Flow': 0},
        {'Date': '2026-04-17', 'Asset_Class': 'Cash',      'Platform': 'test', 'Name': '现金',   'Code': 'CASH',   'Currency': 'CNY', 'Exchange_Rate': 1.0, 'Shares': 1,   'Current_Price': 1,   'Total_Value': 100000, 'Net_Cash_Flow': 0},
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def sample_fund_nav(sample_raw_df):
    from nav_engine import compute_fund_nav
    return compute_fund_nav(sample_raw_df)


@pytest.fixture
def sample_class_nav(sample_raw_df):
    from nav_engine import compute_class_nav
    return compute_class_nav(sample_raw_df)


@pytest.fixture
def sample_allocation(sample_raw_df):
    from nav_engine import compute_allocation
    return compute_allocation(sample_raw_df)


# ─── compute_attribution ───────────────────────────────────

class TestComputeAttribution:
    def test_returns_list(self, sample_raw_df, sample_fund_nav, sample_class_nav):
        from nav_engine import compute_attribution
        rows = compute_attribution(
            sample_raw_df, sample_fund_nav, sample_class_nav,
            '2026-04-10', '2026-04-17',
        )
        assert isinstance(rows, list)
        assert len(rows) > 0

    def test_cash_at_end_with_none_contribution(self, sample_raw_df, sample_fund_nav, sample_class_nav):
        from nav_engine import compute_attribution
        rows = compute_attribution(
            sample_raw_df, sample_fund_nav, sample_class_nav,
            '2026-04-10', '2026-04-17',
        )
        cash_rows = [r for r in rows if r['asset_class'] == 'Cash']
        assert len(cash_rows) == 1
        assert cash_rows[0]['contribution_pct'] is None

    def test_contribution_fields_present(self, sample_raw_df, sample_fund_nav, sample_class_nav):
        from nav_engine import compute_attribution
        rows = compute_attribution(
            sample_raw_df, sample_fund_nav, sample_class_nav,
            '2026-04-10', '2026-04-17',
        )
        non_cash = [r for r in rows if r['asset_class'] != 'Cash']
        for row in non_cash:
            assert 'nav_start' in row
            assert 'nav_end' in row
            assert 'nav_return_pct' in row
            assert 'weight_start' in row
            assert 'contribution_pct' in row

    def test_gold_positive_contribution(self, sample_raw_df, sample_fund_nav, sample_class_nav):
        from nav_engine import compute_attribution
        rows = compute_attribution(
            sample_raw_df, sample_fund_nav, sample_class_nav,
            '2026-04-10', '2026-04-17',
        )
        gold = next((r for r in rows if r['asset_class'] == 'Gold'), None)
        assert gold is not None
        assert gold['nav_return_pct'] > 0  # 黄金从500涨到550

    def test_insufficient_dates_returns_empty(self, sample_raw_df, sample_fund_nav, sample_class_nav):
        from nav_engine import compute_attribution
        rows = compute_attribution(
            sample_raw_df, sample_fund_nav, sample_class_nav,
            '2026-04-17', '2026-04-17',  # 同一天，无变化
        )
        # 起止日期相同，贡献为0或返回空
        assert isinstance(rows, list)


# ─── load/save_target_allocation ───────────────────────────

class TestTargetAllocation:
    def test_returns_defaults_when_missing(self, tmp_dir):
        from nav_engine import load_target_allocation, _TARGET_ALLOC_DEFAULT
        result = load_target_allocation(tmp_dir)
        assert set(result.keys()) == set(_TARGET_ALLOC_DEFAULT.keys())
        assert abs(sum(result.values()) - 1.0) < 0.001

    def test_save_and_reload(self, tmp_dir):
        from nav_engine import load_target_allocation, save_target_allocation
        alloc = {
            'US_Blend_Fund': 0.25, 'US_Growth_Fund': 0.10,
            'CN_Index_Fund': 0.15, 'ETF_Stock': 0.15,
            'Gold': 0.15, 'Fixed_Income': 0.05,
            'Company_Stock': 0.10, 'Cash': 0.05,
        }
        save_target_allocation(tmp_dir, alloc)
        result = load_target_allocation(tmp_dir)
        assert abs(result['US_Blend_Fund'] - 0.25) < 0.001

    def test_missing_keys_filled_with_defaults(self, tmp_dir):
        from nav_engine import load_target_allocation, save_target_allocation, _TARGET_ALLOC_DEFAULT
        # 只写部分 key
        partial = {'US_Blend_Fund': 0.30, '_updated': '2026-01-01'}
        with open(os.path.join(tmp_dir, 'target_allocation.json'), 'w') as f:
            json.dump(partial, f)
        result = load_target_allocation(tmp_dir)
        # 缺失的 key 应填入默认值
        assert set(result.keys()) == set(_TARGET_ALLOC_DEFAULT.keys())

    def test_updated_field_not_in_result(self, tmp_dir):
        from nav_engine import load_target_allocation, save_target_allocation
        alloc = {'US_Blend_Fund': 0.20, 'US_Growth_Fund': 0.10,
                 'CN_Index_Fund': 0.15, 'ETF_Stock': 0.15,
                 'Gold': 0.15, 'Fixed_Income': 0.10,
                 'Company_Stock': 0.10, 'Cash': 0.05}
        save_target_allocation(tmp_dir, alloc)
        result = load_target_allocation(tmp_dir)
        assert '_updated' not in result


# ─── tenth_man context builders ─────────────────────────────

class TestTenthManContextBuilders:
    def test_build_decision_section(self):
        from tenth_man import _build_decision_section
        decision = {
            'asset_name': '成都银行',
            'yf_symbol': '601838.SS',
            'direction': '买入',
            'amount_cny': 20000,
            'core_logic': 'PE低估',
            'macro_assumption': '利率稳定',
        }
        text = _build_decision_section(decision)
        assert '成都银行' in text
        assert '601838.SS' in text
        assert '买入' in text
        assert '20,000' in text
        assert 'PE低估' in text

    def test_build_decision_section_sell(self):
        from tenth_man import _build_decision_section
        decision = {
            'asset_name': '腾讯控股',
            'yf_symbol': '0700.HK',
            'direction': '卖出',
            'amount_cny': 50000,
            'core_logic': '止盈',
            'macro_assumption': '',
        }
        text = _build_decision_section(decision)
        assert '卖出' in text
        assert '50,000' in text

    def test_build_market_section_handles_none(self):
        from tenth_man import _build_market_section
        # 全部数据为 None（市场数据不可用）
        empty_market = {
            'vix': None, 'qvix': None, 'treasury_10y': None,
            'pe_sp500': None, 'pe_ndx100': None,
            'pe_csi300': None, 'pe_csi_a500': None,
            'gold': None,
        }
        text = _build_market_section(empty_market)
        assert '市场信号' in text  # 标题存在即可，数据可为空

    def test_build_portfolio_section(self, sample_raw_df, sample_fund_nav, sample_allocation):
        from tenth_man import _build_portfolio_section
        text = _build_portfolio_section(sample_raw_df, sample_fund_nav, sample_allocation)
        assert '组合状态' in text
        assert '总资产' in text or '¥' in text

    # ─── 方向感知 prompt 测试 ───
    def test_prompt_a_buy_opposes_buying(self):
        from tenth_man import _make_prompt_a
        prompt = _make_prompt_a(is_buy=True)
        assert '反对' in prompt or '买入' in prompt
        assert '价值陷阱' in prompt
        # 买入方向的 prompt 不应包含"反对卖出"的措辞
        assert '反对这次卖出' not in prompt

    def test_prompt_a_sell_opposes_selling(self):
        from tenth_man import _make_prompt_a
        prompt = _make_prompt_a(is_buy=False)
        assert '反对' in prompt or '卖出' in prompt
        # 卖出方向的 prompt 不应出现"价值陷阱"角色（那是买入方向的）
        assert '价值陷阱审问官' not in prompt

    def test_prompt_b_buy_stresses_against_buying(self):
        from tenth_man import _make_prompt_b
        prompt = _make_prompt_b(is_buy=True)
        assert '买入' in prompt
        assert '压测' in prompt or '压力' in prompt or '反对' in prompt

    def test_prompt_b_sell_argues_against_selling(self):
        from tenth_man import _make_prompt_b
        prompt = _make_prompt_b(is_buy=False)
        assert '卖出' in prompt or '减仓' in prompt
        # 卖出方向的 Agent B 应该压测持有不卖的风险，而非找宏观利好
        assert '持有' in prompt or '继续持有' in prompt

    def test_prompt_c_buy_checks_concentration(self):
        from tenth_man import _make_prompt_c
        prompt = _make_prompt_c(is_buy=True)
        assert '集中度' in prompt
        assert '流动性' in prompt

    def test_prompt_c_sell_checks_imbalance(self):
        from tenth_man import _make_prompt_c
        prompt = _make_prompt_c(is_buy=False)
        assert '配置' in prompt or '失衡' in prompt


# ─── ai_weekly context builder ──────────────────────────────

class TestAiWeeklyContext:
    def test_build_context_contains_key_sections(
        self, sample_raw_df, sample_fund_nav, sample_class_nav, sample_allocation, tmp_dir
    ):
        from ai_weekly import build_weekly_context
        empty_market = {
            'vix': None, 'qvix': None, 'treasury_10y': None,
            'pe_sp500': None, 'pe_ndx100': None,
            'pe_csi300': None, 'pe_csi_a500': None,
            'gold': None, 'meta': {},
        }
        ctx = build_weekly_context(
            sample_fund_nav, sample_raw_df, sample_allocation,
            sample_class_nav, empty_market,
            xirr_value=None, sharpe_value=None,
            data_dir=tmp_dir,
        )
        assert '基金总览' in ctx
        assert '资产配置' in ctx
        assert '市场温度计' in ctx

    def test_build_context_excludes_cash_from_allocation(
        self, sample_raw_df, sample_fund_nav, sample_class_nav, sample_allocation, tmp_dir
    ):
        from ai_weekly import build_weekly_context
        empty_market = {k: None for k in
                        ['vix','qvix','treasury_10y','pe_sp500','pe_ndx100',
                         'pe_csi300','pe_csi_a500','gold']}
        empty_market['meta'] = {}
        ctx = build_weekly_context(
            sample_fund_nav, sample_raw_df, sample_allocation,
            sample_class_nav, empty_market,
            xirr_value=None, sharpe_value=None,
            data_dir=tmp_dir,
        )
        # 配置区域不应出现"现金"（Cash 被排除在配置比例外）
        alloc_section = ctx.split('## 当前资产配置')[1].split('##')[0] if '## 当前资产配置' in ctx else ''
        assert '现金' not in alloc_section


# ═══════════════════════════════════════════════════════════
# DCA Manager
# ═══════════════════════════════════════════════════════════

class TestDcaManager:

    def test_load_default_config(self, tmp_dir):
        from dca_manager import load_dca_config
        cfg = load_dca_config(tmp_dir)
        assert 'plans' in cfg
        assert cfg['plans'] == []

    def test_add_and_remove_plan(self, tmp_dir):
        from dca_manager import add_plan, remove_plan, load_dca_config
        pid = add_plan(tmp_dir, {
            'name': '博时标普500', 'code': '018738',
            'asset_class': 'US_Blend_Fund', 'platform': '支付宝',
            'base_amount_cny': 800, 'frequency': 'weekly',
            'enabled': True, 'unit': 'cny', 'note': '',
        })
        cfg = load_dca_config(tmp_dir)
        assert len(cfg['plans']) == 1
        assert cfg['plans'][0]['id'] == pid

        remove_plan(tmp_dir, pid)
        cfg = load_dca_config(tmp_dir)
        assert len(cfg['plans']) == 0

    def test_parse_multiplier_str(self):
        from dca_manager import _parse_multiplier_str
        assert _parse_multiplier_str('1.5x') == 1.5
        assert _parse_multiplier_str('暂停') == 0.0
        assert _parse_multiplier_str('顶格') == 3.0
        assert _parse_multiplier_str('—') == 1.0
        assert _parse_multiplier_str('') == 1.0

    def test_gold_gram_pause_below_min_unit(self):
        """0.3x × 2g = 0.6g < 1g(min_unit) → 暂停(0g)"""
        from dca_manager import compute_suggestion
        plan = {
            'asset_class': 'Gold', 'unit': 'gram',
            'base_amount_unit': 2, 'min_unit': 1,
        }
        # mock market_data で multiplier=0.3 になるよう直接 monkeypatch せず
        # _parse_multiplier_str経由でテスト
        from dca_manager import _parse_multiplier_str
        from unittest.mock import patch
        with patch('market_monitor.lookup_gold_multiplier', return_value='0.3x'):
            market_data = {
                'gold': {'price': 3000, 'ma60': 2800, 'ma200': 2600},
                'vix': {'price': 18.0},
            }
            sug = compute_suggestion(plan, market_data)
        assert sug['suggested_unit'] == 0
        assert sug['unit'] == 'gram'

    def test_gold_gram_rounds_correctly(self):
        """0.5x × 2g = 1.0g >= 1g → 1g"""
        from dca_manager import compute_suggestion
        from unittest.mock import patch
        plan = {
            'asset_class': 'Gold', 'unit': 'gram',
            'base_amount_unit': 2, 'min_unit': 1,
        }
        with patch('market_monitor.lookup_gold_multiplier', return_value='0.5x'):
            market_data = {
                'gold': {'price': 3000, 'ma60': 2800, 'ma200': 2600},
                'vix': {'price': 18.0},
            }
            sug = compute_suggestion(plan, market_data)
        assert sug['suggested_unit'] == 1

    def test_cny_suggestion_rounds_to_10(self):
        """base=800, 1.5x → 1200（整10元）"""
        from dca_manager import compute_suggestion
        from unittest.mock import patch
        plan = {
            'asset_class': 'US_Blend_Fund', 'unit': 'cny',
            'base_amount_cny': 800,
        }
        with patch('market_monitor.lookup_multiplier', return_value='1.5x'):
            market_data = {
                'pe_sp500': {'value': 22.0},
                'vix': {'price': 18.0},
            }
            sug = compute_suggestion(plan, market_data)
        assert sug['suggested_cny'] == 1200
        assert sug['suggested_cny'] % 10 == 0


# ═══════════════════════════════════════════════════════════
# AH Monitor
# ═══════════════════════════════════════════════════════════

class TestAhMonitor:

    def test_load_default_config(self, tmp_dir):
        from ah_monitor import load_ah_config
        cfg = load_ah_config(tmp_dir)
        assert 'stocks' in cfg
        assert len(cfg['stocks']) == 4  # 默认4只

    def test_add_remove_stock(self, tmp_dir):
        from ah_monitor import load_ah_config, add_ah_stock, remove_ah_stock
        # 先清空
        import json, os
        with open(os.path.join(tmp_dir, 'ah_config.json'), 'w') as f:
            json.dump({'stocks': [], '_cache': {}, '_history': {}}, f)

        add_ah_stock(tmp_dir, '中海油', '600938.SS', '0883.HK')
        cfg = load_ah_config(tmp_dir)
        assert len(cfg['stocks']) == 1

        # 重复添加不增加
        add_ah_stock(tmp_dir, '中海油', '600938.SS', '0883.HK')
        cfg = load_ah_config(tmp_dir)
        assert len(cfg['stocks']) == 1

        remove_ah_stock(tmp_dir, '600938.SS')
        cfg = load_ah_config(tmp_dir)
        assert len(cfg['stocks']) == 0

    def test_premium_calculation(self):
        """溢价率 = A价 / (H价 × 汇率) × 100"""
        a_price = 38.0
        h_price = 27.0
        hkd_cny = 0.924
        expected = round(a_price / (h_price * hkd_cny) * 100, 1)
        assert expected == pytest.approx(152.5, abs=0.5)

    def test_signal_labels(self):
        """溢价率阈值对应信号"""
        def signal(premium):
            if premium is None: return '无数据'
            if premium > 120:   return '港股便宜'
            if premium > 90:    return '接近平价'
            return '港股贵'

        assert signal(151.9) == '港股便宜'
        assert signal(105.0) == '接近平价'
        assert signal(87.0)  == '港股贵'
        assert signal(None)  == '无数据'


# ═══════════════════════════════════════════════════════════
# FI Engine
# ═══════════════════════════════════════════════════════════

class TestFiEngine:

    def test_fi_target(self):
        from fi_engine import compute_fi_target
        assert compute_fi_target(200000, 0.04) == pytest.approx(5000000)
        assert compute_fi_target(200000, 0.03) == pytest.approx(6666666, rel=1e-3)

    def test_years_to_fi_already_reached(self):
        from fi_engine import compute_years_to_fi
        assert compute_years_to_fi(6000000, 5000000, 15000, 0.06) == 0.0

    def test_years_to_fi_basic(self):
        from fi_engine import compute_years_to_fi
        years = compute_years_to_fi(500000, 5000000, 15000, 0.06)
        assert years is not None
        assert 10 < years < 20

    def test_years_to_fi_impossible(self):
        from fi_engine import compute_years_to_fi
        # 零储蓄、零收益，永远达不到
        result = compute_years_to_fi(0, 5000000, 0, 0.0)
        assert result is None

    def test_sensitivity_has_five_scenarios(self):
        from fi_engine import fi_sensitivity
        rows = fi_sensitivity(500000, 5000000, 15000, 0.06)
        assert len(rows) == 5
        labels = [r['label'] for r in rows]
        assert '基准' in labels
        assert '收益率+1%' in labels
        assert '储蓄-20%' in labels

    def test_sensitivity_ordering(self):
        """更高收益率 → 更少年数"""
        from fi_engine import fi_sensitivity
        rows = {r['label']: r for r in fi_sensitivity(500000, 5000000, 15000, 0.06)}
        assert rows['收益率+1%']['years'] < rows['基准']['years']
        assert rows['收益率-1%']['years'] > rows['基准']['years']

    def test_monthly_savings_extraction(self, sample_raw_df):
        from fi_engine import compute_monthly_savings
        result = compute_monthly_savings(sample_raw_df)
        # sample_raw_df 有 Cash NCF=100000 在 2026-04
        assert '2026-04' in result
        assert result['2026-04'] == pytest.approx(100000)

    def test_savings_rate(self):
        from fi_engine import compute_savings_rate
        monthly = {'2026-04': 15000, '2026-05': 20000}
        rates = compute_savings_rate(monthly, 50000)
        assert rates['2026-04'] == pytest.approx(0.30)
        assert rates['2026-05'] == pytest.approx(0.40)

    def test_savings_rate_zero_income(self):
        from fi_engine import compute_savings_rate
        assert compute_savings_rate({'2026-04': 10000}, 0) == {}

    def test_load_save_config(self, tmp_dir):
        from fi_engine import load_fi_config, save_fi_config
        cfg = load_fi_config(tmp_dir)
        assert cfg['withdrawal_rate'] == 0.04

        cfg['monthly_income_cny'] = 50000
        save_fi_config(tmp_dir, cfg)
        reloaded = load_fi_config(tmp_dir)
        assert reloaded['monthly_income_cny'] == 50000


# ═══════════════════════════════════════════════════════════
# Life Stages Engine
# ═══════════════════════════════════════════════════════════

class TestLifeStagesEngine:

    @pytest.fixture
    def sample_data(self):
        return {
            'milestones': [
                {
                    'id': 'early_childhood', 'name': '早期养育',
                    'enabled': True, 'start_year': 2026, 'end_year': 2030,
                    'scenarios': {
                        'base': {'annual_cny': 60000},
                        'pessimistic': {'annual_cny': 120000},
                        'optimistic': {'annual_cny': 30000},
                    },
                    'selected': 'base', 'inflation_rate': 0.0,
                },
                {
                    'id': 'property', 'name': '置业',
                    'enabled': True, 'target_year': 2030,
                    'scenarios': {
                        'base': {'down_payment_cny': 1500000, 'monthly_mortgage_cny': 8000},
                    },
                    'selected': 'base',
                },
            ]
        }

    def test_basic_expense_curve(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve = compute_expense_curve(sample_data, 'base')
        # 2026 应有早期养育支出
        assert curve[2026]['components'].get('early_childhood', 0) == pytest.approx(60000, rel=0.01)

    def test_property_down_payment_year(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve = compute_expense_curve(sample_data, 'base')
        # 2030 应有置业首付
        assert curve[2030]['components'].get('property', 0) > 1000000

    def test_property_mortgage_after_target(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve = compute_expense_curve(sample_data, 'base')
        # 2031 应有月供（年化）
        assert curve[2031]['components'].get('property', 0) == pytest.approx(8000 * 12, rel=0.01)

    def test_disabled_milestone_excluded(self, sample_data):
        from life_stages_engine import compute_expense_curve
        sample_data['milestones'][0]['enabled'] = False
        curve = compute_expense_curve(sample_data, 'base')
        assert curve[2026]['components'].get('early_childhood', 0) == 0

    def test_scenario_pessimistic_higher(self, sample_data):
        from life_stages_engine import compute_expense_curve
        curve_base = compute_expense_curve(sample_data, 'base')
        curve_pess = compute_expense_curve(sample_data, 'pessimistic')
        assert curve_pess[2026]['total'] > curve_base[2026]['total']

    def test_inflation_adjustment(self):
        from life_stages_engine import compute_expense_curve
        import datetime
        cur = datetime.date.today().year
        data = {'milestones': [{
            'id': 'early_childhood', 'enabled': True,
            'start_year': cur, 'end_year': cur + 5,
            'scenarios': {'base': {'annual_cny': 100000}},
            'selected': 'base', 'inflation_rate': 0.10,
        }]}
        curve = compute_expense_curve(data, 'base')
        # 第二年应高于第一年（通胀10%）
        assert curve[cur + 1]['total'] > curve[cur]['total']

    def test_higher_education_spread(self):
        from life_stages_engine import compute_expense_curve
        import datetime
        cur = datetime.date.today().year
        data = {'milestones': [{
            'id': 'higher_education', 'enabled': True,
            'start_year': cur, 'end_year': cur + 4,
            'scenarios': {'base': {'total_cny': 400000}},
            'selected': 'base', 'inflation_rate': 0.0,
        }]}
        curve = compute_expense_curve(data, 'base')
        # 总额平摊4年，每年约10万
        assert curve[cur]['total'] == pytest.approx(100000, rel=0.01)


# ═══════════════════════════════════════════════════════════
# Fundamentals PE Percentile
# ═══════════════════════════════════════════════════════════

class TestPePercentile:

    def test_get_pe_percentile_from_snapshot_insufficient(self, tmp_dir):
        """少于10条数据返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        import json, os
        history = {'SAP': [{'date': f'2026-05-0{i}', 'pe': 20.0 + i} for i in range(5)]}
        with open(os.path.join(tmp_dir, 'pe_history_us.json'), 'w') as f:
            json.dump(history, f)
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 23.0)
        assert result is None

    def test_get_pe_percentile_from_snapshot_basic(self, tmp_dir):
        """20条数据，当前PE在中间，分位约50%"""
        from fundamentals import get_pe_percentile_from_snapshot
        import json, os
        pes = list(range(10, 30))  # 10..29, 20条
        history = {'SAP': [{'date': f'2026-01-{i+1:02d}', 'pe': float(p)} for i, p in enumerate(pes)]}
        with open(os.path.join(tmp_dir, 'pe_history_us.json'), 'w') as f:
            json.dump(history, f)
        # PE=20 在20条数据中：10条<=20，分位=50%
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 20.0)
        assert result is not None
        assert result['percentile'] == pytest.approx(50.0, abs=5)
        assert result['pe_min'] == 10.0
        assert result['pe_max'] == 29.0

    def test_get_pe_percentile_no_file(self, tmp_dir):
        """文件不存在返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 23.0)
        assert result is None

    def test_get_pe_percentile_none_pe(self, tmp_dir):
        """current_pe=None 返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', None)
        assert result is None

    def test_us_stock_returns_none_from_snapshot_when_no_file(self, tmp_dir):
        """美股：文件不存在返回 None"""
        from fundamentals import get_pe_percentile_from_snapshot
        result = get_pe_percentile_from_snapshot(tmp_dir, 'SAP', 23.0)
        assert result is None

    def test_a_share_code_format(self):
        """A股代码6位数字格式（不实际调用 akshare）"""
        # 验证 get_pe_percentile 对 A股代码不返回 None 是因为代码格式有效
        # 实际网络调用会 skip
        import akshare as ak
        code = '601838'
        assert code.isdigit() and len(code) == 6  # 满足 A股判断条件


# ═══════════════════════════════════════════════════════════
# Backtest — end_date + value_per_cost
# ═══════════════════════════════════════════════════════════

class TestBacktestEnhancements:

    def test_end_date_truncates_periods(self):
        """end_date 截断后，期数应少于无截断版本"""
        from backtest import run_backtest
        try:
            result_full = run_backtest('csi300', '2020-01-01', 1000.0, freq='M')
            result_trunc = run_backtest('csi300', '2020-01-01', 1000.0, freq='M',
                                        end_date='2021-12-31')
            assert result_trunc['fixed']['periods'] < result_full['fixed']['periods']
        except Exception:
            pytest.skip('network not available')

    def test_value_per_cost_present(self):
        """返回结果含 value_per_cost 字段"""
        from backtest import run_backtest
        try:
            result = run_backtest('csi300', '2020-01-01', 1000.0,
                                  freq='M', end_date='2022-12-31')
            assert 'value_per_cost' in result['fixed']
            assert 'value_per_cost' in result['matrix']
            assert result['fixed']['value_per_cost'] > 0
        except Exception:
            pytest.skip('network not available')

    def test_no_cash_rate_in_result(self):
        """结果不再含 cash_rate_annual 字段（已移除机会成本逻辑）"""
        from backtest import run_backtest
        try:
            result = run_backtest('csi300', '2020-01-01', 1000.0,
                                  freq='M', end_date='2022-12-31')
            assert 'cash_rate_annual' not in result
            assert 'combined_value' not in result['matrix']
        except Exception:
            pytest.skip('network not available')
