"""Read-only three-account weekly reports and explicit valuation-date evidence."""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import copy
import fcntl
import hashlib
import json
import math
import os
import tempfile
import uuid

import pandas as pd
from nav_engine import _atomic_write_csv, compute_fund_nav, CLASS_DISPLAY_NAMES
from father_fund import read_events, replay
from father_workflow import snapshot_portfolio

LABELS = {'main': '主基金', 'son': '儿子基金', 'father': '父亲基金'}


class ReportError(ValueError):
    def __init__(self, issues):
        self.issues = issues if isinstance(issues, list) else [str(issues)]
        super().__init__('；'.join(self.issues))


def today_beijing():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError) as exc:
        raise ReportError(f'无效日期：{value}') from exc


def week_friday(value):
    value = day(value)
    return value + timedelta(days=4-value.weekday())


def period(start, end, today=None):
    start, end = week_friday(start), week_friday(end)
    if start > end:
        raise ReportError('起始周不能晚于结束周')
    if end > (today or today_beijing()):
        raise ReportError('结束周尚未结束，请选择已经结束的周')
    weeks = []
    at = start
    while at <= end:
        weeks.append(at.isoformat())
        at += timedelta(days=7)
    return {'start': start.isoformat(), 'end': end.isoformat(),
            'baseline': (start-timedelta(days=7)).isoformat(), 'weeks': weeks}


def account_paths(data_dir, son_dir=None, father_dir=None):
    root = Path(data_dir)
    return {'main': root, 'son': Path(son_dir) if son_dir else root/'son',
            'father': Path(father_dir) if father_dir else root/'father'}


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def source_versions(paths):
    return {f'{key}/{name}': _hash(root/name) for key, root in paths.items()
            for name in (['ledger.csv', 'valuation_dates.csv'] if key == 'father'
                         else ['portfolio.csv', 'transaction.csv', 'valuation_dates.csv'])}


def _read(path):
    try:
        return pd.read_csv(path, dtype={'Code': str}, keep_default_na=False)
    except Exception as exc:
        raise ReportError(f'{Path(path).name}读取失败：{type(exc).__name__}') from exc


def stamp_quote_dates(frame, results):
    """Capture provider dates only; changing values invalidates their evidence."""
    frame = frame.copy()
    for col in ['Price_Date', 'Price_Date_Value', 'FX_Date', 'FX_Date_Value']:
        if col not in frame:
            frame[col] = ''
    for i, row in frame.iterrows():
        result = results.get(str(row['Code']), {})
        if result.get('status') != 'ok':
            continue
        if result.get('price') is not None and math.isclose(float(row['Current_Price']), float(result['price']), rel_tol=1e-10):
            frame.at[i, 'Price_Date'] = str(result.get('date') or '')[:10]
            frame.at[i, 'Price_Date_Value'] = str(row['Current_Price'])
        if result.get('fx_date') and result.get('fx_rate') and math.isclose(float(row['Exchange_Rate']), float(result['fx_rate']), rel_tol=1e-10):
            frame.at[i, 'FX_Date'] = str(result['fx_date'])[:10]
            frame.at[i, 'FX_Date_Value'] = str(row['Exchange_Rate'])
    return frame


def _csv_frame(root):
    frame = _read(root/'portfolio.csv')
    if frame.empty:
        raise ReportError('尚未建立账户')
    required = {'Date', 'Code', 'Name', 'Asset_Class', 'Currency', 'Shares',
                'Current_Price', 'Exchange_Rate', 'Total_Value', 'Net_Cash_Flow'}
    if required-set(frame):
        raise ReportError('缺少列：'+','.join(sorted(required-set(frame))))
    frame['Date'] = frame['Date'].map(lambda x: day(x).isoformat())
    # Weekend entry dates are accounting labels for the preceding Friday.
    frame['Date'] = frame['Date'].map(lambda x: week_friday(x).isoformat() if day(x).weekday()>4 else x)
    return frame


