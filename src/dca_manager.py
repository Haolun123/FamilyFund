"""dca_manager.py — 定投计划管理模块。

配置数据：$FAMILYFUND_DATA/dca_config.json
  - plans: 定投计划列表，每项含 name/code/asset_class/platform/base_amount_cny 等字段
  - 黄金特殊字段：unit='gram', base_amount_unit, min_unit

温度计倍数：复用 market_monitor 中已有的 lookup_*_multiplier() 函数，
按 asset_class 路由到对应信号。
"""

import json
import os
import uuid


_DEFAULT_CONFIG = {
    "plans": []
}

_ASSET_CLASSES = [
    'US_Blend_Fund',
    'US_Growth_Fund',
    'CN_Index_Fund',
    'ETF_Stock',
    'Gold',
    'Fixed_Income',
    'Company_Stock',
]

_ASSET_CLASS_LABELS = {
    'US_Blend_Fund':  '美股宽基（标普500）',
    'US_Growth_Fund': '美股成长（纳指100）',
    'CN_Index_Fund':  'A股指数（沪深300/A500）',
    'ETF_Stock':      'ETF/个股',
    'Gold':           '黄金',
    'Fixed_Income':   '固定收益',
    'Company_Stock':  '公司股票',
}

_FREQUENCIES = ['weekly', 'biweekly', 'monthly']
_FREQ_LABELS  = {'weekly': '每周', 'biweekly': '每两周', 'monthly': '每月'}


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, 'dca_config.json')


def load_dca_config(data_dir: str) -> dict:
    """加载 dca_config.json。文件不存在时返回空配置。"""
    p = _path(data_dir)
    if not os.path.exists(p):
        return dict(_DEFAULT_CONFIG)
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save_dca_config(data_dir: str, config: dict):
    """写入 dca_config.json（原子写入）。"""
    p = _path(data_dir)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def add_plan(data_dir: str, plan: dict) -> str:
    """新增定投计划，自动生成 id。返回新 id。"""
    config = load_dca_config(data_dir)
    plan_id = 'plan_' + uuid.uuid4().hex[:8]
    plan['id'] = plan_id
    config.setdefault('plans', []).append(plan)
    save_dca_config(data_dir, config)
    return plan_id


def update_plan(data_dir: str, plan_id: str, updates: dict):
    """更新指定 id 的计划字段。"""
    config = load_dca_config(data_dir)
    for plan in config.get('plans', []):
        if plan['id'] == plan_id:
            plan.update(updates)
            break
    save_dca_config(data_dir, config)


def remove_plan(data_dir: str, plan_id: str):
    """删除指定 id 的计划。"""
    config = load_dca_config(data_dir)
    config['plans'] = [p for p in config.get('plans', []) if p['id'] != plan_id]
    save_dca_config(data_dir, config)


def _parse_multiplier_str(mult_str: str) -> float:
    """将倍数字符串（'1.5x', '暂停', '顶格' 等）转为 float。
    暂停 → 0.0；顶格 → 3.0；无法解析 → 1.0。
    """
    if not mult_str or mult_str == '—':
        return 1.0
    if mult_str == '暂停':
        return 0.0
    if mult_str == '顶格':
        return 3.0
    try:
        return float(mult_str.rstrip('x'))
    except ValueError:
        return 1.0


