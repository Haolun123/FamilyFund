"""Father-only CSV drafts, instrument registry and snapshot reporting.

The existing append-only ledger remains authoritative. No parent/Son writes.
"""
from collections import Counter
from pathlib import Path
from decimal import Decimal
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import fcntl

import pandas as pd
from father_fund import LedgerError, event, number, timestamp, replay, read_events, commit_events, now_beijing
from nav_engine import compute_fund_nav, compute_cost_basis

TYPES = ['股票', '场内ETF', '场外基金']


def canonical_code(code):
    code = str(code).strip().upper()
    if re.fullmatch(r'HK\d+', code):
        return str(int(code[2:])).zfill(4)+'.HK'
    if re.fullmatch(r'\d+\.HK', code):
        return str(int(code[:-3])).zfill(4)+'.HK'
    if re.fullmatch(r'\d{6}\.(SS|SZ)', code):
        return code[:6]
    return code


def load_registry(data_dir):
    path = Path(data_dir)/'instrument_map.json'
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or any(not isinstance(v,dict) for v in raw.values()):
            raise ValueError('标的映射结构无效')
        return raw
    except (ValueError, OSError) as exc:
        raise LedgerError(f'父亲账户标的映射无法读取：{exc}') from exc


def save_instrument(data_dir, metadata):
    """Merge one entry under its own lock; preserve originals before replacing."""
    root = Path(data_dir); root.mkdir(parents=True,exist_ok=True)
    with (root/'instrument_map.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            registry = load_registry(root)
            meta = dict(metadata)
            key = canonical_code(meta.get('code',''))
            if not key or not str(meta.get('name','')).strip():
                raise LedgerError('标的代码和名称不能为空')
            if meta.get('validation_status') not in {'confirmed','pending'}:
                raise LedgerError('标的验证状态无效')
            registry[key] = meta
            path = root/'instrument_map.json'
            if path.exists():
                backup = Path(tempfile.mkdtemp(prefix='father_instrument_backup_'))/path.name
                shutil.copy2(path,backup)
                backups = root/'backups'; backups.mkdir(exist_ok=True)
                shutil.copy2(path,backups/('instrument_map_'+uuid.uuid4().hex+'.json'))
                for old in sorted(backups.glob('instrument_map_*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[30:]:
                    old.unlink()
            descriptor, temporary = tempfile.mkstemp(prefix='.instrument_map_',suffix='.tmp',dir=root)
            try:
                with os.fdopen(descriptor,'w',encoding='utf-8') as out:
                    json.dump(registry,out,ensure_ascii=False,indent=2,allow_nan=False)
                    out.flush(); os.fsync(out.fileno())
                os.replace(temporary,path)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
            if load_registry(root) != registry:
                raise LedgerError('标的映射保存后校验失败')
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def candidate(code, name='', currency='CNY', bucket='权益', kind='股票', venue=None, quote_code=None):
    code = str(code).strip()
    normalized = canonical_code(code)
    listed = kind!='场外基金' if venue is None else venue=='场内'
    if not quote_code:
        if normalized.endswith('.HK'): quote_code=normalized
        elif normalized=='SAP.DE': quote_code=normalized
        elif re.fullmatch(r'\d{6}',normalized) and listed:
            quote_code=normalized+('.SS' if normalized.startswith(('5','6')) else '.SZ')
        else: quote_code=normalized
    source = 'eastmoney' if not listed else 'yfinance'
    return dict(code=code,name=name,currency=currency,bucket=bucket,type=kind,
                venue='场内' if listed else '场外',source=source,quote_code=quote_code,
                aliases=sorted(set([code,normalized])),validation_status='pending')


def _holding_metadata(p):
    code = p['code']
    venue = p.get('venue','场内')
    kind = '场外基金' if venue=='场外' else ('场内ETF' if canonical_code(code).startswith(('159','5')) else '股票')
    return candidate(code,p['name'],p['currency'],p['bucket'],kind,venue)


def find_instrument(code, state, registry, parent_data_dir):
    """Only read parent files directly; its loader may write config migrations."""
    key = canonical_code(code)
    entry = registry.get(key)
    if entry and entry.get('validation_status')=='confirmed':
        result = dict(entry)
        # Keep the existing father holding key, including original aliases.
        result['code'] = next((c for c in state['positions'] if canonical_code(c)==key),entry['code'])
        return result
    for c,p in state['positions'].items():
        if canonical_code(c)==key:
            result = _holding_metadata(p)
            result['validation_status']='confirmed'; result['verified_by']='既有父亲持仓'
            return result
    path = Path(parent_data_dir)/'portfolio.csv'
    if not path.exists(): return None
    frame = pd.read_csv(path,dtype={'Code':str}).fillna('')
    latest = frame[frame.Date==frame.Date.max()]
    matches = latest[(latest.Asset_Class!='Cash') & (pd.to_numeric(latest.Shares,errors='coerce')>0) &
                     latest.Code.map(canonical_code).eq(key)]
    if len(matches)!=1: return None
    row = matches.iloc[0]
    cls = row['Asset_Class']
    listed = key.endswith('.HK') or key=='SAP.DE' or (key.isdigit() and key.startswith(('159','5','6'))) or cls in {'Individual_Stock','Stock','ETF_Stock'}
    kind = '场外基金' if not listed else ('场内ETF' if key.startswith(('159','5')) else '股票')
    bucket = {'Fixed_Income':'债券','Gold':'黄金'}.get(cls,'权益')
    result = candidate(code,row['Name'],row['Currency'],bucket,kind)
    symbol_path = Path(parent_data_dir)/'yf_symbols.json'
    if symbol_path.exists() and key!='SAP.DE':
        mapping = json.loads(symbol_path.read_text(encoding='utf-8'))
        match = next((v for c,v in mapping.items() if not c.startswith('_') and canonical_code(c)==key),None)
        symbol = match.get('yf_symbol') if isinstance(match,dict) else match
        if symbol and listed: result['quote_code']=symbol
    result['verified_by']='主基金持仓参考，待确认'
    return result


def fetch_quote(meta):
    """One request through the existing price router; no source-file mutations."""
    from price_fetcher import fetch_latest_prices
    routed=meta['quote_code']
    if meta['source']=='yfinance' and re.fullmatch(r'\d{6}',routed):
        raise LedgerError('场内标的行情代码须含交易所后缀')
    if meta['source']=='eastmoney' and not re.fullmatch(r'\d{6}',routed):
        raise LedgerError('场外基金行情代码须为六位基金代码')
    result=fetch_latest_prices(pd.DataFrame([dict(Date=now_beijing().date().isoformat(),Code=routed)])).get(routed,{})
    result=dict(result)
    try:
        valid=result.get('status')=='ok' and number(result.get('price'),'价格','0.00000001')>0
        if result.get('currency') and result['currency']!=meta['currency']:
            valid=False
        if meta['currency']!='CNY':
            valid=valid and number(result.get('fx_rate'),'汇率','0.00000001')>0
    except LedgerError:
        valid=False
    if not valid:
        result.update(status='error' if result.get('status')!='manual' else 'manual',price=None)
    return result


def verify_instrument(meta):
    """Metadata identity is required separately from successful quote retrieval."""
    result=dict(meta,validation_status='pending')
    try:
        if canonical_code(result['code']) != canonical_code(result['quote_code']):
            raise LedgerError('账户代码与行情代码不对应，请核对交易所及代码')
        if result['source']=='eastmoney':
            from price_fetcher import lookup_fund_name
            if result['type']!='场外基金' or result['currency']!='CNY':
                raise LedgerError('场外基金来源须对应人民币场外基金')
            api_name=lookup_fund_name(result['quote_code'])
            if not api_name: raise LedgerError('API未返回该代码对应的基金名称')
            result['api_name']=api_name
        else:
            import yfinance as yf
            info=yf.Ticker(result['quote_code']).info
            allowed={'股票':{'EQUITY'},'场内ETF':{'ETF'}}.get(result['type'],set())
            api_name=info.get('longName') or info.get('shortName')
            returned=info.get('symbol','')
            if not api_name or info.get('quoteType') not in allowed or returned.upper()!=result['quote_code'].upper():
                raise LedgerError('API返回的代码或证券类型无法核实')
            if info.get('currency')!=result['currency']:
                raise LedgerError('API报价币种与填写币种不一致')
            result['api_name']=api_name
        result.update(validation_status='confirmed',verified_by='API标的信息',verified_at=now_beijing().isoformat(timespec='seconds'))
    except Exception as exc:
        result['validation_error']=str(exc) if isinstance(exc,LedgerError) else '标的信息查询失败，请稍后重新验证'
    try: quote=fetch_quote(result)
    except Exception: quote=dict(status='error',price=None,msg='价格获取失败，请手动填写')
    return result,quote


def parse_trades(text):
    rows=[]
    for cells in csv.reader(text.splitlines()):
        if not cells or not any(c.strip() for c in cells): continue
        cells=[c.strip() for c in cells]
        if not rows and cells[0] in {'日期','Date'}: continue
        if len(cells)!=6: raise LedgerError('每行须有六列：日期,买入/卖出,代码,数量,成交价,人民币收付')
        at=timestamp(cells[0])
        if len(cells[0])==10: at=at[:10]+'T23:59:58'
        if cells[1] not in {'买入','卖出'}: raise LedgerError('交易方向须为买入或卖出')
        if not cells[2]: raise LedgerError('交易代码不能为空')
        row=dict(at=at,side=cells[1],code=cells[2],quantity=str(number(cells[3],'数量','0.00000001')),
                 price=str(number(cells[4],'成交价','0.00000001')),cash_cny=str(number(cells[5],'人民币收付','0.00000001')))
        rows.append(row)
    if not rows: raise LedgerError('请填写交易CSV')
    return rows


def trade_fingerprint(row):
    fields=[str(row['at'])[:10],row['side'],canonical_code(row['code'])]
    fields += [str(number(row[k]).normalize()) for k in ['quantity','price','cash_cny']]
    return hashlib.sha256('|'.join(fields).encode()).hexdigest()


def duplicate_counts(rows, events):
    state=replay(events)
    fingerprints=[]
    for e in events:
        if e['kind']=='TRADE' and e['event_id'] not in state['voided']:
            fingerprints.append(trade_fingerprint(dict(e['payload'],at=e['effective_at'])))
    known=Counter(fingerprints); incoming=Counter(trade_fingerprint(r) for r in rows)
    return sum(min(n,known[key]) for key,n in incoming.items())


def build_trade_batch(rows, metadata, batch_id):
    events=[]
    for i,row in enumerate(rows):
        meta=metadata.get(canonical_code(row['code']))
        if not meta or meta.get('validation_status')!='confirmed':
            raise LedgerError(f"{row['code']}尚未确认标的信息及行情映射")
        fx='1' if meta['currency']=='CNY' else str(number(row['cash_cny'])/number(row['quantity'])/number(row['price']))
        payload={k:meta[k] for k in ['code','name','bucket','venue','currency']}
        payload.update(side=row['side'],quantity=row['quantity'],price=row['price'],fx=fx,
                       cash_cny=row['cash_cny'],fees_cny='0',reference=f'CSV-{batch_id}-{i+1}',
                       csv_fingerprint=trade_fingerprint(row),note='CSV录入；人民币收付已含费用')
        events.append(event('TRADE',row['at'],payload,event_id=f'csv-{batch_id}-{i+1}'))
    return events


def snapshot_event(events, at, quotes, event_id=None):
    at=timestamp(at); state=replay(events)
    if at[:10]>now_beijing().date().isoformat(): raise LedgerError('快照日期不能在未来')
    if any(e['effective_at']>at for e in events if e['kind'] in {'OPEN','TRADE','CASH','QUOTE','SNAPSHOT'} and e['event_id'] not in state['voided']):
        raise LedgerError('快照日期早于已保存或本期交易，请核对交易范围')
    return event('SNAPSHOT',at,dict(quotes=quotes),event_id)


def save_snapshot(data_dir, base_ids, pending, at, quotes, token):
    """Check a stale browser before committing a complete draft as one batch."""
    current=read_events(data_dir)
    if [e['event_id'] for e in current]!=base_ids:
        ids={e['event_id'] for e in current}
        if token in ids:
            saved=next(e for e in current if e['event_id']==token)
            if saved['effective_at']!=timestamp(at) or saved['payload']!={'quotes':quotes}:
                raise LedgerError('同一保存请求对应不同快照内容，请重新读取账户')
            known={e['event_id']:e for e in current}
            if any(known.get(e['event_id'])!=e for e in pending):
                raise LedgerError('同一保存请求对应不同交易内容，请重新读取账户')
            return replay(current)
        raise LedgerError('账户流水已被其他页面更新，请重新读取后核对草稿')
    snapshot=snapshot_event(current+pending,at,quotes,token)
    return commit_events(data_dir,pending+[snapshot],expected_ids=base_ids)


def snapshot_portfolio(events):
    """Derive NAV-compatible frames without rewriting the existing journal."""
    state=replay(events)
    if not state: return pd.DataFrame()
    active=[e for e in events if e['kind']!='VOID' and e['event_id'] not in state['voided']]
    opening=next(e for e in active if e['kind']=='OPEN')
    marks=[opening['effective_at']]
    snapshots=[e for e in active if e['kind']=='SNAPSHOT']
    first=min((e['effective_at'] for e in snapshots),default=None)
    # Preserve old explicit valuation checkpoints. Batch trades are not snapshots.
    legacy=[e['effective_at'] for e in active if e['kind']=='QUOTE' and (not first or e['effective_at']<first)]
    marks.extend(legacy)
    if not snapshots:
        financial=[e['effective_at'] for e in active if e['kind'] in {'TRADE','CASH','QUOTE'}]
        if financial: marks.append(max(financial))
    marks.extend(e['effective_at'] for e in snapshots)
    marks=sorted(set(marks))
    rows=[]; previous=None
    for at in marks:
        valued=replay(events,at)
        flows={}; external=Decimal(0)
        for e in active:
            if e['effective_at']>at or (previous and e['effective_at']<=previous): continue
            p=e['payload']
            if e['kind']=='TRADE':
                flows[p['code']]=flows.get(p['code'],Decimal(0))+number(p['cash_cny'])*(1 if p['side']=='买入' else -1)
            elif e['kind']=='CASH' and p['action'] in {'入金','出金'}:
                external+=number(p['amount'])*(1 if p['action']=='入金' else -1)
        for code,p in valued['positions'].items():
            value=p['quantity']*p['price']*p['fx']
            rows.append(dict(Date=at,Asset_Class={'权益':'Individual_Stock','债券':'Fixed_Income','货币':'Fixed_Income','黄金':'Gold'}[p['bucket']],
                             Platform='父亲账户',Name=p['name'],Code=code,Currency=p['currency'],Exchange_Rate=float(p['fx']),
                             Shares=float(p['quantity']),Current_Price=float(p['price']),Total_Value=float(value),
                             Net_Cash_Flow=float(value if previous is None else flows.get(code,0))))
        rows.append(dict(Date=at,Asset_Class='Cash',Platform='父亲账户',Name='现金',Code='CASH',Currency='CNY',Exchange_Rate=1.,
                         Shares=float(valued['cash']),Current_Price=1.,Total_Value=float(valued['cash']),
                         Net_Cash_Flow=float(valued['cash'] if previous is None else external)))
        previous=at
    return pd.DataFrame(rows)


def performance(events):
    frame=snapshot_portfolio(events)
    if frame.empty: return pd.DataFrame(),pd.DataFrame()
    return compute_fund_nav(frame),compute_cost_basis(frame)
