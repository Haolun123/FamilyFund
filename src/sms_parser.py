"""sms_parser.py — 基金确认短信解析。

支持格式：
  A: 博时等基金公司（申购关键词，含年份日期）
  B: 南方基金定投格式（定投关键词，不含年份）
  D: 招商银行黄金积存金

返回结构：
    [
        {
            'confirm_date': 'YYYY-MM-DD',  # 确认日期
            'action':       '买入' | '卖出',
            'fund_name':    str,            # 短信中的基金名称
            'amount':       float,          # 金额 CNY
            'shares':       float,          # 份额（克数用于黄金）
            'nav':          float,          # 净值（黄金为元/克）
            'is_gold':      bool,           # 是否为黄金积存金
            'raw':          str,            # 原始短信
            'matched_code': str | None,     # 匹配到的持仓 Code
            'matched_name': str | None,     # 匹配到的持仓 Name
        }
    ]
"""

import re
from datetime import date, datetime
from difflib import get_close_matches


# ── 正则 ──────────────────────────────────────────────────

# 格式A：博时等，含完整年份，"申购/买入"关键词，确认日期在申购日之后
_PAT_A = re.compile(
    r'于(\d{4})年(\d{1,2})月(\d{1,2})日'   # 申购日（不用）
    r'.*?(申购|买入)'
    r'(.+?)\s*'                              # 基金名称
    r'([\d,]+\.?\d*)\s*元'                   # 金额
    r'.*?(\d{1,2})月(\d{1,2})日确认成功'    # 确认月日
    r'.*?份额为([\d.]+)份'                   # 份额
    r'.*?净值为([\d.]+)',                    # 净值
    re.DOTALL,
)

# 格式A2：博时定投格式，"YYYY年MM月DD日您通过...定期定投...确认成功，份额为...净值为"
_PAT_A2 = re.compile(
    r'(\d{4})年(\d{1,2})月(\d{1,2})日'      # 确认日期（含完整年份）
    r'.*?定(?:期定投|投)'
    r'.*?(?:（博时钱包支付）)?\s*'
    r'(.+?)\s*'                              # 基金名称
    r'([\d,]+\.?\d*)\s*元'                   # 金额
    r'.*?确认成功'
    r'.*?份额为([\d.]+)份'                   # 份额
    r'.*?净值为([\d.]+)',                    # 净值
    re.DOTALL,
)

# 格式B：南方基金定投，"定投"关键词，日期不含年份，份额/净值后无"为"
_PAT_B = re.compile(
    r'您(\d{1,2})月(\d{1,2})日定投'         # 定投月日（不用）
    r'(.+?)基金'                             # 基金名称
    r'([\d,]+\.?\d*)\s*元'                   # 金额
    r'于(\d{1,2})月(\d{1,2})日确认成功'     # 确认月日
    r'.*?确认份额([\d.]+)份'                 # 份额（无"为"）
    r'.*?成交净值([\d.]+)',                  # 净值（无"为"）
    re.DOTALL,
)

# 格式B2：南方基金申购，"申购"关键词，份额/净值后有"为"
_PAT_B2 = re.compile(
    r'您(\d{1,2})月(\d{1,2})日申购'         # 申购月日（不用）
    r'(.+?)基金'                             # 基金名称
    r'([\d,]+\.?\d*)\s*元'                   # 金额
    r'于(\d{1,2})月(\d{1,2})日确认成功'     # 确认月日
    r'.*?确认份额为([\d.]+)份'               # 份额（有"为"）
    r'.*?成交净值为([\d.]+)',                # 净值（有"为"）
    re.DOTALL,
)

# 格式D：招商银行黄金积存金
_PAT_D = re.compile(
    r'已于(\d{4})年(\d{1,2})月(\d{1,2})日扣款成功'  # 确认日期
    r'.*?定投([\d.]+)\s*克黄金'                       # 克数
    r'.*?扣款金额人民币([\d.]+)元',                   # 金额
    re.DOTALL,
)


def _parse_amount(s: str) -> float:
    """去掉逗号后转 float。"""
    return float(s.replace(',', ''))


def _infer_year(month: int) -> int:
    """短信只有月份时推断年份（超过当前月则取上一年）。"""
    today = date.today()
    if month > today.month:
        return today.year - 1
    return today.year


