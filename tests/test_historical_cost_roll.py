"""test_historical_cost_roll.py — historical_cost roll 逻辑集成测试

模拟真实 weekly update 场景，验证：
1. roll 只更新均价和 current_shares，不碰其他字段
2. 买入后加权均价正确上升
3. 卖出后均价不变（均价法）
4. 港股/EUR 换汇标的均价计算正确
5. 不在 historical_cost.json 里的标的不会被新增
6. 已有正确初始值的标的，若本周无交易，均价不变
"""
import os
import sys
import json
import math
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

BASELINE = '2026-04-10'


def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _portfolio(rows):
    """构造 portfolio DataFrame，自动补齐 Date 格式。"""
    cols = ['Date', 'Asset_Class', 'Platform', 'Name', 'Code',
            'Currency', 'Exchange_Rate', 'Shares', 'Current_Price',
            'Total_Value', 'Net_Cash_Flow']
    df = pd.DataFrame(rows, columns=cols)
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
    return df


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def setup(tmp_path):
    """
    初始状态（用户手动填写的正确均价）：
      - 021000 南方纳指：均价 2.12 CNY，建仓日 10000 份
      - HK0700 腾讯：均价 491.241 HKD，建仓日 100 股，汇率 0.857
      - 018738 博时标普：均价 5.23 CNY，建仓日 8000 份
    """
    hist = {
        "_note": "test",
        "021000": {"avg_cost": 2.12,    "currency": "CNY", "name": "南方纳指"},
        "HK0700": {"avg_cost": 491.241, "currency": "HKD", "name": "腾讯控股"},
        "018738": {"avg_cost": 5.23,    "currency": "CNY", "name": "博时标普"},
    }
    _write_json(tmp_path / 'historical_cost.json', hist)

    # 建仓日快照（BASELINE）
    portfolio_rows = [
        (BASELINE, 'US_Growth_Fund', 'p', '南方纳指', '021000',
         'CNY', 1.0, 10000.0, 2.12, 21200.0, 21200.0),
        (BASELINE, 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
         'HKD', 0.857, 100.0, 491.241, 42099.4, 42099.4),
        (BASELINE, 'US_Blend_Fund', 'p', '博时标普', '018738',
         'CNY', 1.0, 8000.0, 5.23, 41840.0, 41840.0),
    ]
    return tmp_path, portfolio_rows


# ── Test 1：无交易，均价不变 ──────────────────────────────────────────────────

class TestNoTrade:

    def test_avg_cost_unchanged_when_no_trade(self, setup):
        """本周无任何交易，均价应保持初始值不变"""
        tmp_path, baseline_rows = setup

        # 本周快照：价格变了，但份额和 NCF 没变
        this_week = baseline_rows.copy()
        this_week[0] = ('2026-08-21',) + baseline_rows[0][1:8] + (2.30, 23000.0, 0.0)
        this_week[1] = ('2026-08-21',) + baseline_rows[1][1:8] + (460.0,  39393.2, 0.0)
        this_week[2] = ('2026-08-21',) + baseline_rows[2][1:8] + (5.50,  44000.0, 0.0)

        df = _portfolio(baseline_rows + this_week)
        df.to_csv(tmp_path / 'portfolio.csv', index=False)

        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        portfolio_df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), portfolio_df)

        assert abs(result['021000']['avg_cost'] - 2.12) < 0.001, "CNY 标的无交易均价应不变"
        assert abs(result['HK0700']['avg_cost'] - 491.241) < 0.001, "HKD 标的无交易均价应不变"
        assert abs(result['018738']['avg_cost'] - 5.23) < 0.001, "博时无交易均价应不变"


# ── Test 2：买入 CNY 标的，均价正确滚动 ──────────────────────────────────────

