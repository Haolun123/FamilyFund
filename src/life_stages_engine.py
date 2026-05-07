"""life_stages_engine.py — 人生阶段规划：动态支出曲线计算。

读取 $FAMILYFUND_DATA/life_stages.json，展开为逐年支出序列，
供 FI 测算使用，替代静态 annual_expense_target_cny。
"""

import json
import os
from datetime import date


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, 'life_stages.json')


def load_life_stages(data_dir: str) -> dict | None:
    p = _path(data_dir)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save_life_stages(data_dir: str, data: dict):
    p = _path(data_dir)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def compute_expense_curve(data: dict, scenario: str = 'base') -> dict:
    """将 life_stages.json 展开为逐年支出序列。

    Args:
        data:     load_life_stages() 的返回值
        scenario: 'pessimistic' / 'base' / 'optimistic'

    Returns:
        {
            year (int): {
                'total':      float,   # 当年总支出 CNY
                'components': {        # 各来源分项
                    'milestone_id': float,
                    ...
                }
            }
        }
    """
    current_year = date.today().year
    end_year     = current_year + 60  # 展望60年

    # 逐年初始化
    curve = {y: {'total': 0.0, 'components': {}} for y in range(current_year, end_year + 1)}

    for ms in data.get('milestones', []):
        if not ms.get('enabled', True):
            continue

        ms_id  = ms['id']
        sc     = ms['scenarios'].get(scenario) or ms['scenarios'].get('base', {})
        inf    = ms.get('inflation_rate', 0.025)

        if ms_id == 'property':
            # 一次性置业：首付 + 月供
            target_year = ms.get('target_year', current_year + 5)
            if target_year in curve:
                dp = sc.get('down_payment_cny', 0)
                # 通胀调整（从当前年到目标年）
                years_away = target_year - current_year
                dp_adj = dp * (1 + inf) ** years_away
                curve[target_year]['total'] += dp_adj
                curve[target_year]['components'][ms_id] = dp_adj

            # 月供（目标年之后每年）
            monthly = sc.get('monthly_mortgage_cny', 0)
            if monthly > 0:
                for y in range(target_year, min(target_year + 30, end_year + 1)):
                    annual = monthly * 12
                    curve[y]['total'] += annual
                    curve[y]['components'][ms_id] = curve[y]['components'].get(ms_id, 0) + annual

        elif ms_id == 'higher_education':
            # 总额分摊到年限内
            start = ms.get('start_year', current_year)
            end   = ms.get('end_year', start + 4)
            years = max(end - start, 1)
            total = sc.get('total_cny', 0)
            years_away = start - current_year
            total_adj = total * (1 + inf) ** max(years_away, 0)
            annual = total_adj / years
            for y in range(start, min(end, end_year + 1)):
                curve[y]['total'] += annual
                curve[y]['components'][ms_id] = annual

        else:
            # 常规年度支出（早期养育/K12/退休）
            start = ms.get('start_year', current_year)
            end   = ms.get('end_year', end_year)  # 退休无 end_year
            annual_base = sc.get('annual_cny', 0)
            for y in range(max(start, current_year), min(end, end_year + 1)):
                years_away = y - current_year
                annual_adj = annual_base * (1 + inf) ** years_away
                curve[y]['total'] += annual_adj
                curve[y]['components'][ms_id] = annual_adj

    # 四舍五入
    for y in curve:
        curve[y]['total'] = round(curve[y]['total'])
        curve[y]['components'] = {k: round(v) for k, v in curve[y]['components'].items()}

    return curve


def get_milestone_summary(data: dict, scenario: str, ms_id: str) -> str:
    """返回里程碑的简要金额描述，用于 UI 展示。"""
    for ms in data.get('milestones', []):
        if ms['id'] != ms_id:
            continue
        sc = ms['scenarios'].get(scenario) or ms['scenarios'].get('base', {})
        if ms_id == 'property':
            dp = sc.get('down_payment_cny', 0)
            mo = sc.get('monthly_mortgage_cny', 0)
            return f"首付 ¥{dp/10000:.0f}万 + 月供 ¥{mo:,}"
        elif ms_id == 'higher_education':
            total = sc.get('total_cny', 0)
            return f"总计 ¥{total/10000:.0f}万"
        else:
            annual = sc.get('annual_cny', 0)
            start  = ms.get('start_year', 0)
            end    = ms.get('end_year', 0)
            return f"¥{annual/10000:.0f}万/年  {start}-{end}"
    return '—'
