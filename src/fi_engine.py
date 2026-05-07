"""fi_engine.py — 财务独立测算 + 储蓄率追踪。

配置文件：$FAMILYFUND_DATA/fi_config.json
"""

import json
import math
import os
from datetime import date


_DEFAULT_CONFIG = {
    "monthly_income_cny":        30000,
    "monthly_savings_target_pct": 0.40,
    "annual_expense_target_cny": 200000,
    "withdrawal_rate":           0.04,
    "expected_annual_return":    0.06,
}


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, 'fi_config.json')


def load_fi_config(data_dir: str) -> dict:
    p = _path(data_dir)
    if not os.path.exists(p):
        return dict(_DEFAULT_CONFIG)
    with open(p, encoding='utf-8') as f:
        cfg = json.load(f)
    # 补全缺失字段
    for k, v in _DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_fi_config(data_dir: str, config: dict):
    p = _path(data_dir)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ══════════════════════════════════════════════════════
# 财务独立测算
# ══════════════════════════════════════════════════════

def compute_fi_target(annual_expense: float, withdrawal_rate: float) -> float:
    """FI 目标资产 = 年支出 / 提款率（25x 法则）。"""
    if withdrawal_rate <= 0:
        return float('inf')
    return annual_expense / withdrawal_rate


def compute_years_to_fi(
    current_assets: float,
    fi_target: float,
    monthly_savings: float,
    annual_return: float,
) -> float | None:
    """反推达到 FI 目标所需年数。

    使用复利公式：FV = PV*(1+r)^n + PMT*((1+r)^n - 1)/r
    反解 n（二分法，最多搜索 100 年）。

    Returns:
        年数（float），或 None（已达标返回 0，无法在100年内达标返回 None）
    """
    if current_assets >= fi_target:
        return 0.0

    r_monthly = (1 + annual_return) ** (1 / 12) - 1
    pmt = monthly_savings

    def fv(n_years: float) -> float:
        n = n_years * 12  # 月数
        if annual_return == 0:
            return current_assets + pmt * n
        return current_assets * (1 + r_monthly) ** n + pmt * ((1 + r_monthly) ** n - 1) / r_monthly

    if fv(100) < fi_target:
        return None  # 100年内无法达标

    # 二分法
    lo, hi = 0.0, 100.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if fv(mid) < fi_target:
            lo = mid
        else:
            hi = mid
    return round(hi, 1)


def fi_sensitivity(
    current_assets: float,
    fi_target: float,
    monthly_savings: float,
    annual_return: float,
) -> list[dict]:
    """敏感性分析：收益率 ±1%、月储蓄 ±20%。

    Returns:
        list of {'label', 'annual_return', 'monthly_savings', 'years', 'target_year'}
    """
    scenarios = [
        ('收益率-1%',  annual_return - 0.01, monthly_savings),
        ('基准',       annual_return,        monthly_savings),
        ('收益率+1%',  annual_return + 0.01, monthly_savings),
        ('储蓄-20%',  annual_return,        monthly_savings * 0.8),
        ('储蓄+20%',  annual_return,        monthly_savings * 1.2),
    ]
    results = []
    current_year = date.today().year
    for label, ret, sav in scenarios:
        y = compute_years_to_fi(current_assets, fi_target, sav, ret)
        target_year = (current_year + math.ceil(y)) if y is not None and y > 0 else (
            current_year if y == 0 else None
        )
        results.append({
            'label':          label,
            'annual_return':  ret,
            'monthly_savings': sav,
            'years':          y,
            'target_year':    target_year,
        })
    return results


# ══════════════════════════════════════════════════════
# 储蓄率追踪
# ══════════════════════════════════════════════════════

def compute_monthly_savings(raw_df) -> dict:
    """从 portfolio.csv 提取每月 Cash 行的正 NCF（外部入金）。

    ESPP/RSU 归属的 NCF 不计入（Company_Stock 行），只统计 Cash 行外部入金。

    Returns:
        {'YYYY-MM': float, ...}  正数=当月入金
    """
    import pandas as pd

    cash_df = raw_df[
        (raw_df['Asset_Class'] == 'Cash') &
        (raw_df['Net_Cash_Flow'] > 0)
    ].copy()

    if cash_df.empty:
        return {}

    cash_df['month'] = pd.to_datetime(cash_df['Date']).dt.strftime('%Y-%m')
    monthly = cash_df.groupby('month')['Net_Cash_Flow'].sum()
    return {k: round(float(v), 2) for k, v in monthly.items()}


def compute_savings_rate(monthly_savings: dict, monthly_income: float) -> dict:
    """计算每月储蓄率。

    Returns:
        {'YYYY-MM': float, ...}  0-1 之间的比率
    """
    if monthly_income <= 0:
        return {}
    return {
        month: round(amount / monthly_income, 4)
        for month, amount in monthly_savings.items()
    }
