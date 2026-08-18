"""sms_parser.py — 基金确认短信解析。

支持格式：
  A: 博时等基金公司（申购关键词，含年份日期）
  B: 南方基金定投格式（定投关键词，不含年份）
  D: 招商银行黄金积存金
  E: 摩根基金（申购/定期定额申购，不含年份，确认日从"持有时间从X月X日"推算）
  F: 建信基金（定期定额申购，日期为8位数字 YYYYMMDD，无净值需反算）

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
    r'.*?份额为([\d,]+\.?\d*)份'             # 份额（支持千分位逗号）
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
    r'.*?份额为([\d,]+\.?\d*)份'             # 份额（支持千分位逗号）
    r'.*?净值为([\d.]+)',                    # 净值
    re.DOTALL,
)

# 格式B：南方基金定投，"定投"关键词，日期不含年份，份额/净值后无"为"
_PAT_B = re.compile(
    r'您(\d{1,2})月(\d{1,2})日定投'         # 定投月日（不用）
    r'(.+?)基金'                             # 基金名称
    r'([\d,]+\.?\d*)\s*元'                   # 金额
    r'于(\d{1,2})月(\d{1,2})日确认成功'     # 确认月日
    r'.*?确认份额([\d,]+\.?\d*)份'           # 份额（无"为"，支持千分位逗号）
    r'.*?成交净值([\d.]+)',                  # 净值（无"为"）
    re.DOTALL,
)

# 格式B2：南方基金申购，"申购"关键词，份额/净值后有"为"
_PAT_B2 = re.compile(
    r'您(\d{1,2})月(\d{1,2})日申购'         # 申购月日（不用）
    r'(.+?)基金'                             # 基金名称
    r'([\d,]+\.?\d*)\s*元'                   # 金额
    r'于(\d{1,2})月(\d{1,2})日确认成功'     # 确认月日
    r'.*?确认份额为([\d,]+\.?\d*)份'         # 份额（有"为"，支持千分位逗号）
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


# 格式E：摩根基金，"您于N月N日通过摩根基金成功申购/定期定额申购...持有时间从N月N日开始计算"
# 确认日 = 持有时间起始日 - 1天（T+1确认，T+2开始持有）
_PAT_E = re.compile(
    r'您于(\d{1,2})月(\d{1,2})日通过摩根基金成功(?:定期定额)?申购'
    r'(.+?)\s*'                               # 基金名称
    r'([\d,]+\.?\d*)\s*元'                    # 金额
    r'.*?成交净值([\d.]+)'                    # 净值
    r'.*?确认份额([\d,]+\.?\d*)份'            # 份额
    r'.*?持有时间从(\d{1,2})月(\d{1,2})日',  # 持有起始月日（确认日+1天）
    re.DOTALL,
)


# 格式F：建信基金，日期为8位 YYYYMMDD，无净值（需从金额/份额反算）
_PAT_F = re.compile(
    r'【建信基金】.*?于(\d{8})提交的'
    r'(.+?)'                              # 基金名称
    r'(?:人民币)?'                        # 可选的"人民币"前缀（不计入基金名）
    r'([\d,]+\.?\d*)\s*元'               # 金额
    r'定期定额申购申请已确认成功'
    r'.*?确认份额([\d,]+\.?\d*)份',      # 份额（支持千分位逗号）
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
        shares    = _parse_amount(m.group(9))
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
        shares    = _parse_amount(m.group(6))
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
        shares    = _parse_amount(m.group(7))
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
        shares    = _parse_amount(m.group(7))
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

    # 格式E：摩根基金
    result = _parse_jpmorgan(sms)
    if result:
        return result

    # 格式F：建信基金（无净值，反算）
    m = _PAT_F.search(sms)
    if m:
        date_str  = m.group(1)  # '20260812'
        y, mo, d_ = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
        fund_name = m.group(2).strip()
        amount    = _parse_amount(m.group(3))
        shares    = _parse_amount(m.group(4))
        nav       = round(amount / shares, 4) if shares > 0 else 0.0
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
            '_brand':        '建信',  # 用于匹配时限定发行方，避免跨公司误匹配
        }

    return None


# ── 格式E：摩根基金 ──────────────────────────────────────────
def _build_jpmorgan(m: re.Match, raw: str) -> dict:
    """从一个 _PAT_E 匹配构造结果字典。"""
    fund_name  = m.group(3).strip()
    amount     = _parse_amount(m.group(4))
    nav        = float(m.group(5))
    shares     = _parse_amount(m.group(6))
    hold_mo, hold_d = int(m.group(7)), int(m.group(8))
    # 持有起始日 - 1 天 = 确认日
    hold_year  = _infer_year(hold_mo)
    hold_date  = date(hold_year, hold_mo, hold_d)
    from datetime import timedelta
    confirm_date = hold_date - timedelta(days=1)
    return {
        'confirm_date': confirm_date.strftime('%Y-%m-%d'),
        'action':       '买入',
        'fund_name':    fund_name,
        'amount':       amount,
        'shares':       shares,
        'nav':          nav,
        'is_gold':      False,
        'raw':          raw,
        'matched_code': None,
        'matched_name': None,
    }


def _parse_jpmorgan(sms: str) -> dict | None:
    """解析摩根基金格式短信（格式E），返回第一笔。"""
    m = _PAT_E.search(sms)
    if not m:
        return None
    return _build_jpmorgan(m, sms)


def _parse_jpmorgan_all(sms: str) -> list[dict]:
    """提取一个块内的所有摩根交易（摩根会把多笔塞进同一条无空行短信）。"""
    return [_build_jpmorgan(m, sms) for m in _PAT_E.finditer(sms)]



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


# ── sms_code_map：持久化精确匹配 ────────────────────────────

def load_sms_map(data_dir: str) -> dict:
    """读取 sms_code_map.json，返回 {fund_name: code} 字典。"""
    import json, os
    path = os.path.join(data_dir, 'sms_code_map.json')
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_sms_map(data_dir: str, sms_map: dict) -> None:
    """写入 sms_code_map.json（原子写入，覆盖全量）。"""
    import json, os
    path = os.path.join(data_dir, 'sms_code_map.json')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(sms_map, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def add_sms_mapping(data_dir: str, fund_name: str, code: str, name: str) -> None:
    """添加一条 fund_name → code 映射并持久化。"""
    sms_map = load_sms_map(data_dir)
    sms_map[fund_name] = {'code': code, 'name': name}
    save_sms_map(data_dir, sms_map)


# ── 公开 API ──────────────────────────────────────────────

def parse_sms(text: str, holdings: list[dict] | None = None,
              data_dir: str | None = None) -> list[dict]:
    """解析短信文本（支持多条，用空行分隔）。

    Args:
        text:     粘贴的短信内容（多条用空行分隔）
        holdings: 持仓列表 [{'code': str, 'name': str}]，用于基金名称匹配
        data_dir: $FAMILYFUND_DATA 路径，用于读取 sms_code_map.json 精确匹配

    Returns:
        解析结果列表，每条对应一条短信
    """
    # 按空行分割多条短信
    blocks = [b.strip() for b in re.split(r'\n\s*\n', text) if b.strip()]
    results = []

    # 加载持久化精确匹配 map（优先于模糊匹配）
    sms_map = load_sms_map(data_dir) if data_dir else {}

    def _attach_match(parsed: dict) -> dict:
        """为解析结果附加持仓匹配。"""
        if parsed['is_gold']:
            parsed.pop('_brand', None)
            if holdings:
                gold = [h for h in holdings if 'GOLD' in h.get('code', '').upper()
                        or '黄金' in h.get('name', '')]
                if gold:
                    parsed['matched_code'] = gold[0]['code']
                    parsed['matched_name'] = gold[0]['name']
            return parsed

        brand = parsed.pop('_brand', None)

        # 1. 优先走精确 map 匹配（不依赖 holdings）
        fund_name = parsed['fund_name']
        if fund_name in sms_map:
            entry = sms_map[fund_name]
            parsed['matched_code'] = entry['code']
            parsed['matched_name'] = entry['name']
            return parsed

        # 2. 模糊匹配（需要 holdings）
        if holdings:
            candidate_holdings = holdings
            if brand:
                branded = [h for h in holdings if brand in h.get('name', '')]
                if branded:
                    candidate_holdings = branded
            code, name = _match_holding(fund_name, candidate_holdings)
            parsed['matched_code'] = code
            parsed['matched_name'] = name

        return parsed
        return parsed

    for block in blocks:
        # 摩根格式：一个块内可能含多笔交易，先尝试全部提取
        jpm_all = _parse_jpmorgan_all(block)
        if jpm_all:
            for parsed in jpm_all:
                results.append(_attach_match(parsed))
            continue

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

        results.append(_attach_match(parsed))

    return results
