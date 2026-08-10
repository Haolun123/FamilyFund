"""synthetic_sp.py — 合成标普β定投工具（道指预付池 + 动态矩阵拆腿）。

背景：QDII 额度受限 + 场内标普/纳指 ETF 溢价 8-12%。用「场外建信纳指100 +
场内道指ETF」按可配置比例拼一个「类标普β」，绕开溢价。

核心逻辑：
- 标普敞口目标 = sp_target_base × 标普矩阵倍数；扣掉已有场外额度 = 标普缺口
- 纳指敞口目标 = ndx_target_base × 纳指矩阵倍数；扣掉已有场外额度 = 纳指缺口
- 标普缺口用「类标普β」补，按 道指:建信纳指 拆腿（默认 50:50，可配置）
- 纳指缺口用建信纳指补
- 建信纳指本周买入 = 纳指缺口 + 类标普β的纳指腿
- 道指走预付池：一次性买入避开最低手续费墙，每周按矩阵配额虚拟消耗

守恒律：
    sp_other + jianxin_sp_leg + dow_weekly == sp_target
    ndx_other + jianxin_ndx_leg          == ndx_target
（缺口 ≤ 0 时对应腿归零）

状态文件：$FAMILYFUND_DATA/dca_prepaid.json
"""

import json
import os


_DEFAULT_CONFIG = {
    'config': {
        'sp_target_base':  2500,
        'ndx_target_base': 2500,
        'sp_other_weekly':  550,
        'ndx_other_weekly': 1050,
        'synthetic_split': {'dow': 0.5, 'jianxin_ndx': 0.5},
        'jianxin_code': '',
        'dow_code': '',
        'topup_threshold': 0.0,   # 余额 ≤ 此值时提醒补仓
    },
    'dow_prepaid': {
        'balance': 0.0,
        'last_topup_date': None,
        'topup_amount': 0.0,
        'consumption_log': [],
    },
}


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, 'dca_prepaid.json')


def load_prepaid(data_dir: str) -> dict:
    """加载 dca_prepaid.json。文件不存在时返回默认配置（深拷贝）。"""
    p = _path(data_dir)
    if not os.path.exists(p):
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    with open(p, encoding='utf-8') as f:
        data = json.load(f)
    # 补齐缺失字段（向前兼容）
    base = json.loads(json.dumps(_DEFAULT_CONFIG))
    base['config'].update(data.get('config', {}))
    base['dow_prepaid'].update(data.get('dow_prepaid', {}))
    return base


def save_prepaid(data_dir: str, data: dict):
    """写入 dca_prepaid.json（原子写入）。"""
    p = _path(data_dir)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _matrix_multiplier(market_data: dict, target: str) -> float:
    """取标普或纳指的当前矩阵倍数。target: 'sp500' | 'ndx100'。"""
    from market_monitor import lookup_multiplier

    def _num(x):
        return _parse_multiplier_str(x)

    if target == 'sp500':
        pe = (market_data.get('pe_sp500') or {}).get('value') or \
             (market_data.get('pe_sp500') or {}).get('manual_override')
        vol = (market_data.get('vix') or {}).get('price')
        return _num(lookup_multiplier(pe, vol, 'sp500'))
    else:
        pe = (market_data.get('pe_ndx100') or {}).get('value') or \
             (market_data.get('pe_ndx100') or {}).get('manual_override')
        vol = (market_data.get('vxn') or {}).get('price')
        return _num(lookup_multiplier(pe, vol, 'ndx100'))


def _parse_multiplier_str(mult_str) -> float:
    """'0.8x' → 0.8；'—'/None → 0.0。"""
    if mult_str is None:
        return 0.0
    if isinstance(mult_str, (int, float)):
        return float(mult_str)
    s = str(mult_str).strip().lower().rstrip('x')
    try:
        return float(s)
    except ValueError:
        return 0.0


def compute_synthetic_dca(market_data: dict, config: dict,
                          sp_multiplier: float = None,
                          ndx_multiplier: float = None) -> dict:
    """核心拆腿计算。

    Args:
        market_data:    market_monitor.get_market_data() 返回值（用于取矩阵倍数）
        config:         dca_prepaid.json 的 'config' 段
        sp_multiplier:  可选，直接指定标普倍数（跳过 market_data 查表，便于测试）
        ndx_multiplier: 可选，直接指定纳指倍数

    Returns:
        dict，见模块文档的守恒律。所有金额取整到 10 元（与 dca_manager 一致）。
    """
    sp_mult  = sp_multiplier  if sp_multiplier  is not None else _matrix_multiplier(market_data, 'sp500')
    ndx_mult = ndx_multiplier if ndx_multiplier is not None else _matrix_multiplier(market_data, 'ndx100')

    sp_base   = config.get('sp_target_base', 2500)
    ndx_base  = config.get('ndx_target_base', 2500)
    sp_other  = config.get('sp_other_weekly', 550)
    ndx_other = config.get('ndx_other_weekly', 1050)
    split     = config.get('synthetic_split', {'dow': 0.5, 'jianxin_ndx': 0.5})
    dow_ratio = split.get('dow', 0.5)

    def _round10(x):
        return round(x / 10) * 10

    # 目标与缺口（缺口不为负）
    sp_target  = _round10(sp_base * sp_mult)
    ndx_target = _round10(ndx_base * ndx_mult)
    sp_gap  = max(0, sp_target - sp_other)
    ndx_gap = max(0, ndx_target - ndx_other)

    # 标普缺口拆腿。为保证守恒（两腿之和 == 缺口），道指腿取整后，
    # 建信标普腿用缺口减道指腿反推，避免两腿独立取整丢失尾数。
    dow_sp_leg      = _round10(sp_gap * dow_ratio)
    jianxin_sp_leg  = sp_gap - dow_sp_leg
    # 纳指缺口全给建信
    jianxin_ndx_leg = ndx_gap

    # 建信本周总买入 = 纳指腿 + 类标普纳指腿
    jianxin_weekly = jianxin_ndx_leg + jianxin_sp_leg
    dow_weekly     = dow_sp_leg

    return {
        'sp_multiplier':  sp_mult,
        'ndx_multiplier': ndx_mult,
        'sp_target':      sp_target,
        'ndx_target':     ndx_target,
        'sp_gap':         sp_gap,
        'ndx_gap':        ndx_gap,
        'jianxin_weekly': jianxin_weekly,
        'dow_weekly':     dow_weekly,
        'jianxin_ndx_leg': jianxin_ndx_leg,
        'jianxin_sp_leg':  jianxin_sp_leg,
        'dow_sp_leg':      dow_sp_leg,
    }