class TestBuyCNY:

    def test_buy_raises_avg_cost(self, setup):
        """
        本周买入南方纳指 1000 份，成交价 2.40，花费 2400 CNY（NCF=2400）
        新均价 = (2.12 × 10000 + 2400) / 11000 = 23600 / 11000 ≈ 2.1455
        """
        tmp_path, baseline_rows = setup

        this_week = [
            ('2026-08-21', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 11000.0, 2.40, 26400.0, 2400.0),  # NCF=2400
            ('2026-08-21', 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
             'HKD', 0.857, 100.0, 460.0, 39422.0, 0.0),
            ('2026-08-21', 'US_Blend_Fund', 'p', '博时标普', '018738',
             'CNY', 1.0, 8000.0, 5.50, 44000.0, 0.0),
        ]

        df = _portfolio(baseline_rows + this_week)
        df.to_csv(tmp_path / 'portfolio.csv', index=False)

        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        portfolio_df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), portfolio_df)

        expected = (2.12 * 10000 + 2400) / 11000
        assert abs(result['021000']['avg_cost'] - expected) < 0.001, \
            f"买入后均价应为 {expected:.4f}，实际 {result['021000']['avg_cost']}"
        assert result['021000']['current_shares'] == pytest.approx(11000.0)

    def test_buy_at_lower_price_lowers_avg_cost(self, setup):
        """
        低价买入，均价应下降
        买入 2000 份 @ 1.80（低于初始均价 2.12）
        新均价 = (2.12 × 10000 + 1.80 × 2000) / 12000 = 24800 / 12000 ≈ 2.0667
        """
        tmp_path, baseline_rows = setup

        this_week = [
            ('2026-08-21', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 12000.0, 1.80, 21600.0, 3600.0),  # NCF=3600
            ('2026-08-21', 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
             'HKD', 0.857, 100.0, 460.0, 39422.0, 0.0),
            ('2026-08-21', 'US_Blend_Fund', 'p', '博时标普', '018738',
             'CNY', 1.0, 8000.0, 5.50, 44000.0, 0.0),
        ]

        df = _portfolio(baseline_rows + this_week)
        df.to_csv(tmp_path / 'portfolio.csv', index=False)

        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        portfolio_df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), portfolio_df)

        expected = (2.12 * 10000 + 3600) / 12000
        assert result['021000']['avg_cost'] < 2.12, "低价买入后均价应下降"
        assert abs(result['021000']['avg_cost'] - expected) < 0.001


# ── Test 3：卖出，均价不变（均价法）──────────────────────────────────────────

class TestSell:

    def test_sell_does_not_change_avg_cost(self, setup):
        """
        卖出 2000 份南方纳指（NCF 为负，不参与均价计算）
        均价应保持 2.12 不变，份额变为 8000
        """
        tmp_path, baseline_rows = setup

        this_week = [
            ('2026-08-21', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 8000.0, 2.30, 18400.0, -4600.0),  # NCF 负值=卖出
            ('2026-08-21', 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
             'HKD', 0.857, 100.0, 460.0, 39422.0, 0.0),
            ('2026-08-21', 'US_Blend_Fund', 'p', '博时标普', '018738',
             'CNY', 1.0, 8000.0, 5.50, 44000.0, 0.0),
        ]

        df = _portfolio(baseline_rows + this_week)
        df.to_csv(tmp_path / 'portfolio.csv', index=False)

        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        portfolio_df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), portfolio_df)

        assert abs(result['021000']['avg_cost'] - 2.12) < 0.001, "卖出后均价应不变"
        assert result['021000']['current_shares'] == pytest.approx(8000.0)


# ── Test 4：HKD 标的买入，均价和换汇均正确 ───────────────────────────────────

class TestBuyHKD:

    def test_hkd_buy_avg_cost_and_cny_cache(self, setup):
        """
        腾讯买入 100 股，NCF=39138.66 CNY，当时汇率 0.867
        折合港元 ≈ 39138.66 / 0.867 ≈ 45142 HKD，均价 ≈ 451.4 HKD
        新 HKD 均价 = (491.241 × 100 + 45142) / 200 ≈ 470.3 HKD
        _avg_cost_cny = 470.3 × 新汇率（0.857）≈ 403.0
        """
        tmp_path, baseline_rows = setup

        this_week = [
            ('2026-08-21', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.30, 23000.0, 0.0),
            ('2026-08-21', 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
             'HKD', 0.857, 200.0, 460.0, 78844.0, 39138.66),  # 买入100股
            ('2026-08-21', 'US_Blend_Fund', 'p', '博时标普', '018738',
             'CNY', 1.0, 8000.0, 5.50, 44000.0, 0.0),
        ]

        df = _portfolio(baseline_rows + this_week)
        df.to_csv(tmp_path / 'portfolio.csv', index=False)

        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        portfolio_df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), portfolio_df)

        entry = result['HK0700']
        # 均价应在 491 和 452 之间（两笔的加权）
        assert 451 < entry['avg_cost'] < 492, \
            f"HKD 均价应在合理区间，实际 {entry['avg_cost']}"
        assert entry['current_shares'] == pytest.approx(200.0)
        # _avg_cost_cny 应存在且合理
        assert '_avg_cost_cny' in entry
        assert 380 < entry['_avg_cost_cny'] < 430, \
            f"_avg_cost_cny 应在合理区间，实际 {entry['_avg_cost_cny']}"


