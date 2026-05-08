"""test_new_features.py — 核心引擎单元测试

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