def _parse_one(sms: str) -> dict | None:
    """解析单条短信，返回结构化结果，无法解析返回 None。"""
    sms = sms.strip()
    if not sms:
        return None

    # 格式D：黄金（优先，防止被其他格式误匹配）
    m = _PAT_D.search(sms)
    if m:
        y, mo, d_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
        grams  = float(m.group(4))
        amount = float(m.group(5))
        nav    = round(amount / grams, 4) if grams > 0 else 0.0
        return {
            'confirm_date': f'{y:04d}-{mo:02d}-{d_:02d}',
            'action':       '买入',
            'fund_name':    '黄金积存金',
            'amount':       amount,
            'shares':       grams,
            'nav':          nav,
            'is_gold':      True,
            'raw':          sms,
            'matched_code': None,
            'matched_name': None,
        }

    # 格式A：博时申购，含完整年份
    m = _PAT_A.search(sms)
    if m:
        base_year = int(m.group(1))
        confirm_mo, confirm_d = int(m.group(7)), int(m.group(8))
        fund_name = m.group(5).strip()
        amount    = _parse_amount(m.group(6))
        shares    = float(m.group(9))
        nav       = float(m.group(10))
        return {
            'confirm_date': f'{base_year:04d}-{confirm_mo:02d}-{confirm_d:02d}',
            'action':       '买入',
            'fund_name':    fund_name,
            'amount':       amount,
            'shares':       shares,
            'nav':          nav,
            'is_gold':      False,
            'raw':          sms,
            'matched_code': None,
            'matched_name': None,
        }

    # 格式A2：博时定投，"YYYY年MM月DD日您通过...定期定投...确认成功"
    m = _PAT_A2.search(sms)
    if m:
        y, mo, d_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
        fund_name = m.group(4).strip()
        amount    = _parse_amount(m.group(5))
        shares    = float(m.group(6))
        nav       = float(m.group(7))
        return {
            'confirm_date': f'{y:04d}-{mo:02d}-{d_:02d}',
            'action':       '买入',
            'fund_name':    fund_name,
            'amount':       amount,
            'shares':       shares,
            'nav':          nav,
            'is_gold':      False,
            'raw':          sms,
            'matched_code': None,
            'matched_name': None,
        }

    # 格式B：南方基金定投（"定投"关键词，份额/净值无"为"）
    m = _PAT_B.search(sms)
    if m:
        confirm_mo, confirm_d = int(m.group(5)), int(m.group(6))
        year      = _infer_year(confirm_mo)
        fund_name = m.group(3).strip()
        amount    = _parse_amount(m.group(4))
        shares    = float(m.group(7))
        nav       = float(m.group(8))
        return {
            'confirm_date': f'{year:04d}-{confirm_mo:02d}-{confirm_d:02d}',
            'action':       '买入',
            'fund_name':    fund_name,
            'amount':       amount,
            'shares':       shares,
            'nav':          nav,
            'is_gold':      False,
            'raw':          sms,
            'matched_code': None,
            'matched_name': None,
        }

    # 格式B2：南方基金申购（"申购"关键词，份额/净值有"为"）
    m = _PAT_B2.search(sms)
    if m:
        confirm_mo, confirm_d = int(m.group(5)), int(m.group(6))
        year      = _infer_year(confirm_mo)
        fund_name = m.group(3).strip()
        amount    = _parse_amount(m.group(4))
        shares    = float(m.group(7))
        nav       = float(m.group(8))
        return {
            'confirm_date': f'{year:04d}-{confirm_mo:02d}-{confirm_d:02d}',
            'action':       '买入',
            'fund_name':    fund_name,
            'amount':       amount,
            'shares':       shares,
            'nav':          nav,
            'is_gold':      False,
            'raw':          sms,
            'matched_code': None,
            'matched_name': None,
        }

    return None


# ── 基金名称匹配 ──────────────────────────────────────────

def _match_holding(fund_name: str, holdings: list[dict]) -> tuple[str | None, str | None]:
    """将短信基金名称模糊匹配到持仓。

    Args:
        fund_name: 短信中的基金名称
        holdings:  [{'code': str, 'name': str}, ...]

    Returns:
        (matched_code, matched_name) 或 (None, None)
    """
    if not holdings:
        return None, None

    names = [h['name'] for h in holdings]

    # 1. 关键词包含匹配（去除常见后缀后比较）
    def _normalize(s: str) -> str:
        for suffix in ['ETF联接', 'ETF', '联接', 'A类', 'E类', 'I类', 'C类', 'A', 'E', 'I', 'C']:
            s = s.replace(suffix, '')
        return s.strip()

    fn_norm = _normalize(fund_name)
    for h in holdings:
        hn_norm = _normalize(h['name'])
        if fn_norm in hn_norm or hn_norm in fn_norm:
            return h['code'], h['name']

    # 2. 关键词分词匹配
    keywords = re.findall(r'[A-Za-z0-9\u4e00-\u9fff]+', fund_name)
    best_score = 0
    best = None
    for h in holdings:
        score = sum(1 for kw in keywords if kw in h['name'])
        if score > best_score:
            best_score = score
            best = h
    if best_score >= 1 and best is not None:
        return best['code'], best['name']

    # 3. difflib 模糊匹配
    matches = get_close_matches(fund_name, names, n=1, cutoff=0.5)
    if matches:
        for h in holdings:
            if h['name'] == matches[0]:
                return h['code'], h['name']

    return None, None


# ── 公开 API ──────────────────────────────────────────────

def parse_sms(text: str, holdings: list[dict] | None = None) -> list[dict]:
    """解析短信文本（支持多条，用空行分隔）。

    Args:
        text:     粘贴的短信内容（多条用空行分隔）
        holdings: 持仓列表 [{'code': str, 'name': str}]，用于基金名称匹配

    Returns:
        解析结果列表，每条对应一条短信
    """
    # 按空行分割多条短信
    blocks = [b.strip() for b in re.split(r'\n\s*\n', text) if b.strip()]
    results = []
    for block in blocks:
        parsed = _parse_one(block)
        if parsed is None:
            # 无法解析，返回错误条目
            results.append({
                'confirm_date': None,
                'action':       None,
                'fund_name':    '无法解析',
                'amount':       None,
                'shares':       None,
                'nav':          None,
                'is_gold':      False,
                'raw':          block,
                'matched_code': None,
                'matched_name': None,
                'parse_error':  True,
            })
            continue

        # 基金名称匹配
        if holdings and not parsed['is_gold']:
            code, name = _match_holding(parsed['fund_name'], holdings)
            parsed['matched_code'] = code
            parsed['matched_name'] = name
        elif parsed['is_gold'] and holdings:
            # 黄金：匹配持仓中 Asset_Class=Gold 的项
            gold = [h for h in holdings if 'GOLD' in h.get('code', '').upper()
                    or '黄金' in h.get('name', '')]
            if gold:
                parsed['matched_code'] = gold[0]['code']
                parsed['matched_name'] = gold[0]['name']

        results.append(parsed)

    return results