# ── Test 5：不在 json 里的标的不被新增 ───────────────────────────────────────

class TestNoNewEntry:

    def test_new_holding_not_added_to_json(self, setup):
        """
        本周新建仓一个标的（不在 historical_cost.json 里），
        roll 后 json 里不应出现这个新标的
        """
        tmp_path, baseline_rows = setup

        this_week = [
            ('2026-08-21', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 10000.0, 2.30, 23000.0, 0.0),
            ('2026-08-21', 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
             'HKD', 0.857, 100.0, 460.0, 39422.0, 0.0),
            ('2026-08-21', 'US_Blend_Fund', 'p', '博时标普', '018738',
             'CNY', 1.0, 8000.0, 5.50, 44000.0, 0.0),
            ('2026-08-21', 'Individual_Stock', 'p', '新标的', '999999',
             'CNY', 1.0, 500.0, 10.0, 5000.0, 5000.0),  # 不在 json 里
        ]

        df = _portfolio(baseline_rows + this_week)
        df.to_csv(tmp_path / 'portfolio.csv', index=False)

        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        portfolio_df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), portfolio_df)

        assert '999999' not in result, "不在 json 里的标的不应被自动新增"


# ── Test 6：连续两周滚动，验证累积正确 ───────────────────────────────────────

class TestMultiWeekRoll:

    def test_two_week_roll(self, setup):
        """
        第一周：买入 021000 1000 份 @ 2.40，NCF=2400
        第二周：再买入 021000 500 份 @ 2.50，NCF=1250
        最终均价应正确反映三笔买入的加权平均
        """
        tmp_path, baseline_rows = setup

        week1 = [
            ('2026-08-21', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 11000.0, 2.40, 26400.0, 2400.0),
            ('2026-08-21', 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
             'HKD', 0.857, 100.0, 460.0, 39422.0, 0.0),
            ('2026-08-21', 'US_Blend_Fund', 'p', '博时标普', '018738',
             'CNY', 1.0, 8000.0, 5.50, 44000.0, 0.0),
        ]
        week2 = [
            ('2026-08-28', 'US_Growth_Fund', 'p', '南方纳指', '021000',
             'CNY', 1.0, 11500.0, 2.50, 28750.0, 1250.0),
            ('2026-08-28', 'Individual_Stock', 'p', '腾讯控股', 'HK0700',
             'HKD', 0.857, 100.0, 465.0, 39850.5, 0.0),
            ('2026-08-28', 'US_Blend_Fund', 'p', '博时标普', '018738',
             'CNY', 1.0, 8000.0, 5.60, 44800.0, 0.0),
        ]

        df = _portfolio(baseline_rows + week1 + week2)
        df.to_csv(tmp_path / 'portfolio.csv', index=False)

        from historical_cost import roll_historical_cost
        from nav_engine import load_portfolio
        portfolio_df = load_portfolio(str(tmp_path / 'portfolio.csv'))
        result = roll_historical_cost(str(tmp_path), portfolio_df)

        # 预期：(2.12×10000 + 2400 + 1250) / 11500 = 24850 / 11500 ≈ 2.1609
        expected = (2.12 * 10000 + 2400 + 1250) / 11500
        assert abs(result['021000']['avg_cost'] - expected) < 0.001, \
            f"两周累积后均价应为 {expected:.4f}，实际 {result['021000']['avg_cost']}"
        assert result['021000']['current_shares'] == pytest.approx(11500.0)