def _father_frame(root):
    events = read_events(root)
    if not events:
        raise ReportError('父亲账户尚未初始化')
    state = replay(events)
    active = [copy.deepcopy(e) for e in events if e['kind'] != 'VOID' and e['event_id'] not in state['voided']]
    selected = {}
    for e in active:
        if e['kind'] != 'SNAPSHOT':
            continue
        d = day(e['effective_at'])
        if d.weekday()<4:
            continue
        target = week_friday(d).isoformat()
        if d.weekday()>4:
            financial = [x for x in active if x['kind'] in {'TRADE','CASH','OPEN'}
                         and target < x['effective_at'][:10] <= d.isoformat()]
            # Weekend events are allowed for the same accounting week and will
            # be moved in the report view, never in the authoritative journal.
            if any(day(x['effective_at']).weekday()<5 for x in financial):
                raise ReportError(f'{target}周末快照混入后续工作日金融事件')
        previous = selected.get(target)
        if previous is None or e['effective_at']>previous['effective_at']:
            selected[target] = e
    chosen_ids = {e['event_id'] for e in selected.values()}
    active = [e for e in active if e['kind']!='SNAPSHOT' or day(e['effective_at']).weekday()<4 or e['event_id'] in chosen_ids]
    # Replay authoritative timestamps first. Moving a Saturday snapshot before
    # its Saturday manual quote would create an invalid journal in memory.
    try:
        frame = snapshot_portfolio(active)
    except ValueError as exc:
        raise ReportError('父亲快照及持仓无法按周重建：'+str(exc)) from exc
    quotes = {(e['effective_at'][:10], q['code']): q for e in active if e['kind']=='SNAPSHOT' for q in e['payload']['quotes']}
    for i, row in frame.iterrows():
        q = quotes.get((row.Date[:10], row.Code))
        if q:
            frame.at[i, 'Price_Date'] = q['as_of'][:10]
            # Legacy father quotes have one timestamp for the composite quote.
            frame.at[i, 'FX_Date'] = '' if row.Currency=='CNY' else q.get('fx_as_of', q['as_of'])[:10]
    frame['Date'] = frame.Date.map(lambda x: week_friday(x).isoformat() if day(x).weekday()>4 else x[:10])
    # Only reporting labels change; actual quote dates remain available for
    # cutoff checks and explicit price-bound date evidence.
    for e in active:
        if e['kind'] in {'TRADE','CASH'} and day(e['effective_at']).weekday()>4:
            e['effective_at'] = week_friday(e['effective_at']).isoformat()+'T23:59:58'
    return frame, active


def _evidence(root):
    path = root/'valuation_dates.csv'
    return _read(path) if path.exists() else pd.DataFrame()


def _text(value):
    return '' if value is None or pd.isna(value) else str(value)


def _bound_quote_dates(row):
    dates={field:_text(row.get(field,'')) for field in ['Price_Date','FX_Date']}
    for field, value in [('Price_Date',row.Current_Price),('FX_Date',row.Exchange_Rate)]:
        bound=_text(row.get(field+'_Value',''))
        if bound:
            try:
                matches=math.isclose(float(bound),float(value),rel_tol=1e-10)
            except (ValueError,TypeError):
                matches=False
            if not matches:
                dates[field]=''
    return dates


