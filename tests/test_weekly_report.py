"""test_weekly_report.py — weekly_report 模块单元测试"""
import os
import sys
import json
import tempfile
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ── 构造最小测试数据集 ──────────────────────────────────────

def _make_portfolio_csv(path: str):
    rows = [
        # 上周
        ('2026-08-07', 'US_Blend_Fund',  '标普场外', '博时标普500 E类', '018738', 'CNY', 1.0,  100.0, 5.0,  500.0, 0.0),
        ('2026-08-07', 'Gold',           '招商银行', '黄金',            'GOLD',   'CNY', 1.0,   10.0, 500.0, 5000.0, 0.0),
        ('2026-08-07', 'Company_Stock',  'SAP',      'SAP',             'SAP.DE', 'EUR', 7.80, 100.0, 100.0, 78000.0, 0.0),
        ('2026-08-07', 'Cash',           '招商银行', '现金',            'CASH',   'CNY', 1.0, 1000.0, 1.0,  1000.0, 0.0),
        # 本周
        ('2026-08-14', 'US_Blend_Fund',  '标普场外', '博时标普500 E类', '018738', 'CNY', 1.0,  110.0, 5.2,   572.0, 500.0),
        ('2026-08-14', 'Gold',           '招商银行', '黄金',            'GOLD',   'CNY', 1.0,   10.0, 510.0, 5100.0, 0.0),
        ('2026-08-14', 'Company_Stock',  'SAP',      'SAP',             'SAP.DE', 'EUR', 7.90, 102.0, 102.0, 82184.0, 3000.0),
        ('2026-08-14', 'Cash',           '招商银行', '现金',            'CASH',   'CNY', 1.0,  500.0, 1.0,   500.0, 0.0),
    ]
    cols = ['Date', 'Asset_Class', 'Platform', 'Name', 'Code',
            'Currency', 'Exchange_Rate', 'Shares', 'Current_Price', 'Total_Value', 'Net_Cash_Flow']
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)


def _make_transaction_csv(path: str):
    rows = [
        ('2026-08-14', 'US_Blend_Fund', '标普场外', '博时标普500 E类', '018738', '买入', 500.0, 5.0, 'CNY', 0.0),
    ]
    cols = ['Date', 'Asset_Class', 'Platform', 'Name', 'Code', 'Type', 'Amount_CNY', 'Price', 'Price_Currency', 'Fee_CNY']
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)


def _make_dca_json(path: str):
    data = {
        'config': {'sp_target_base': 2500, 'ndx_target_base': 2500,
                   'sp_other_weekly': 550, 'ndx_other_weekly': 1050,
                   'synthetic_split': {'dow': 0.5, 'jianxin_ndx': 0.5},
                   'jianxin_code': '539001', 'dow_code': '513400', 'topup_threshold': 0.0},
        'dow_prepaid': {
            'balance': 4000.0,
            'last_topup_date': '2026-08-10',
            'topup_amount': 5000.0,
            'consumption_log': [
                {'date': '2026-08-14', 'sp_multiplier': 0.5, 'consumed': 500.0, 'balance_after': 4000.0}
            ]
        }
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)


@pytest.fixture
def data_dir(tmp_path):
    _make_portfolio_csv(str(tmp_path / 'portfolio.csv'))
    _make_transaction_csv(str(tmp_path / 'transaction.csv'))
    _make_dca_json(str(tmp_path / 'dca_prepaid.json'))
    return str(tmp_path)


# ── 测试 ──────────────────────────────────────────────────

class TestGenerateWeeklyReport:

    def test_file_created(self, data_dir):
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        assert os.path.exists(path)
        assert path.endswith('2026-08-14.md')

    def test_report_has_overview(self, data_dir):
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '## 总览' in content
        assert '总资产' in content
        assert '单位净值' in content

    def test_report_has_transaction_section(self, data_dir):
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '## 本周交易' in content
        assert '博时标普500 E类' in content
        assert '018738' in content

    def test_traded_asset_shows_price_gain_and_buy_contrib(self, data_dir):
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '## 有交易标的变化' in content
        assert '价格涨跌' in content
        assert '加仓贡献' in content

    def test_sap_standalone_section(self, data_dir):
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '## 公司股票（SAP）' in content
        assert 'ESPP' in content or '归属' in content
        assert '汇率' in content

    def test_sap_fx_impact_shown_when_rate_changes(self, data_dir):
        """本周汇率 7.90 vs 上周 7.80，汇率影响应出现"""
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '汇率影响' in content

    def test_dca_section_shows_consumed(self, data_dir):
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '## 合成标普β状态' in content
        assert '本周抵扣' in content
        assert '未执行' not in content

    def test_dca_section_warns_when_not_consumed(self, data_dir):
        """consumption_log 为空时应显示未执行警告"""
        dca_path = os.path.join(data_dir, 'dca_prepaid.json')
        with open(dca_path, encoding='utf-8') as f:
            dca = json.load(f)
        dca['dow_prepaid']['consumption_log'] = []
        with open(dca_path, 'w', encoding='utf-8') as f:
            json.dump(dca, f)

        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '未执行' in content

    def test_allocation_section(self, data_dir):
        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '## 资产配置变化' in content
        assert '美股宽基' in content

    def test_new_position_shows_as_new(self, data_dir):
        """新建仓标的（上周市值=0）应显示'新建仓'而非价格涨跌数字"""
        # 在本周新增一行新标的
        port_path = os.path.join(data_dir, 'portfolio.csv')
        df = pd.read_csv(port_path)
        new_row = pd.DataFrame([{
            'Date': '2026-08-14', 'Asset_Class': 'US_Growth_Fund', 'Platform': '纳指场外',
            'Name': '建信纳指100 A类', 'Code': '539001', 'Currency': 'CNY', 'Exchange_Rate': 1.0,
            'Shares': 143.78, 'Current_Price': 3.48, 'Total_Value': 500.0, 'Net_Cash_Flow': 500.0,
        }])
        tx_path = os.path.join(data_dir, 'transaction.csv')
        tx = pd.read_csv(tx_path)
        new_tx = pd.DataFrame([{
            'Date': '2026-08-14', 'Asset_Class': 'US_Growth_Fund', 'Platform': '纳指场外',
            'Name': '建信纳指100 A类', 'Code': '539001', 'Type': '买入',
            'Amount_CNY': 500.0, 'Price': 3.48, 'Price_Currency': 'CNY', 'Fee_CNY': 0.0,
        }])
        pd.concat([df, new_row], ignore_index=True).to_csv(port_path, index=False)
        pd.concat([tx, new_tx], ignore_index=True).to_csv(tx_path, index=False)

        from weekly_report import generate_weekly_report
        path = generate_weekly_report(data_dir, '2026-08-14', '2026-08-07')
        content = open(path, encoding='utf-8').read()
        assert '新建仓' in content
