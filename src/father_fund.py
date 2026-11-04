"""Independent father-account event ledger. No parent portfolio/NCF mutations.

A single append-only CSV is the source of truth. Corrections append VOID events;
all holdings/cash are rebuilt and validated before an atomic commit.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import csv
import fcntl
import json
import shutil
import tempfile
import uuid

import pandas as pd
from nav_engine import _atomic_write_csv

COLUMNS = ['event_id', 'recorded_at', 'effective_at', 'kind', 'payload']
BUCKETS = ['权益', '债券', '货币', '黄金']
CURRENCIES = ['CNY', 'HKD', 'USD', 'EUR']
PLAN_STATUSES = ['持有', '观察', '待退出', '已完成']
CASH_TYPES = ['入金', '出金', '分红', '利息', '费用']


class LedgerError(ValueError):
    pass


def now_beijing():
    """Use an explicit zone: the Docker image may run in UTC."""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def number(value, label='数值', minimum=None):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise LedgerError(f'{label}必须是有效数字') from None
    if not result.is_finite() or (minimum is not None and result < Decimal(str(minimum))):
        raise LedgerError(f'{label}超出允许范围')
    return result


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).strip())
        if dt.tzinfo is not None:
            raise ValueError()
        return dt.isoformat(timespec='seconds')
    except ValueError:
        raise LedgerError('日期使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS（北京时间）') from None


def event(kind, effective_at, payload, event_id=None):
    return dict(event_id=event_id or str(uuid.uuid4()), recorded_at=now_beijing().isoformat(timespec='microseconds'),
                effective_at=timestamp(effective_at), kind=kind, payload=payload)


def _instrument(p):
    code, name = str(p.get('code', '')).strip(), str(p.get('name', '')).strip()
    if not code or not name or len(code) > 40 or len(name) > 160:
        raise LedgerError('标的代码和名称不能为空，且不得过长')
    if p.get('currency') not in CURRENCIES or p.get('bucket') not in BUCKETS:
        raise LedgerError('请选择有效币种和资产类别')
    fx = number(p.get('fx'), '折算汇率', '0.00000001')
    if p['currency'] == 'CNY' and fx != 1:
        raise LedgerError('人民币资产汇率必须为1')
    price = number(p.get('price'), '价格', '0.00000001')
    venue=p.get('venue','场内')
    if venue not in {'场内','场外'}:
        raise LedgerError('交易场所须为场内或场外')
    return dict(code=code, name=name, currency=p['currency'], bucket=p['bucket'], venue=venue, price=price, fx=fx)


def read_events(data_dir):
    path = Path(data_dir) / 'ledger.csv'
    if not path.exists():
        return []
    try:
        with path.open(encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != COLUMNS:
                raise LedgerError('父亲账户流水字段不匹配，请检查文件，不能继续覆盖')
            rows = list(reader)
        for row in rows:
            row['payload'] = json.loads(row['payload'])
            if not isinstance(row['payload'],dict):
                raise LedgerError('流水内容不是有效对象，请检查原文件')
        return rows
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(f'流水无法读取：{exc}') from exc


def replay(events, as_of=None):
    """Validate full active history; optionally value it at a historical timestamp."""
    by_id = {}
    for e in events:
        if not e.get('event_id') or e['event_id'] in by_id:
            raise LedgerError('流水编号重复或为空')
        timestamp(e['effective_at'])
        if e['kind'] not in {'OPEN', 'TRADE', 'CASH', 'QUOTE', 'PLAN', 'BROKER', 'VOID'}:
            raise LedgerError('未知流水类型')
        by_id[e['event_id']] = e
    voided = set()
    for e in events:
        if e['kind'] == 'VOID':
            target = e['payload'].get('target')
            if target not in by_id or by_id[target]['kind'] in {'OPEN', 'VOID'} or target in voided:
                raise LedgerError('撤销目标不存在、已撤销或不能撤销')
            if not str(e['payload'].get('reason', '')).strip():
                raise LedgerError('撤销必须填写原因')
            voided.add(target)
    active = [(i,e) for i,e in enumerate(events) if e['kind'] != 'VOID' and e['event_id'] not in voided]
    opens = [e for _,e in active if e['kind'] == 'OPEN']
    if not events:
        return None
    if len(opens) != 1:
        raise LedgerError('账户必须且只能有一个期初基准')
    opening = opens[0]
    if any(e['effective_at'] < opening['effective_at'] for _,e in active):
        raise LedgerError('交易或记录不能早于期初基准')
    active.sort(key=lambda pair: (pair[1]['effective_at'], 0 if pair[1]['kind']=='OPEN' else 1, pair[0]))
    if as_of is not None:
        cutoff=timestamp(as_of)
        active=[pair for pair in active if pair[1]['effective_at'] <= cutoff]
        if not active:
            return None
    state = dict(cash=Decimal(0), positions={}, plans={}, broker=None, external_flow=Decimal(0),
                 initial_total=Decimal(0), as_of=opening['effective_at'], voided=voided, trades=[])
    references = set()
    for _,e in active:
        p,kind = e['payload'],e['kind']
        state['as_of'] = max(state['as_of'], e['effective_at'])
        if kind == 'OPEN':
            state['cash']=number(p.get('cash'), '期初净现金', 0)
            for item in p.get('positions', []):
                pos=_instrument(item)
                if pos['code'] in state['positions']:
                    raise LedgerError('期初标的代码重复')
                pos.update(quantity=number(item.get('quantity'), '期初份额', '0.00000001'), quoted_at=e['effective_at'])
                state['positions'][pos['code']]=pos
            state['initial_total']=state['cash']+sum(x['quantity']*x['price']*x['fx'] for x in state['positions'].values())
            if state['initial_total'] <= 0:
                raise LedgerError('期初总资产必须大于0')
        elif kind == 'TRADE':
            ins=_instrument(p)
            side=p.get('side')
            if side not in {'买入','卖出'}:
                raise LedgerError('交易方向无效')
            qty=number(p.get('quantity'),'成交数量','0.00000001')
            cash=number(p.get('cash_cny'),'人民币实际收付','0.00000001')
            number(p.get('fees_cny',0),'费用',0)
            ref=str(p.get('reference','')).strip()
            if not ref:
                raise LedgerError('请填写成交编号；没有券商编号时可自编，须唯一')
            if ref in references:
                raise LedgerError('成交编号重复，未再次保存')
            references.add(ref)
            pos=state['positions'].get(ins['code'])
            if pos and any(pos[k]!=ins[k] for k in ['currency','bucket','venue']):
                raise LedgerError('同一代码的币种或类别不一致')
            if side=='卖出' and (not pos or pos['quantity'] < qty):
                raise LedgerError(f"{ins['name']}卖出数量超过当时持仓")
            if not pos:
                pos=dict(ins,quantity=Decimal(0),quoted_at=e['effective_at'])
                state['positions'][ins['code']]=pos
            pos['quantity'] += qty if side=='买入' else -qty
            # Trade price is an observation at the execution time, never the historical cost.
            pos.update(price=ins['price'],fx=ins['fx'],quoted_at=e['effective_at'])
            state['cash'] += -cash if side=='买入' else cash
            state['trades'].append(e)
        elif kind == 'CASH':
            action=p.get('action')
            if action not in CASH_TYPES:
                raise LedgerError('资金变动类型无效')
            amount=number(p.get('amount'),'资金金额','0.00000001')
            signed=-amount if action in {'出金','费用'} else amount
            state['cash']+=signed
            if action in {'入金','出金'}:
                state['external_flow']+=signed
        elif kind == 'QUOTE':
            for q in p.get('quotes',[]):
                price=number(q.get('price'),'估值价格','0.00000001')
                fx=number(q.get('fx'),'估值汇率','0.00000001')
                quoted_at=timestamp(q.get('as_of'))
                if quoted_at > e['effective_at']:
                    raise LedgerError('报价日期不能晚于估值记录日期')
                pos=state['positions'].get(str(q.get('code')))
                if pos and quoted_at >= pos['quoted_at']:
                    if pos['currency']=='CNY' and fx!=1:
                        raise LedgerError('人民币报价汇率必须为1')
                    pos.update(price=price,fx=fx,quoted_at=quoted_at)
        elif kind == 'PLAN':
            if not p.get('code') or p.get('status') not in PLAN_STATUSES:
                raise LedgerError('计划必须有代码和有效状态')
            if p.get('target_value','') != '':
                number(p['target_value'],'计划金额',0)
            state['plans'][p['code']]=p
        elif kind == 'BROKER':
            for k in ['total','market','available']:
                number(p.get(k),k,0)
            if number(p['market']) > number(p['total']):
                raise LedgerError('无融资账户的市值不能超过总资产')
            codes=set()
            for holding in p.get('holdings',[]):
                if not holding.get('code') or holding['code'] in codes:
                    raise LedgerError('对账持仓代码为空或重复')
                codes.add(holding['code'])
                number(holding.get('quantity'),'券商持仓数量',0)
            state['broker']=e
        if state['cash'] < Decimal('-0.005'):
            raise LedgerError('该时间点净现金不足，请先补齐入金/卖出记录或检查实际收付金额')
    state['market']=sum(x['quantity']*x['price']*x['fx'] for x in state['positions'].values())
    state['equity']=sum(x['quantity']*x['price']*x['fx'] for x in state['positions'].values() if x['bucket']=='权益')
    state['total']=state['cash']+state['market']
    state['pnl']=state['total']-state['initial_total']-state['external_flow']
    return state


def commit_events(data_dir, additions):
    """Serialize read/validate/append/write as one transaction, with idempotency."""
    if not additions:
        raise LedgerError('没有需要保存的记录')
    root=Path(data_dir);root.mkdir(parents=True,exist_ok=True)
    with (root/'account.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            current=read_events(root)
            known={e['event_id']:e for e in current}
            if all(e['event_id'] in known for e in additions):
                if all(all(known[e['event_id']][k]==e[k] for k in ['effective_at','kind','payload']) for e in additions):
                    return replay(current)
                raise LedgerError('同一请求编号对应了不同内容')
            if any(e['event_id'] in known for e in additions):
                raise LedgerError('批次部分已存在，拒绝重复提交')
            combined=current+additions
            result=replay(combined)
            path=root/'ledger.csv'
            # Preserve an exact pre-write copy, in addition to the shared 30-backup rotation.
            if path.exists():
                backup=Path(tempfile.mkdtemp(prefix='father_ledger_backup_'))/'ledger.csv'
                shutil.copy2(path,backup)
            serialized=[dict(e,payload=json.dumps(e['payload'],ensure_ascii=False,sort_keys=True)) for e in combined]
            _atomic_write_csv(pd.DataFrame(serialized,columns=COLUMNS),str(path))
            saved=read_events(root)
            if len(saved)!=len(current)+len(additions) or saved!=combined:
                raise LedgerError('保存后校验失败，请检查备份')
            return result
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def cancel_event(data_dir, event_id, reason):
    return commit_events(data_dir,[event('VOID',now_beijing().isoformat(),dict(target=event_id,reason=reason))])


def reconcile(events, broker_event):
    p=broker_event['payload']
    state=replay(events,as_of=broker_event['effective_at'])
    if not state:
        raise LedgerError('对账日期早于期初')
    reported={h['code']:number(h['quantity']) for h in p.get('holdings',[])}
    computed={k:v['quantity'] for k,v in state['positions'].items() if v['quantity']}
    differences=[]
    if 'holdings' in p:
        for code in sorted(set(reported)|set(computed)):
            if reported.get(code,Decimal(0))!=computed.get(code,Decimal(0)):
                differences.append(dict(code=code,ledger=float(computed.get(code,Decimal(0))),broker=float(reported.get(code,Decimal(0)))))
    return dict(at=broker_event['effective_at'],total_difference=state['total']-number(p['total']),
                market_difference=state['market']-number(p['market']),
                net_cash_difference=state['cash']-(number(p['total'])-number(p['market'])),
                available_gap=number(p['available'])-(number(p['total'])-number(p['market'])),
                holdings=differences)


def holdings_frame(state):
    rows=[]
    for code,p in state['positions'].items():
        if p['quantity']<=0:
            continue
        value=p['quantity']*p['price']*p['fx']
        rows.append({'名称':p['name'],'代码':code,'类别':p['bucket'],'场所':p['venue'],'币种':p['currency'],
                     '股数/份额':float(p['quantity']),'价格':float(p['price']),'折算汇率':float(p['fx']),
                     '市值(元)':float(value),'仓位(%)':float(value/state['total']*100) if state['total'] else 0,
                     '报价时间':p['quoted_at'],'计划状态':state['plans'].get(code,{}).get('status','未设置')})
    return pd.DataFrame(rows)


def _tables(markdown):
    sections={};section=None;headers=None
    for line in markdown.splitlines():
        if line.startswith('## '):
            section=line[3:].strip();sections[section]=[];headers=None
        elif line.strip().startswith('|') and section:
            cells=[c.strip() for c in line.strip().strip('|').split('|')]
            if headers is None:
                headers=cells
            elif all(set(c)<=set('-: ') for c in cells):
                continue
            elif len(cells)!=len(headers):
                raise LedgerError(f'{section}表格列数不一致；备注中不要使用竖线')
            else:
                sections[section].append(dict(zip(headers,cells)))
    return sections


def parse_input(markdown):
    """Parse user-editable Chinese Markdown tables; preview does not write."""
    tables=_tables(markdown)
    account={r.get('字段'):r.get('值','') for r in tables.get('1. 期初账户',[])}
    when=timestamp(account.get('基准时间',''))
    cash=number(account.get('期初净现金（人民币）',''),'期初净现金',0)
    positions=[]
    for r in tables.get('2. 期初持仓',[]):
        if not any(r.values()):
            continue
        if not r.get('代码'):
            raise LedgerError('期初持仓行缺少代码')
        if not r.get('数量'):
            raise LedgerError(f"请填写{r.get('名称','')}的期初数量；期初未持有请填0")
        qty=number(r['数量'],'期初数量',0)
        if not qty:
            continue
        positions.append(dict(code=r['代码'],name=r['名称'],bucket=r['类别'],venue=r.get('交易场所','场内'),currency=r['币种'],quantity=str(qty),price=r['期初价格'],fx=r['折算汇率']))
    events=[event('OPEN',when,dict(cash=str(cash),positions=positions))]
    for r in tables.get('3. 基准之后的成交',[]):
        if not r.get('成交编号'):
            if any(r.values()):
                raise LedgerError('成交行缺少成交编号；空白占位行可删除')
            continue
        payload=dict(reference=r['成交编号'],side=r['方向'],code=r['代码'],name=r['名称'],bucket=r['类别'],venue=r.get('交易场所','场内'),currency=r['币种'],quantity=r['数量'],price=r['成交价'],fx=r['折算汇率'],cash_cny=r['人民币实际收付'],fees_cny=r['费用（已含）'] or '0',note=r['备注'])
        events.append(event('TRADE',r['成交时间'],payload))
    for r in tables.get('4. 其他资金变动',[]):
        if not any(r.values()):
            continue
        events.append(event('CASH',r['发生时间'],dict(action=r['类型'],amount=r['人民币金额'],note=r['备注'])))
    replay(events)
    return events


def parse_reference(markdown):
    """Latest broker figures are evidence only, never automatically an opening."""
    tables=_tables(markdown)
    info={r.get('字段'):r.get('值','') for r in tables.get('5. 最新券商对账',[])}
    if not info.get('总资产（人民币）'):
        return None
    p=dict(total=info['总资产（人民币）'],market=info['证券市值（人民币）'],available=info['可用资金（人民币）'])
    for k in p:number(p[k],k,0)
    p['holdings']=[dict(code=r['代码'],name=r['名称'],quantity=r['数量']) for r in tables.get('6. 最新持仓参考',[]) if r.get('代码')]
    # Date alone can display a reference, but may not silently claim an exact checkpoint time.
    return dict(date=info.get('记录日期',''),effective_at=info.get('对账时间（选填）',''),payload=p,
                rows=tables.get('6. 最新持仓参考',[]))