def date_rows(paths, start, end, today=None):
    """Read current valuation rows for optional explicit date confirmation."""
    bounds = period(start, end, today)
    rows = []
    for key, root in paths.items():
        try:
            frame = _father_frame(root)[0] if key=='father' else _csv_frame(root)
        except (ReportError, ValueError):
            continue
        evidence = _evidence(root)
        for _, r in frame.iterrows():
            if not bounds['baseline'] <= r.Date <= bounds['end'] or str(r.Code)=='CASH' or float(r.Shares)==0:
                continue
            dates=_bound_quote_dates(r)
            record = dict(Fund=key, Date=r.Date, Code=r.Code, Name=r.Name,
                          Price=float(r.Current_Price), FX=float(r.Exchange_Rate),
                          Price_Date=dates['Price_Date'], FX_Date=dates['FX_Date'],
                          Expected_Date=r.Date, Reason='')
            if not evidence.empty:
                matched = evidence[(evidence.Date==r.Date)&(evidence.Code==r.Code)]
                if not matched.empty:
                    saved=matched.iloc[-1]
                    if math.isclose(float(saved.Price), record['Price'], rel_tol=1e-10) and math.isclose(float(saved.FX), record['FX'], rel_tol=1e-10):
                        for field in ['Price_Date','FX_Date','Expected_Date','Reason']:
                            if str(saved.get(field,'') or ''):
                                record[field]=str(saved[field])
            rows.append(record)
    return pd.DataFrame(rows)


def save_date_rows(paths, records, expected_versions):
    if source_versions(paths)!=expected_versions:
        raise ReportError('账户数据已变化，请重新读取日期证据')
    pending = []
    for key, root in paths.items():
        rows=records[records.Fund==key].copy() if not records.empty else pd.DataFrame()
        if rows.empty:
            continue
        for _, r in rows.iterrows():
            expected=day(r.Expected_Date)
            friday=week_friday(r.Date)
            if not friday-timedelta(days=4)<=expected<=day(r.Date):
                raise ReportError('期望估值日必须在当周且不晚于快照日期')
            if r.Expected_Date!=r.Date and not str(r.Reason).strip():
                raise ReportError('调整期望估值日须填写休市或产品估值规则理由')
            for field in ['Price_Date','FX_Date']:
                if str(r[field]).strip() and day(r[field])>day(r.Date):
                    raise ReportError('报价或汇率日期不能晚于快照日期')
        path=root/'valuation_dates.csv'
        old=_evidence(root)
        if not old.empty:
            keys=set(zip(rows.Date,rows.Code))
            old=old[[ (r.Date,r.Code) not in keys for _,r in old.iterrows() ]]
            rows=pd.concat([old,rows],ignore_index=True)
        pending.append((path, rows.drop(columns=['Fund'],errors='ignore')))
    # Validate every account before writing any evidence. Each file retains
    # its own atomic-write/backup guarantees.
    if source_versions(paths)!=expected_versions:
        raise ReportError('账户数据已变化，请重新读取日期证据')
    for path, rows in pending:
        _atomic_write_csv(rows,str(path))