def compute_suggestion(plan: dict, market_data: dict) -> dict:
    """计算单个定投计划的本周建议。

    Args:
        plan:        dca_config.json 中的单个计划 dict
        market_data: market_monitor.get_market_data() 的返回值

    Returns:
        {
            'multiplier_str': '1.5x',   # 原始倍数字符串
            'multiplier':     1.5,       # float
            'arrow':          '↑',       # 箭头信号
            'suggested_cny':  1200,      # 建议人民币金额（取整到10元）
            'suggested_unit': 2,         # 黄金：建议克数（unit=gram 时有效）
            'unit':           'cny',     # 'cny' 或 'gram'
            'gold_price_cny': None,      # 黄金单价（仅 unit=gram 时填充）
        }
    """
    from market_monitor import (
        lookup_multiplier,
        lookup_a_share_multiplier,
        lookup_gold_multiplier,
        compute_bias,
    )

    # ── 固定定投模式：跳过矩阵信号，强制 1.0x ──
    if plan.get('mode', 'matrix') == 'fixed':
        mult_str = '1.0x'
        multiplier = 1.0
        arrow = '→'
        base_cny = plan.get('base_amount_cny', 0)
        unit = plan.get('unit', 'cny')
        if unit == 'gram':
            base_unit = plan.get('base_amount_unit', 1)
            min_unit  = plan.get('min_unit', 1)
            gold_entry = market_data.get('gold') or {}
            gold_price = gold_entry.get('price')
            suggested_unit = base_unit
            suggested_cny  = round(suggested_unit * gold_price / 10) * 10 if gold_price else None
            return {
                'multiplier_str': '1.0x', 'multiplier': 1.0, 'arrow': '→',
                'suggested_cny': suggested_cny, 'suggested_unit': suggested_unit,
                'unit': 'gram', 'gold_price_cny': gold_price,
            }
        suggested_cny = round(base_cny * 1.0 / 10) * 10
        return {
            'multiplier_str': '1.0x', 'multiplier': 1.0, 'arrow': '→',
            'suggested_cny': suggested_cny, 'suggested_unit': None,
            'unit': 'cny', 'gold_price_cny': None,
        }

    asset_class = plan.get('asset_class', '')
    vix   = (market_data.get('vix')  or {}).get('price')
    vxn   = (market_data.get('vxn')  or {}).get('price')
    qvix  = (market_data.get('qvix') or {}).get('price')

    # ── 按 asset_class 路由 ──
    if asset_class == 'US_Blend_Fund':
        pe = (market_data.get('pe_sp500') or {}).get('value') or \
             (market_data.get('pe_sp500') or {}).get('manual_override')
        mult_str = lookup_multiplier(pe, vix, 'sp500')

    elif asset_class == 'US_Growth_Fund':
        pe = (market_data.get('pe_ndx100') or {}).get('value') or \
             (market_data.get('pe_ndx100') or {}).get('manual_override')
        mult_str = lookup_multiplier(pe, vxn, 'ndx100')

    elif asset_class == 'CN_Index_Fund':
        pe_csi300 = (market_data.get('pe_csi300') or {}).get('value')
        mult_str = lookup_a_share_multiplier(pe_csi300, qvix, 'csi300')

    elif asset_class == 'ETF_Stock':
        pe_csi300 = (market_data.get('pe_csi300') or {}).get('value')
        mult_str = lookup_a_share_multiplier(pe_csi300, qvix, 'csi300')

    elif asset_class == 'Gold':
        gold_entry = market_data.get('gold')
        bias200 = compute_bias(gold_entry).get('bias200') if gold_entry else None
        mult_str = lookup_gold_multiplier(bias200, vix)

    else:
        # Fixed_Income / Company_Stock → 始终 1.0x
        mult_str = '1.0x'

    multiplier = _parse_multiplier_str(mult_str)

    # ── 箭头 ──
    if multiplier == 0.0:
        arrow = '⏸'
    elif multiplier >= 2.0:
        arrow = '↑↑'
    elif multiplier >= 1.2:
        arrow = '↑'
    elif multiplier >= 0.8:
        arrow = '→'
    else:
        arrow = '↓'

    base_cny = plan.get('base_amount_cny', 0)
    unit = plan.get('unit', 'cny')

    if unit == 'gram':
        # 黄金：纯克数计算，不依赖 base_amount_cny
        base_unit = plan.get('base_amount_unit', 1)
        min_unit  = plan.get('min_unit', 1)
        raw = base_unit * multiplier
        if raw < min_unit:
            suggested_units = 0
        else:
            suggested_units = max(min_unit, round(raw / min_unit) * min_unit)
        # 参考人民币金额（展示用，非决策依据）
        gold_price_cny = _estimate_gold_price_cny(market_data)
        suggested_cny = round(gold_price_cny * suggested_units, -1) if (gold_price_cny and suggested_units > 0) else None
        return {
            'multiplier_str': mult_str,
            'multiplier':     multiplier,
            'arrow':          arrow,
            'suggested_cny':  suggested_cny,
            'suggested_unit': suggested_units,
            'unit':           'gram',
            'gold_price_cny': gold_price_cny,
        }
    else:
        suggested_cny = round(base_cny * multiplier / 10) * 10
        return {
            'multiplier_str': mult_str,
            'multiplier':     multiplier,
            'arrow':          arrow,
            'suggested_cny':  suggested_cny,
            'suggested_unit': None,
            'unit':           'cny',
            'gold_price_cny': None,
        }


def _estimate_gold_price_cny(market_data: dict) -> float | None:
    """从市场数据估算黄金每克人民币价格。
    gold price 单位是 USD/troy oz（1 troy oz ≈ 31.1035 g）。
    汇率从 fx_service 获取，失败返回 None。
    """
    gold_entry = market_data.get('gold')
    if not gold_entry:
        return None
    price_usd_oz = gold_entry.get('price')
    if not price_usd_oz:
        return None
    try:
        from fx_service import get_rate
        usd_cny = get_rate('USD', 'CNY')
        if usd_cny:
            return round(price_usd_oz * usd_cny / 31.1035, 2)
    except Exception:
        pass
    return None


def compute_all_suggestions(plans: list, market_data: dict) -> list:
    """批量计算所有启用计划的建议。返回 list of (plan, suggestion) tuples。"""
    results = []
    for plan in plans:
        if not plan.get('enabled', True):
            continue
        suggestion = compute_suggestion(plan, market_data)
        results.append((plan, suggestion))
    return results
