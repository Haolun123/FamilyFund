"""cashflow_engine 单元测试。

测试策略:
  - 大部分用 mock DataFrame（不依赖真实CSV）
  - 一个集成测试用 Q1 鲨鱼记账文件（如果可访问）
"""
import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cashflow_engine import (
    parse_shark_csv,
    load_quarter_shark,
    categorize_expense,
    compute_cashflow_summary,
    aggregate_by_category,
    build_sankey_data,
    compute_net_worth_reconciliation,
    NECESSARY_CATEGORIES,
    DISCRETIONARY_CATEGORIES,
    DEBT_PRINCIPAL_CATEGORIES,
)


# ─── 类别归类 ───

class TestCategorize:
    def test_necessary(self):
        assert categorize_expense('餐饮') == '必需'
        assert categorize_expense('医疗') == '必需'
        assert categorize_expense('孩子') == '必需'

    def test_discretionary(self):
        assert categorize_expense('购物') == '可选'
        assert categorize_expense('旅行') == '可选'
        assert categorize_expense('数码') == '可选'
        # 2026-06-30 Q2 跑通后补充
        assert categorize_expense('宠物') == '可选'
        assert categorize_expense('娱乐') == '可选'

    def test_debt_principal(self):
        assert categorize_expense('债务还本') == '债务还本'

    def test_unknown_falls_to_other(self):
        assert categorize_expense('未定义分类') == '其他'

    def test_no_overlap_between_buckets(self):
        """必需/可选/债务还本三组不能有交集。"""
        assert not (NECESSARY_CATEGORIES & DISCRETIONARY_CATEGORIES)
        assert not (NECESSARY_CATEGORIES & DEBT_PRINCIPAL_CATEGORIES)
        assert not (DISCRETIONARY_CATEGORIES & DEBT_PRINCIPAL_CATEGORIES)


# ─── compute_cashflow_summary ───

def _mock_df(records):
    """构造测试用 DataFrame。"""
    rows = []
    for date_str, type_, cat, amount in records:
        rows.append({
            'Date': pd.Timestamp(date_str),
            'Type': type_,
            'Category': cat,
            'Amount': amount,
            'Note': '',
        })
    return pd.DataFrame(rows)


class TestCashflowSummary:
    def test_empty_df(self):
        result = compute_cashflow_summary(pd.DataFrame())
        assert result['income_total'] == 0
        assert result['savings_rate'] == 0

    def test_simple_balance(self):
        """收入10万 - 必需3万 - 可选1万 = 净储蓄6万,储蓄率60%。"""
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '餐饮', 30000),
            ('2026-04-03', '支出', '购物', 10000),
        ])
        r = compute_cashflow_summary(df)
        assert r['income_total'] == 100000
        assert r['expense_total'] == 40000
        assert r['expense_necessary'] == 30000
        assert r['expense_discretionary'] == 10000
        assert r['debt_principal'] == 0
        assert r['free_cashflow'] == 60000
        assert r['net_savings'] == 60000   # 无债务还本时,自由现金流==净储蓄
        assert r['savings_rate'] == 0.6
        # 日常消费=必需3w+可选1w=4w, 必需占比75%
        assert r['necessary_ratio'] == 0.75

    def test_with_debt_principal(self):
        """债务还本不算消耗:
        收入10w - 必需3w - 可选1w - 债务还本2w
        自由现金流 = 10 - (3+1+2) = 4w (真实现金流出)
        净储蓄    = 10 - (3+1+2) + 2 = 6w (本金回到净资产)
        """
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '餐饮', 30000),
            ('2026-04-03', '支出', '购物', 10000),
            ('2026-04-04', '支出', '债务还本', 20000),
        ])
        r = compute_cashflow_summary(df)
        assert r['expense_total'] == 60000   # 含债务还本
        assert r['debt_principal'] == 20000
        assert r['free_cashflow'] == 40000   # 真实现金流出 40000
        assert r['net_savings'] == 60000     # 净资产视角 60000
        assert r['savings_rate'] == 0.6
        # 必需占比:不含债务还本,3/(3+1)=75%
        assert r['necessary_ratio'] == 0.75

    def test_unknown_category_treated_as_necessary(self):
        """未归类的分类按必需处理(保守)。"""
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '神秘分类', 5000),
        ])
        r = compute_cashflow_summary(df)
        assert r['expense_necessary'] == 5000
        assert r['expense_discretionary'] == 0

    def test_zero_income_no_div_zero(self):
        """收入为0时 savings_rate 不报错。"""
        df = _mock_df([
            ('2026-04-01', '支出', '餐饮', 100),
        ])
        r = compute_cashflow_summary(df)
        assert r['savings_rate'] == 0.0
        assert r['necessary_ratio'] == 1.0  # 全是必需

    def test_negative_savings(self):
        """超支情况:收入1w,支出2w → 净储蓄 -1w。"""
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 10000),
            ('2026-04-02', '支出', '购物', 20000),
        ])
        r = compute_cashflow_summary(df)
        assert r['net_savings'] == -10000
        assert r['savings_rate'] == -1.0