def _check_snapshot(frame, at, label, evidence, warnings, opening=False):
    rows=frame[frame.Date==at]
    if rows.empty:
        raise ReportError(f'{label}缺少{at}快照／净值，请先更新该期')
    if rows.Code.eq('').any() or rows.Code.duplicated().any():
        raise ReportError(f'{label} {at}代码为空或重复')
    if rows[rows.Asset_Class.eq('Cash')].shape[0]>1:
        raise ReportError(f'{label} {at}现金行重复')
    for _, r in rows.iterrows():
        prefix=f'{label} {at} {r.Name}（{r.Code}）'
        for field in ['Shares','Total_Value','Net_Cash_Flow']:
            try:
                val=float(r[field])
                if not math.isfinite(val) or (field!='Net_Cash_Flow' and val<0):
                    raise ValueError()
            except (ValueError,TypeError):
                raise ReportError(prefix+' '+field+'无效')
        if r.Asset_Class=='Cash':
            if abs(float(r.Shares)-float(r.Total_Value))>.02:
                warnings.append(prefix+'：历史现金份额与余额不同，按Total_Value核算')
            continue
        if float(r.Shares)==0:
            if abs(float(r.Total_Value))>.02:
                raise ReportError(prefix+'零持仓仍有市值')
            continue
        try:
            price,fx=float(r.Current_Price),float(r.Exchange_Rate)
            if not all(math.isfinite(x) and x>0 for x in [price,fx]):
                raise ValueError()
        except (ValueError,TypeError):
            raise ReportError(prefix+'价格／汇率缺失或无效')
        if abs(float(r.Shares)*price*fx-float(r.Total_Value))>max(.02,float(r.Total_Value)*1e-6):
            raise ReportError(prefix+'市值不等于份额×价格×汇率')
        if opening:
            continue
        expected=at
        dates=_bound_quote_dates(r)
        if not evidence.empty:
            matches=evidence[(evidence.Date==at)&(evidence.Code==r.Code)]
            if not matches.empty:
                saved=matches.iloc[-1]
                if math.isclose(float(saved.Price),price,rel_tol=1e-10) and math.isclose(float(saved.FX),fx,rel_tol=1e-10):
                    expected=str(saved.get('Expected_Date',at) or at)
                    if expected!=at:
                        if not str(saved.get('Reason','')).strip():
                            raise ReportError(prefix+'期望日调整缺少理由')
                        warnings.append(prefix+f'：用户确认期望估值日{expected}（{saved.Reason}）')
                    for field in dates:
                        if str(saved.get(field,'') or ''):
                            dates[field]=str(saved[field])
        if not week_friday(at)-timedelta(days=4)<=day(expected)<=day(at):
            raise ReportError(prefix+'期望日不在当周')
        for field in ['Price_Date']+(['FX_Date'] if r.Currency!='CNY' else []):
            observed=dates[field]
            if not observed:
                if str(r.get('Quote_Metadata_Version','')) in {'1','1.0'}:
                    raise ReportError(prefix+'新快照缺少'+field+'，请在报表页补充已核实日期')
                warnings.append(prefix+('：报价日期未验证' if field=='Price_Date' else '：汇率日期未验证'))
            elif day(observed)<day(expected):
                raise ReportError(prefix+f'报价／汇率过期：{observed}，期望{expected}；若为QDII公布滞后或休市，请在报表页明确实际估值日与理由，不能直接改成周五')
            elif day(observed)>day(at):
                raise ReportError(prefix+f'报价／汇率晚于截止日：{observed}；周末录入的周五价格请补充已核实日期，不能使用截止日之后的实际行情')


def _transactions(root, key, bounds, warnings, events=None):
    if key=='father':
        rows=[]
        for e in events:
            if e['kind'] not in {'TRADE','CASH'}:
                continue
            at=e['effective_at'][:10]
            if not bounds['baseline']<at<=bounds['end']:
                continue
            p=e['payload']
            rows.append(dict(Date=week_friday(at).isoformat(),Name=p.get('name','现金'),
                             Code=p.get('code','CASH'),Type=p.get('side',p.get('action','')),
                             Amount_CNY=float(p.get('cash_cny',p.get('amount',0)))))
        return rows
    path=root/'transaction.csv'
    if not path.exists() or path.stat().st_size==0:
        warnings.append(LABELS[key]+'：逐笔流水缺失，贡献按快照NCF计算')
        return []
    tx=_read(path)
    if tx.empty:
        warnings.append(LABELS[key]+'：历史逐笔流水为空，不代表没有交易')
        return []
    if not {'Date','Type','Amount_CNY','Name','Code'}<=set(tx):
        raise ReportError(LABELS[key]+'交易表缺少必要字段')
    chosen=[]
    for _, r in tx.iterrows():
        at=day(r.Date)
        accounting=week_friday(at).isoformat() if at.weekday()>4 else at.isoformat()
        if not bounds['baseline']<accounting<=bounds['end']:
            continue
        if 'Trade_Date' in tx and str(r.Trade_Date):
            booking=day(r.Trade_Date)
            if week_friday(booking)!=week_friday(accounting):
                raise ReportError(f'{LABELS[key]} {r.Name}流水跨周：{r.Trade_Date}归属{accounting}')
            if booking.weekday()>4:
                warnings.append(f'{LABELS[key]} {r.Name}：周末操作{r.Trade_Date}按{week_friday(booking)}记账')
        else:
            warnings.append(LABELS[key]+'：流水仅有快照归属日，未验证逐笔实际日期')
        if not math.isfinite(float(r.Amount_CNY)) or float(r.Amount_CNY)<0:
            raise ReportError(LABELS[key]+'流水金额无效')
        chosen.append(r.to_dict())
    return chosen


