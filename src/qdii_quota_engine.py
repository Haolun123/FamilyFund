"""qdii_quota_engine.py — QDII场外额度 + 场内溢价决策引擎。

周内决策辅助工具，回答：这周场外额度够不够？不够时场内怎么补？

输出是操作建议，不是交易记录。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional


# ── 溢价阈值 ──────────────────────────────────────────────────
PREMIUM_THRESHOLD = {
    'NDX100': 0.11,   # 纳指100 场内ETF溢价阈值
    'SP500':  0.07,   # 标普500 场内ETF溢价阈值
    'DOW':    0.04,   # 道指     场内ETF溢价阈值
}

# 场内ETF代码
ETF_CODES = {
    'NDX100': '513390',   # 纳指100ETF博时
    'SP500':  '513650',   # 标普500ETF南方
    'DOW':    '513400',   # 道指ETF
}

# 场内最小交易金额（低于此金额手续费不划算）
MIN_ETF_TRADE = 5000.0


@dataclass
class IndexQuotaResult:
    """单个指数的额度分析结果。"""
    index: str                        # 'NDX100' / 'SP500'
    weekly_target: float              # 本周目标金额
    offmarket_capacity: float         # 场外可买上限
    shortfall: float                  # 缺口（0表示场外够）
    etf_premium: Optional[float]      # 场内ETF实时溢价（None=未能拉取）
    etf_code: str                     # 场内ETF代码
    etf_price: Optional[float]        # 场内ETF实时价格
    offmarket_nav: Optional[float]    # 场外基金最新净值（用于计算溢价）
    suggestion: str                   # 操作建议文字
    suggestion_amount: float          # 建议场内买入金额（0=不建议）
    below_min_trade: bool             # 缺口是否低于最小交易金额


@dataclass
class SyntheticSPResult:
    """合成标普建议。"""
    ndx_surplus: float           # 纳指场外超额量（可用于合成）
    sp_shortfall: float          # 标普缺口
    synthetic_coverage: float    # 合成可覆盖金额
    uncovered: float             # 仍无法覆盖的缺口
    dow_to_buy: float            # 建议追加买入道指金额
    prepaid_balance: float       # dca_prepaid 道指余额
    dow_premium: Optional[float] # 道指ETF实时溢价
    dow_viable: bool             # 道指溢价是否在阈值内
    suggestion: str


@dataclass
class QdiiQuotaReport:
    """完整的QDII额度决策报告。"""
    ndx: IndexQuotaResult
    sp: IndexQuotaResult
    synthetic: Optional[SyntheticSPResult]
    matrix_multiplier: float
    quota_last_updated: str      # 限额表最后更新日期
    errors: list[str] = field(default_factory=list)


# ── 加载配置 ──────────────────────────────────────────────────

def load_quota_config(data_dir: str) -> dict:
    """加载 qdii_quota.json。"""
    path = os.path.join(data_dir, 'config', 'qdii_quota.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_prepaid_config(data_dir: str) -> dict:
    """加载 dca_prepaid.json。"""
    path = os.path.join(data_dir, 'dca_prepaid.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_dca_config(data_dir: str) -> dict:
    """加载 dca_config.json。"""
    path = os.path.join(data_dir, 'config', 'dca_config.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ── 核心计算 ──────────────────────────────────────────────────

def _calc_offmarket_capacity(quota_config: dict, index: str) -> float:
    """计算场外可买上限 = Σ(日限额 × 5)，跳过限额='关闭'的基金。"""
    total = 0.0
    for fund in quota_config.get('funds', []):
        if fund.get('index') != index:
            continue
        limit = fund.get('limit_cny_per_day')
        if limit is None or str(limit).strip() == '关闭':
            continue
        try:
            total += float(limit) * 5
        except (ValueError, TypeError):
            continue
    return total


def _get_weekly_target(dca_config: dict, prepaid_config: dict,
                        index: str, multiplier: float) -> float:
    """计算本周目标金额 = base × 矩阵倍数。
    other_weekly 不计入目标，因为对应基金的限额已通过 qdii_quota.json 体现在场外上限里。
    """
    cfg = prepaid_config.get('config', {})

    if index == 'NDX100':
        base = cfg.get('ndx_target_base', 0)
    elif index == 'SP500':
        base = cfg.get('sp_target_base', 0)
    else:
        return 0.0

    return base * multiplier


def fetch_qdii_premiums() -> dict[str, Optional[float]]:
    """
    通过 akshare 集思录接口拉取 QDII ETF T-1 溢价率。
    返回 {etf_code: premium_rate}，premium_rate 为小数（如 0.07 = 7%）。
    拉取失败的返回 None。
    """
    result = {code: None for code in ETF_CODES.values()}
    try:
        import akshare as ak
        df = ak.qdii_e_index_jsl()
        # 集思录字段：代码、T-1溢价率（百分比字符串或浮点）
        code_col = next((c for c in df.columns if '代码' in c), None)
        prem_col = next((c for c in df.columns if '溢价' in c), None)
        if code_col is None or prem_col is None:
            return result
        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            if code in result:
                try:
                    val = row[prem_col]
                    # 可能是 "7.23%" 或 7.23 或 0.0723
                    if isinstance(val, str):
                        val = float(val.replace('%', '').strip())
                        # 集思录通常已经是百分比形式
                        result[code] = val / 100
                    else:
                        val = float(val)
                        # 若绝对值 > 1，说明是百分比形式
                        result[code] = val / 100 if abs(val) > 1 else val
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return result


def _get_offmarket_codes_by_index(quota_config: dict, index: str) -> list[str]:
    """获取某指数下所有有效场外基金代码（非关闭）。"""
    codes = []
    for fund in quota_config.get('funds', []):
        if fund.get('index') != index:
            continue
        limit = fund.get('limit_cny_per_day')
        if limit is not None and str(limit).strip() == '关闭':
            continue
        code = fund.get('code', '')
        if code:
            codes.append(code)
    return codes


def _analyze_index(index: str, quota_config: dict, dca_config: dict,
                   prepaid_config: dict, multiplier: float,
                   premiums: dict) -> IndexQuotaResult:
    """分析单个指数的额度和场内建议。premiums: {etf_code: premium_rate}"""
    weekly_target = _get_weekly_target(dca_config, prepaid_config, index, multiplier)
    capacity = _calc_offmarket_capacity(quota_config, index)
    shortfall = max(0.0, weekly_target - capacity)
    etf_code = ETF_CODES[index]
    threshold = PREMIUM_THRESHOLD[index]

    premium = premiums.get(etf_code)
    suggestion = ''
    suggestion_amount = 0.0
    below_min = False

    if shortfall > 0:
        if premium is None:
            suggestion = f'场外缺口 ¥{shortfall:,.0f}，溢价数据暂无（集思录未收录或拉取失败），请手动查看 {etf_code}'
        elif premium < threshold:
            if shortfall < MIN_ETF_TRADE:
                below_min = True
                suggestion = (
                    f'场外缺口 ¥{shortfall:,.0f}，溢价 {premium*100:.1f}% 低于阈值，'
                    f'但缺口低于最小交易额 ¥{MIN_ETF_TRADE:,.0f}，建议累计到下周'
                )
            else:
                suggestion_amount = shortfall
                suggestion = (
                    f'建议场内 {etf_code} 补入 ¥{shortfall:,.0f}'
                    f'（溢价 {premium*100:.1f}%，低于 {threshold*100:.0f}% 阈值）'
                )
        else:
            suggestion = (
                f'场外缺口 ¥{shortfall:,.0f}，但溢价 {premium*100:.1f}% '
                f'超过 {threshold*100:.0f}% 阈值，不建议场内买入'
            )
            if index == 'SP500':
                suggestion += '，见合成标普建议'
    else:
        suggestion = f'场外额度充足（上限 ¥{capacity:,.0f} ≥ 目标 ¥{weekly_target:,.0f}）'

    return IndexQuotaResult(
        index=index,
        weekly_target=weekly_target,
        offmarket_capacity=capacity,
        shortfall=shortfall,
        etf_premium=premium,
        etf_code=etf_code,
        etf_price=None,
        offmarket_nav=None,
        suggestion=suggestion,
        suggestion_amount=suggestion_amount,
        below_min_trade=below_min,
    )


def _analyze_synthetic_sp(sp_result: IndexQuotaResult,
                           ndx_result: IndexQuotaResult,
                           prepaid_config: dict,
                           premiums: dict) -> Optional[SyntheticSPResult]:
    """分析合成标普建议（仅在标普有缺口且溢价超阈值时触发）。"""
    sp_shortfall = sp_result.shortfall
    if sp_shortfall <= 0:
        return None
    if sp_result.etf_premium is not None and sp_result.etf_premium < PREMIUM_THRESHOLD['SP500']:
        return None  # 溢价够低，直接场内买，不需要合成

    # 纳指超额量 = 纳指场外可买上限 - 纳指本周目标
    ndx_surplus = max(0.0, ndx_result.offmarket_capacity - ndx_result.weekly_target)

    # 合成：纳指超额 N元 + 道指 N元 ≈ 标普 2N元
    synthetic_coverage = min(ndx_surplus * 2, sp_shortfall) if ndx_surplus > 0 else 0.0
    dow_to_buy = synthetic_coverage / 2

    uncovered = sp_shortfall - synthetic_coverage

    # 道指prepaid余额
    prepaid_balance = prepaid_config.get('dow_prepaid', {}).get('balance', 0.0)

    # 道指溢价（从已拉取的premiums里取）
    dow_etf_code = ETF_CODES['DOW']
    dow_premium = premiums.get(dow_etf_code)
    dow_viable = dow_premium is not None and dow_premium < PREMIUM_THRESHOLD['DOW']

    # 实际需要额外买的道指（扣除prepaid余额）
    dow_extra_needed = max(0.0, dow_to_buy - prepaid_balance)

    if ndx_surplus <= 0:
        suggestion = f'纳指无超额，标普缺口 ¥{sp_shortfall:,.0f} 本周无法通过合成覆盖'
    elif not dow_viable:
        dow_info = f'{dow_premium*100:.1f}%' if dow_premium is not None else '未知'
        suggestion = (
            f'纳指超额 ¥{ndx_surplus:,.0f}，理论可合成 ¥{synthetic_coverage:,.0f}，'
            f'但道指ETF溢价 {dow_info} 超过 {PREMIUM_THRESHOLD["DOW"]*100:.0f}% 阈值，不建议追加道指'
        )
    else:
        parts = [f'纳指超额 ¥{ndx_surplus:,.0f}，可合成覆盖标普缺口 ¥{synthetic_coverage:,.0f}']
        if prepaid_balance > 0:
            parts.append(f'prepaid余额 ¥{prepaid_balance:,.0f} 可抵扣')
        if dow_extra_needed > 0:
            parts.append(f'建议额外买入道指ETF 513400 ¥{dow_extra_needed:,.0f}')
        if uncovered > 0:
            parts.append(f'仍有 ¥{uncovered:,.0f} 无法覆盖，下周优先补入')
        suggestion = '；'.join(parts)

    return SyntheticSPResult(
        ndx_surplus=ndx_surplus,
        sp_shortfall=sp_shortfall,
        synthetic_coverage=synthetic_coverage,
        uncovered=uncovered,
        dow_to_buy=dow_to_buy,
        prepaid_balance=prepaid_balance,
        dow_premium=dow_premium,
        dow_viable=dow_viable,
        suggestion=suggestion,
    )


# ── 对外接口 ──────────────────────────────────────────────────

def compute_qdii_quota_report(data_dir: str,
                              ndx_multiplier: float,
                              sp_multiplier: float) -> QdiiQuotaReport:
    """
    计算本周QDII额度决策报告。

    Args:
        data_dir: FAMILYFUND_DATA 目录
        ndx_multiplier: 纳指本周矩阵倍数
        sp_multiplier:  标普本周矩阵倍数

    Returns:
        QdiiQuotaReport
    """
    errors = []

    try:
        quota_config = load_quota_config(data_dir)
    except Exception as e:
        errors.append(f'qdii_quota.json 加载失败：{e}')
        quota_config = {'funds': [], '_last_updated': ''}

    try:
        prepaid_config = load_prepaid_config(data_dir)
    except Exception as e:
        errors.append(f'dca_prepaid.json 加载失败：{e}')
        prepaid_config = {'config': {}, 'dow_prepaid': {'balance': 0.0}}

    try:
        dca_config = load_dca_config(data_dir)
    except Exception as e:
        errors.append(f'dca_config.json 加载失败：{e}')
        dca_config = {'plans': []}

    quota_last_updated = quota_config.get('_last_updated', '未知')

    # 统一拉取一次溢价（避免多次调用akshare）
    try:
        premiums = fetch_qdii_premiums()
    except Exception as e:
        errors.append(f'溢价数据拉取失败：{e}')
        premiums = {code: None for code in ETF_CODES.values()}

    ndx_result = _analyze_index('NDX100', quota_config, dca_config, prepaid_config, ndx_multiplier, premiums)
    sp_result = _analyze_index('SP500', quota_config, dca_config, prepaid_config, sp_multiplier, premiums)
    synthetic = _analyze_synthetic_sp(sp_result, ndx_result, prepaid_config, premiums)

    return QdiiQuotaReport(
        ndx=ndx_result,
        sp=sp_result,
        synthetic=synthetic,
        matrix_multiplier=ndx_multiplier,  # 主显示用纳指倍数
        quota_last_updated=quota_last_updated,
        errors=errors,
    )