# ─── aggregate_by_category ───

class TestAggregateByCategory:
    def test_basic_aggregation(self):
        df = _mock_df([
            ('2026-04-01', '支出', '餐饮', 100),
            ('2026-04-02', '支出', '餐饮', 200),
            ('2026-04-03', '支出', '购物', 500),
        ])
        agg = aggregate_by_category(df, '支出')
        assert len(agg) == 2
        # 按金额降序
        assert agg.iloc[0]['Category'] == '购物'
        assert agg.iloc[0]['Amount'] == 500
        assert agg.iloc[0]['Count'] == 1
        assert agg.iloc[1]['Category'] == '餐饮'
        assert agg.iloc[1]['Amount'] == 300
        assert agg.iloc[1]['Count'] == 2

    def test_bucket_assigned(self):
        df = _mock_df([
            ('2026-04-01', '支出', '餐饮', 100),
            ('2026-04-02', '支出', '购物', 200),
            ('2026-04-03', '支出', '债务还本', 5000),
        ])
        agg = aggregate_by_category(df, '支出')
        buckets = dict(zip(agg['Category'], agg['Bucket']))
        assert buckets['餐饮'] == '必需'
        assert buckets['购物'] == '可选'
        assert buckets['债务还本'] == '债务还本'

    def test_empty_returns_empty_df(self):
        df = _mock_df([])
        agg = aggregate_by_category(df, '支出')
        assert len(agg) == 0
        assert list(agg.columns) == ['Category', 'Amount', 'Count', 'Bucket']


# ─── build_sankey_data ───

class TestSankeyData:
    def test_basic_structure(self):
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '餐饮', 30000),
            ('2026-04-03', '支出', '购物', 10000),
        ])
        s = build_sankey_data(df)
        assert 'nodes' in s
        assert 'sources' in s
        assert 'targets' in s
        assert 'values' in s
        # 长度对齐
        assert len(s['sources']) == len(s['targets']) == len(s['values'])

    def test_flows_sum_balance(self):
        """左流入 + 右流出 应该平衡(误差<1元)。
        节点拓扑: 收入类 → 总流入 → 支出类/净储蓄
        左到中流入合计 = 总收入
        中到右流出合计 = 总支出 + 净储蓄(若正)
        两边应相等。
        """
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '餐饮', 30000),
            ('2026-04-03', '支出', '购物', 10000),
        ])
        s = build_sankey_data(df)
        nodes = s['nodes']
        hub_idx = nodes.index('总流入')

        inflow_to_hub = sum(v for src, tgt, v in zip(s['sources'], s['targets'], s['values'])
                            if tgt == hub_idx)
        outflow_from_hub = sum(v for src, tgt, v in zip(s['sources'], s['targets'], s['values'])
                               if src == hub_idx)
        assert abs(inflow_to_hub - outflow_from_hub) < 1

    def test_sankey_includes_savings_when_positive(self):
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '餐饮', 30000),
        ])
        s = build_sankey_data(df)
        assert '净储蓄' in s['nodes']


# ─── 净资产核对 ───