def _calculate(key, root, bounds):
    warnings=[]
    if key=='father':
        frame,events=_father_frame(root)
    else:
        frame,events=_csv_frame(root),None
    label=LABELS[key]
    first=min(frame.Date)
    if first>bounds['end']:
        raise ReportError(label+'在期末尚未成立')
    inception=first>bounds['baseline']
    baseline=first if inception else bounds['baseline']
    marks=[baseline]+[w for w in bounds['weeks'] if w>baseline]
    if bounds['end'] not in marks:
        marks.append(bounds['end'])
    evidence=_evidence(root)
    for at in dict.fromkeys(marks):
        _check_snapshot(frame,at,label,evidence,warnings,opening=inception and at==baseline)
    within=frame[(frame.Date>=baseline)&(frame.Date<=bounds['end'])].copy()
    for field in ['Shares','Current_Price','Exchange_Rate','Total_Value','Net_Cash_Flow']:
        within[field]=pd.to_numeric(within[field],errors='coerce')
    # Validate all intermediate checkpoints too, without treating their dates
    # as completed weekly snapshots.
    for at in sorted(set(within.Date)-set(marks)):
        _check_snapshot(frame,at,label,evidence,warnings,opening=False)
    start=within[within.Date==baseline];end=within[within.Date==bounds['end']]
    if float(start.Total_Value.sum())<=0:
        raise ReportError(label+'期初总资产须大于零')
    nav=compute_fund_nav(within)
    nav_values=within.groupby('Date').Total_Value.sum().sort_index()
    flows=within[within.Asset_Class.isin(['Cash','Company_Stock'])].groupby('Date').Net_Cash_Flow.sum()
    factor=1.
    previous=float(nav_values.iloc[0])
    for at, total in nav_values.iloc[1:].items():
        flow=float(flows.get(at,0))
        if previous<=0:
            raise ReportError(label+'期初资产为零，无法计算收益率')
        if float(total)-flow<0:
            raise ReportError(label+'外部流量与资产不一致，剔除入金后资产为负')
        factor *= (float(total)-flow)/previous
        previous=float(total)
    external=float(flows[flows.index>baseline].sum())
    grouped_start=start.groupby('Code').Total_Value.sum()
    grouped_end=end.groupby('Code').Total_Value.sum()
    ncf=within[within.Date>baseline].groupby('Code').Net_Cash_Flow.sum()
    contributions=[]
    for code in sorted(set(grouped_start.index)|set(grouped_end.index)|set(ncf.index)):
        rows=within[within.Code==code];r=rows.iloc[-1]
        if r.Asset_Class=='Cash':
            continue
        contributions.append(dict(code=code,name=r.Name,asset_class=r.Asset_Class,
                                  value=float(grouped_end.get(code,0)),
                                  pnl=float(grouped_end.get(code,0)-grouped_start.get(code,0)-ncf.get(code,0))))
    initial,total=float(start.Total_Value.sum()),float(end.Total_Value.sum())
    allocation=[dict(asset_class=cls, value=float(value),
                     weight_pct=float(value)/total*100 if total else 0.)
                for cls,value in end.groupby('Asset_Class').Total_Value.sum().sort_values(ascending=False).items()]
    if inception:
        warnings.append(label+f'：本区间使用{baseline}成立／接手期初，不与其他基金强行对齐')
    return dict(key=key,label=label,baseline=baseline,end=bounds['end'],inception=inception,
                initial=initial,total=total,external=external,pnl=total-initial-external,
                return_pct=(factor-1)*100,nav_start=1.,nav_end=factor,
                cash=float(end.loc[end.Asset_Class=='Cash','Total_Value'].sum()),
                contributions=contributions,allocation=allocation,transactions=_transactions(root,key,bounds,warnings,events),
                checkpoints=nav[['Date','Total_Value','Net_Cash_Flow','NAV']].to_dict('records'),
                warnings=sorted(set(warnings)))