def estimate_coverage(balance: float, dow_weekly: float) -> int:
    """按当前道指周配额估算预付池还能覆盖几周。周配额为 0 返回 -1（表示无限/无需消耗）。"""
    if dow_weekly <= 0:
        return -1
    return int(balance // dow_weekly)


def compute_beta_gap(market_data: dict, config: dict, us_actual_total: float,
                     sp_multiplier: float = None, ndx_multiplier: float = None) -> dict:
    """整体美股β缺口模型：按本周美股类实际投入算道指应抵扣额。

    背景：建信纳指身兼两职（纳指腿 + 类标普纳指腿），短信只能把它归一类。
    若按标普/纳指分别算缺口会产生循环依赖。故把标普+纳指合并成一个「美股β」
    缺口箱，建信全额算进实际投入，消除循环。

    Args:
        market_data:     market_monitor.get_market_data()（取矩阵倍数）
        config:          dca_prepaid.json 的 'config' 段
        us_actual_total: 本周美股类（US_Blend_Fund + US_Growth_Fund）实际投入合计，
                         含建信全额。通常来自短信解析累加。
        sp_multiplier / ndx_multiplier: 可选，直接指定倍数（便于测试）

    Returns:
        {
            'sp_multiplier', 'ndx_multiplier',
            'total_target',   # 美股β总目标 = 标普目标 + 纳指目标
            'us_actual',      # 本周美股实际投入（回显）
            'gap',            # 缺口 = 总目标 − 实际（可负）
            'dow_deduct',     # 道指应抵扣 = max(0, 缺口 × dow_ratio)，取整到10
        }

    语义：缺口 ≤ 0（实际超额）→ 抵扣 0，不负扣、不回冲预付池余额。
    超额部分记为美股β超配，不由本模块处理。
    """
    sp_mult  = sp_multiplier  if sp_multiplier  is not None else _matrix_multiplier(market_data, 'sp500')
    ndx_mult = ndx_multiplier if ndx_multiplier is not None else _matrix_multiplier(market_data, 'ndx100')

    sp_base   = config.get('sp_target_base', 2500)
    ndx_base  = config.get('ndx_target_base', 2500)
    split     = config.get('synthetic_split', {'dow': 0.5, 'jianxin_ndx': 0.5})
    dow_ratio = split.get('dow', 0.5)

    def _round10(x):
        return round(x / 10) * 10

    total_target = _round10(sp_base * sp_mult) + _round10(ndx_base * ndx_mult)
    gap = total_target - us_actual_total
    dow_deduct = _round10(max(0.0, gap) * dow_ratio)

    return {
        'sp_multiplier':  sp_mult,
        'ndx_multiplier': ndx_mult,
        'total_target':   total_target,
        'us_actual':      us_actual_total,
        'gap':            gap,
        'dow_deduct':     dow_deduct,
    }


def consume_prepaid(data_dir: str, date: str, dow_weekly: float,
                    sp_multiplier: float = None) -> dict:
    """Weekly Update 手动确认时调用：从预付池扣减当周道指配额。

    Args:
        data_dir:      $FAMILYFUND_DATA
        date:          消耗日期 YYYY-MM-DD
        dow_weekly:    本周道指配额（来自 compute_synthetic_dca）
        sp_multiplier: 记录用，可选

    Returns:
        {'balance_after': float, 'consumed': float, 'need_topup': bool}
    """
    data = load_prepaid(data_dir)
    pool = data['dow_prepaid']
    threshold = data['config'].get('topup_threshold', 0.0)

    consumed = float(dow_weekly)
    balance_after = round(pool.get('balance', 0.0) - consumed, 2)
    pool['balance'] = balance_after
    pool.setdefault('consumption_log', []).append({
        'date': date,
        'sp_multiplier': sp_multiplier,
        'consumed': consumed,
        'balance_after': balance_after,
    })
    save_prepaid(data_dir, data)

    return {
        'balance_after': balance_after,
        'consumed': consumed,
        'need_topup': balance_after <= threshold,
    }


def topup_prepaid(data_dir: str, date: str, amount: float) -> dict:
    """记录道指一次性买入，累加到预付池余额。

    Returns:
        {'balance': float}
    """
    data = load_prepaid(data_dir)
    pool = data['dow_prepaid']
    pool['balance'] = round(pool.get('balance', 0.0) + float(amount), 2)
    pool['last_topup_date'] = date
    pool['topup_amount'] = float(amount)
    save_prepaid(data_dir, data)
    return {'balance': pool['balance']}