class TestNetWorthReconciliation:
    def test_perfect_match(self):
        """无资产估值变化情况下,残差应为0。"""
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '餐饮', 30000),
            ('2026-04-03', '支出', '债务还本', 5000),
        ])
        # 净储蓄 = 100000 - 35000 + 5000 = 70000
        # 期初100w,期末107w(净储蓄部分)
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_070_000,
        )
        assert r['shark_income'] == 100000
        assert r['shark_expense_ex_debt'] == 30000  # 剔除债务还本
        assert r['predicted_change'] == 70000
        assert r['nw_change'] == 70000
        assert r['residual'] == 0

    def test_with_asset_appreciation(self):
        """股票/房产升值会造成残差为正。"""
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
            ('2026-04-02', '支出', '餐饮', 30000),
        ])
        # 预测净资产变化 = 70000
        # 实际变化 = 100000(其中30000是资产升值)
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_100_000,
        )
        assert r['predicted_change'] == 70000
        assert r['nw_change'] == 100000
        assert r['residual'] == 30000  # 30000 来自资产升值

    def test_with_cashflow_log(self):
        """cashflow_log 中的现金流按 Type 区分纳入计算(2026-07-01 修正)。

        旧逻辑: 所有 cashflow_log 都加入预测 → 错误(卖车不创造净资产)
        新逻辑: 只把经营性(Inflow_*/Outflow_*) 加入预测,
                资本性(Capital_*) 单独展示但不入预测
        """
        df = _mock_df([
            ('2026-04-01', '收入', '工资', 100000),
        ])
        # 混合三种 Type: 资本性 + 经营性流入 + 跨季度过滤
        cfl = pd.DataFrame([
            {'Quarter': '2026Q2', 'Date': '2026-04-15', 'Amount': 200000,
             'Type': 'Capital_Inflow',  'Note': '卖车'},   # 资本性,不入预测
            {'Quarter': '2026Q2', 'Date': '2026-05-25', 'Amount':  15000,
             'Type': 'Inflow_Other',    'Note': '政府补贴'},  # 经营性,入预测
            {'Quarter': '2026Q1', 'Date': '2026-01-01', 'Amount':  99999,
             'Type': 'Inflow_Salary',   'Note': '不算在内(Q1)'},  # 跨季度,被过滤
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_115_000,
            cashflow_log=cfl, quarter='2026Q2',
        )
        # 新字段
        assert r['capital_inflow'] == 200000     # 卖车单独记录
        assert r['capital_outflow'] == 0
        assert r['operating_inflow'] == 15000    # 只有政府补贴
        assert r['operating_outflow'] == 0
        # 预测变化只含经营性: 工资 100k - 支出 0 + 经营性流入 15k = 115k
        assert r['predicted_change'] == 115000
        # 实际变化 115k → 残差 0
        assert r['residual'] == 0

    def test_capital_inflow_does_not_affect_prediction(self):
        """关键不变性: Capital_Inflow 不应该影响 predicted_change。"""
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        cfl = pd.DataFrame([
            {'Quarter': '2026Q2', 'Date': '2026-04-15', 'Amount': 500000,
             'Type': 'Capital_Inflow', 'Note': '卖房'},
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_100_000,
            cashflow_log=cfl, quarter='2026Q2',
        )
        # 即使卖房¥50万,预测只含工资 = 10万,不含资本性流入
        assert r['predicted_change'] == 100000
        assert r['capital_inflow'] == 500000  # 但单独记录可见

    def test_capital_outflow(self):
        """Capital_Outflow(大额资产购置)也不影响预测。"""
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        cfl = pd.DataFrame([
            {'Quarter': '2026Q2', 'Date': '2026-04-15', 'Amount': -200000,
             'Type': 'Capital_Outflow', 'Note': '买车付首付'},
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_100_000,
            cashflow_log=cfl, quarter='2026Q2',
        )
        assert r['predicted_change'] == 100000
        assert r['capital_outflow'] == 200000
        assert r['operating_outflow'] == 0

    def test_operating_outflow(self):
        """Outflow_Major (基金外大额经营性支出) 应该减少预测。"""
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        cfl = pd.DataFrame([
            {'Quarter': '2026Q2', 'Date': '2026-04-15', 'Amount': -30000,
             'Type': 'Outflow_Major', 'Note': '保险年费'},
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_070_000,
            cashflow_log=cfl, quarter='2026Q2',
        )
        # 预测 = 工资 100k - 0 + 0 - 30k = 70k
        assert r['predicted_change'] == 70000
        assert r['operating_outflow'] == 30000
        assert r['residual'] == 0

    def test_unknown_type_defaults_to_operating(self):
        """未知 Type 按金额符号回退到经营性(保守,避免漏算)。"""
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        cfl = pd.DataFrame([
            {'Quarter': '2026Q2', 'Date': '2026-04-15', 'Amount': 5000,
             'Type': 'SomeUnknownType', 'Note': '未来新增类型'},
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_105_000,
            cashflow_log=cfl, quarter='2026Q2',
        )
        # 未知Type按正金额→operating_in
        assert r['operating_inflow'] == 5000
        assert r['capital_inflow'] == 0

    def test_backward_compat_special_fields(self):
        """旧 UI 代码用 special_inflow/outflow 字段,新版应保持可用。"""
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        cfl = pd.DataFrame([
            {'Quarter': '2026Q2', 'Date': '2026-04-15', 'Amount': 15000,
             'Type': 'Inflow_Other', 'Note': '补贴'},
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_115_000,
            cashflow_log=cfl, quarter='2026Q2',
        )
        # 向后兼容: special_* 应该只反映经营性(不再包含资本性)
        assert r['special_inflow'] == 15000
        assert r['special_outflow'] == 0

    def test_sap_vesting_included(self):
        """SAP NCF(ESPP+RSU 归属)应纳入预测,且剔除建仓日。

        建仓日定义: portfolio 中最早的 Date(min)。
        其 NCF 是成本基准(Total_Value 全量),不算流入。
        """
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        # 建仓日 2026-04-10 +458,503(基准,不算流入),后续 +20,547(实际归属)
        portfolio = pd.DataFrame([
            {'Date': '2026-04-10', 'Asset_Class': 'Company_Stock',
             'Name': 'SAP', 'Net_Cash_Flow': 458503},
            {'Date': '2026-05-08', 'Asset_Class': 'Company_Stock',
             'Name': 'SAP', 'Net_Cash_Flow': 3292},
            {'Date': '2026-06-12', 'Asset_Class': 'Company_Stock',
             'Name': 'SAP', 'Net_Cash_Flow': 3292},
            {'Date': '2026-06-19', 'Asset_Class': 'Company_Stock',
             'Name': 'SAP', 'Net_Cash_Flow': 13963},
            # 非 Company_Stock 的 NCF 不算
            {'Date': '2026-05-22', 'Asset_Class': 'ETF_Stock',
             'Name': '腾讯', 'Net_Cash_Flow': 50000},
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_120_547,
            portfolio_df=portfolio,
            q_prev_end='2026-03-31', q_curr_end='2026-06-30',
        )
        # SAP 归属 = 3292 + 3292 + 13963 = 20547(剔除建仓日 458503)
        assert r['sap_vesting'] == 20547
        # 预测 = 100000(工资) + 20547(SAP) = 120547
        assert r['predicted_change'] == 120547
        assert r['residual'] == 0

    def test_sap_vesting_filters_outside_quarter(self):
        """季度区间外的 NCF 应被过滤,且建仓日定义符合 portfolio.csv 最早日期。"""
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        # 建仓日在 Q1(2025-12-31)，Q2 内有 NCF 应被纳入
        portfolio = pd.DataFrame([
            {'Date': '2025-12-31', 'Asset_Class': 'Company_Stock',
             'Name': 'SAP', 'Net_Cash_Flow': 500000},  # 建仓日,剔除
            {'Date': '2026-03-15', 'Asset_Class': 'Company_Stock',
             'Name': 'SAP', 'Net_Cash_Flow': 9999},     # Q1 内,被区间过滤
            {'Date': '2026-05-08', 'Asset_Class': 'Company_Stock',
             'Name': 'SAP', 'Net_Cash_Flow': 3292},     # Q2 内,纳入
        ])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_103_292,
            portfolio_df=portfolio,
            q_prev_end='2026-03-31', q_curr_end='2026-06-30',
        )
        assert r['sap_vesting'] == 3292

    def test_sap_vesting_skipped_when_no_portfolio(self):
        """portfolio_df 为 None 时 sap_vesting=0,不报错。"""
        df = _mock_df([('2026-04-01', '收入', '工资', 100000)])
        r = compute_net_worth_reconciliation(
            df, nw_prev=1_000_000, nw_curr=1_100_000,
        )
        assert r['sap_vesting'] == 0
        assert r['predicted_change'] == 100000


# ─── 集成测试:用 Q1 真实数据 ───

@pytest.mark.skipif(
    not os.path.exists(os.path.join(
        os.environ.get('FAMILYFUND_DATA', '/app/data'),
        '鲨鱼记账', '2026Q1.csv'
    )),
    reason='Q1 鲨鱼记账数据不可访问'
)
class TestQ1RealData:
    """用 Q1 的 671 行真实数据做端到端验证。"""

    @pytest.fixture
    def q1_df(self):
        data_dir = os.environ.get('FAMILYFUND_DATA', '/app/data')
        return load_quarter_shark(data_dir, '2026Q1')

    def test_q1_load_succeeds(self, q1_df):
        assert q1_df is not None
        assert len(q1_df) > 600  # 应该有671行左右

    def test_q1_has_income_and_expense(self, q1_df):
        types = q1_df['Type'].unique().tolist()
        assert '收入' in types
        assert '支出' in types

    def test_q1_summary_sanity(self, q1_df):
        """Q1 收入应该显著大于支出(已知有年终奖),净储蓄率应该很高。"""
        r = compute_cashflow_summary(q1_df)
        assert r['income_total'] > 400000  # >40万
        assert r['expense_total'] < 300000  # <30万
        assert r['savings_rate'] > 0.5     # 储蓄率>50%

    def test_q1_sankey_balanced(self, q1_df):
        """桑基图节点平衡性。"""
        s = build_sankey_data(q1_df)
        hub_idx = s['nodes'].index('总流入')
        inflow = sum(v for src, tgt, v in zip(s['sources'], s['targets'], s['values'])
                     if tgt == hub_idx)
        outflow = sum(v for src, tgt, v in zip(s['sources'], s['targets'], s['values'])
                      if src == hub_idx)
        assert abs(inflow - outflow) < 1, f'桑基图不平衡: in={inflow}, out={outflow}'