def build_report(data_dir, start, end, son_dir=None, father_dir=None, today=None):
    paths=account_paths(data_dir,son_dir,father_dir)
    versions=source_versions(paths)
    bounds=period(start,end,today)
    funds=[];issues=[]
    for key,root in paths.items():
        try:
            funds.append(_calculate(key,root,bounds))
        except (ReportError,ValueError,KeyError,OSError) as exc:
            issues.append(LABELS[key]+'：'+str(exc))
    if issues:
        raise ReportError(issues)
    if source_versions(paths)!=versions:
        raise ReportError('计算期间数据已变化，请重新预览')
    result=dict(bounds=bounds,funds=funds,source_versions=versions)
    result['markdown']=render_markdown(result)
    return result


def _clean(value):
    return str(value).replace('|','／').replace('\n',' ')


def render_markdown(result):
    b=result['bounds'];funds=result['funds']
    start=(day(b['start'])-timedelta(days=4)).isoformat()
    lines=[f'# 三基金区间汇总｜{start}至{b["end"]}', '',
           '> 按周五记账；周末操作可归刚结束的一周。金额均为人民币。只读取保存的账本，不新增交易。', '',
           '## 总览', '', '| 基金 | 实际期初 | 期初资产 | 期末资产 | 外部净入金 | 投资损益 | 区间收益率 |',
           '|---|---|---:|---:|---:|---:|---:|']
    for f in funds:
        lines.append(f'| {f["label"]} | {f["baseline"]} | {f["initial"]:,.2f} | {f["total"]:,.2f} | {f["external"]:,.2f} | {f["pnl"]:+,.2f} | {f["return_pct"]:+.2f}% |')
    lines += ['',f'合计期末资产：{sum(f["total"] for f in funds):,.2f}元；合计投资损益：{sum(f["pnl"] for f in funds):+,.2f}元。',
              '损益＝期末资产−期初资产−外部净入金；收益率按全部区间快照剔除现金流后链式计算。成立日不同的基金标注各自实际期初，不输出合并净值或合并收益率。',
              '区间净值以各自期初归一为1；不将展示净值四舍五入后再相除。周快照不能证明周内最大回撤。','']
    for f in funds:
        lines += [f'## {f["label"]}', '',f'现金余额：{f["cash"]:,.2f}元；区间归一净值：1.0000 → {f["nav_end"]:.4f}。','',
                  '| 标的 | 期末市值 | 损益贡献 |','|---|---:|---:|']
        for c in sorted(f['contributions'],key=lambda c:abs(c['pnl']),reverse=True):
            lines.append(f'| {_clean(c["name"])}（{_clean(c["code"])}） | {c["value"]:,.2f} | {c["pnl"]:+,.2f} |')
        lines += ['', '贡献＝标的市值变化−区间标的NCF，含清仓损益及已入表分红；SAP归属成本按外部流量处理。贡献与总损益的微小差异可能来自原账本的费用和取整口径。','',
                  '| 期末资产类别 | 市值 | 占总资产 |','|---|---:|---:|']
        for a in f['allocation']:
            lines.append(f'| {_clean(CLASS_DISPLAY_NAMES.get(a["asset_class"],a["asset_class"]))} | {a["value"]:,.2f} | {a["weight_pct"]:.2f}% |')
        lines += ['', '| 周快照／期初 | 总资产 | 外部流量 |','|---|---:|---:|']
        for r in f['checkpoints']:
            lines.append(f'| {r["Date"]} | {r["Total_Value"]:,.2f} | {r["Net_Cash_Flow"]:,.2f} |')
        lines += ['',f'区间有{len(f["transactions"])}条已记录流水。','', '| 操作 | 金额合计 | 笔数 |','|---|---:|---:|']
        for kind in sorted(set(str(t['Type']) for t in f['transactions'])):
            tx=[t for t in f['transactions'] if str(t['Type'])==kind]
            lines.append(f'| {_clean(kind)} | {sum(float(t["Amount_CNY"]) for t in tx):,.2f} | {len(tx)} |')
        lines += ['', '### 日期校验与口径提示','']
        lines += ['- '+_clean(w) for w in f['warnings']] or ['- 指定估值日的价格和必要汇率日期已验证。']
        lines.append('')
    lines += ['## 来源与边界', '',
              '- 主基金与儿子基金：各自portfolio.csv、transaction.csv、可选valuation_dates.csv。',
              '- 父亲基金：ledger.csv派生持仓；周末保存的快照只在账簿、报价及周归属验证后使用。',
              '- 非交易日的期望估值日期按用户明确确认记录；不声称外部交易日历已自动核实。',
              '- 本报告不改变投资决策，不追溯父亲接手前历史盈亏，也不将独立账户合并所有权。','']
    return '\n'.join(lines)


def _atomic_bytes(path, content):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.report_',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(content);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _index(root):
    reports=sorted(root.glob('三基金*.md'),reverse=True)
    text='# 三基金汇总报表\n\n'+ '\n'.join(f'- [{p.stem}]({p.name})' for p in reports)+'\n'
    _atomic_bytes(root/'README.md',text.encode())


def sync_saved_report(data_dir, filename, vault_path):
    if Path(filename).name!=filename or not filename.startswith('三基金') or not filename.endswith('.md'):
        raise ReportError('报告文件名无效')
    source=Path(data_dir)/'weekly_summaries'/filename
    if not vault_path or not Path(vault_path).is_dir():
        return dict(synced=False,error='Obsidian vault不存在或未挂载',filename=filename)
    destination=Path(vault_path)/'familyfund/weekly_summaries'/filename
    try:
        content=source.read_bytes()
        if destination.exists() and destination.read_bytes()!=content:
            raise OSError('Obsidian同名报告有独立修改，拒绝覆盖')
        _atomic_bytes(destination,content);_index(destination.parent)
        return dict(synced=True,filename=filename,obsidian_path=str(destination))
    except OSError as exc:
        return dict(synced=False,filename=filename,error='Obsidian同步失败：'+str(exc))


def save_report(result, data_dir, vault_path, son_dir=None, father_dir=None):
    paths=account_paths(data_dir,son_dir,father_dir)
    if source_versions(paths)!=result['source_versions']:
        raise ReportError('预览后账户数据已变化，请重新预览')
    fresh=build_report(data_dir,result['bounds']['start'],result['bounds']['end'],son_dir,father_dir)
    if fresh['source_versions']!=result['source_versions'] or fresh['markdown']!=result['markdown']:
        raise ReportError('预览已过期，请重新预览')
    root=Path(data_dir)/'weekly_summaries';root.mkdir(parents=True,exist_ok=True)
    b=result['bounds'];filename=f'三基金汇总_{b["start"]}_{b["end"]}_{datetime.now(ZoneInfo("Asia/Shanghai")):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.md'
    with (root/'.report.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        _atomic_bytes(root/filename,result['markdown'].encode())
        _atomic_bytes(root/(filename[:-3]+'.json'),json.dumps(result,ensure_ascii=False,indent=2).encode())
        _index(root)
    status=sync_saved_report(data_dir,filename,vault_path)
    status['data_path']=str(root/filename)
    return status
