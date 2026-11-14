"""Father Fund: CSV drafts, quote fallback and atomic snapshot saving."""
from decimal import Decimal
from datetime import timedelta
from pathlib import Path
import hashlib
import os
import uuid

import pandas as pd
import streamlit as st
from father_fund import (BUCKETS, CURRENCIES, CASH_TYPES, PLAN_STATUSES, LedgerError,
                         read_events, replay, event, commit_events, cancel_event,
                         holdings_frame, parse_input, parse_reference, reconcile, now_beijing, number)
from father_workflow import (canonical_code, load_registry, save_instrument, find_instrument, candidate, TYPES,
                             verify_instrument, fetch_quote, parse_trades, trade_fingerprint, duplicate_counts,
                             build_trade_batch, save_snapshot, performance)


def _token(key):
    if key not in st.session_state:
        st.session_state[key]=str(uuid.uuid4())
    return st.session_state[key]


def _saved(message, token=None):
    if token:
        st.session_state.pop(token,None)
    st.session_state['ff_message']=message
    st.rerun()


def _reference(text):
    try:
        ref=parse_reference(text)
    except (LedgerError,KeyError) as exc:
        st.warning(f'最新参考表尚不完整：{exc}')
        return
    if not ref:
        return
    p=ref['payload'];total=float(p['total']);market=float(p['market']);available=float(p['available'])
    st.caption(f"用户提供的券商参考 · {ref['date']} · 尚未作为期初入账")
    cols=st.columns(4)
    cols[0].metric('参考总资产',f'¥{total:,.2f}')
    cols[1].metric('参考证券市值',f'¥{market:,.2f}')
    cols[2].metric('券商可用资金',f'¥{available:,.2f}')
    cols[3].metric('20%—30%权益额度',f'{total*.2/10000:.2f}—{total*.3/10000:.2f}万')
    gap=market+available-total
    if abs(gap)>.01:
        st.warning(f'市值＋可用资金比总资产相差 ¥{gap:,.2f}；保留差异，不自动调平现金。')
    if ref['rows']:
        st.dataframe(pd.DataFrame(ref['rows']),hide_index=True,use_container_width=True)


def _initialization(data_dir, text, input_path):
    _reference(text)
    st.subheader('建立期初并导入历史成交')
    st.caption('先填期初基准，再填基准之后的成交。预览不写入；确认后一次保存，之后逐笔登记即可。')
    st.write(f'可填写的 Markdown 文件：{input_path}')
    st.download_button('下载填写表格',text,file_name='_father_input.md',mime='text/markdown',key='ff_download_input')
    if st.button('重新读取填写文件',key='ff_reload_input'):
        st.session_state['ff_input_text']=text
        st.session_state.pop('ff_preview_batch',None)
    entered=st.text_area('粘贴填写后的 Markdown（也可以直接修改文件后刷新页面）',value=text,height=340,key='ff_input_text')
    signature=hashlib.sha256(entered.encode()).hexdigest()
    if st.button('预览期初与交易后的持仓',key='ff_preview'):
        try:
            events=parse_input(entered)
            ref=parse_reference(entered)
            if ref and ref['effective_at']:
                # Quotes are imported only if the user supplied both price and confirmed FX.
                quotes=[]
                for row in ref['rows']:
                    if row.get('最新现价') and row.get('已确认折算汇率'):
                        quotes.append(dict(code=row['代码'],price=row['最新现价'],fx=row['已确认折算汇率'],as_of=ref['effective_at']))
                if quotes:
                    events.append(event('QUOTE',ref['effective_at'],dict(quotes=quotes)))
                events.append(event('BROKER',ref['effective_at'],ref['payload']))
            state=replay(events)
            st.session_state['ff_confirm_init']=False
            st.session_state['ff_preview_batch']=(signature,events)
        except (LedgerError,KeyError) as exc:
            st.session_state.pop('ff_preview_batch',None)
            st.error(f'表格尚未填完整或有冲突：{exc}')
    pending=st.session_state.get('ff_preview_batch')
    if pending and pending[0]==signature:
        state=replay(pending[1])
        st.dataframe(holdings_frame(state),hide_index=True,use_container_width=True)
        st.write(f"推算净现金 ¥{state['cash']:,.2f}；总资产 ¥{state['total']:,.2f}；成交 {len(state['trades'])} 笔。")
        if state['broker']:
            _show_reconciliation(pending[1],state['broker'])
        confirm=st.checkbox('确认期初与成交范围正确，知道对账差异尚需解释',key='ff_confirm_init')
        if st.button('建立父亲账户',disabled=not confirm,key='ff_initialize',type='primary'):
            try:
                commit_events(data_dir,pending[1])
                st.session_state.pop('ff_preview_batch',None)
                _saved('父亲账户已建立，交易与持仓已保存。')
            except LedgerError as exc:
                st.error(str(exc))


def _show_reconciliation(events, broker):
    r=reconcile(events,broker)
    st.caption(f"按券商快照时点对账：{r['at']}；后续交易不会被拿来与旧快照误比。")
    rows=[{'项目':label,'账簿减券商（元）':float(r[k])} for label,k in [('总资产','total_difference'),('证券市值','market_difference'),('净现金（总资产减市值）','net_cash_difference')]]
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
    st.write(f"券商可用资金与其净现金口径之差：¥{r['available_gap']:,.2f}")
    if r['holdings']:
        st.warning('持仓数量未对齐，请查缺失或重复交易。')
        st.dataframe(pd.DataFrame(r['holdings']),hide_index=True,use_container_width=True)
    else:
        st.caption('已提供完整持仓参考时，数量对账无差异；金额差异还可能来自报价时点和汇率。')


def _trade_form(data_dir,state,events):
    active=[e for e in events if e['kind']=='TRADE' and e['event_id'] not in state['voided']]
    correction=st.checkbox('纠正一笔已登记成交（保留原记录）',key='ff_correct_mode')
    old=None
    if correction:
        if not active:
            st.info('暂无可纠正成交。');return
        target=st.selectbox('原成交',active,format_func=lambda e:f"{e['effective_at']} · {e['payload']['name']}（{e['payload']['code']}）· {e['payload']['side']} · {e['payload']['reference']}",key='ff_correct_select')
        old=target
        default=target['payload']
    else:
        choices=list(state['positions'])
        if not choices:
            st.info('新标的请使用交易CSV录入。'); return
        chosen=st.selectbox('标的',choices,format_func=lambda c:c if c=='新标的' else f"{state['positions'][c]['name']}（{c}）",key='ff_trade_instrument')
        default={} if chosen=='新标的' else state['positions'][chosen]
    scope='ff_trade_'+(old['event_id'] if old else str(default.get('code','new')))
    with st.form(scope):
        c1,c2,c3=st.columns(3)
        name=c1.text_input('标的名称',value=default.get('name',''))
        code=c2.text_input('代码',value=default.get('code',''))
        bucket=c3.selectbox('资产类别',BUCKETS,index=BUCKETS.index(default.get('bucket','权益')))
        venue=st.selectbox('交易场所',['场内','场外'],index=0 if default.get('venue','场内')=='场内' else 1,help='场内股票/ETF获取市场报价；场外公募基金获取基金净值。')
        c1,c2,c3=st.columns(3)
        side=c1.selectbox('方向',['买入','卖出'],index=0 if default.get('side','买入')=='买入' else 1)
        currency=c2.selectbox('报价币种',CURRENCIES,index=CURRENCIES.index(default.get('currency','CNY')))
        fx=c3.number_input('人民币／报价币折算汇率',min_value=.00000001,value=float(default.get('fx',1)),format='%.8f')
        at=st.text_input('成交时间（北京时间）',value=old['effective_at'] if old else now_beijing().strftime('%Y-%m-%d %H:%M:%S'))
        c1,c2,c3=st.columns(3)
        qty=c1.number_input('成交股数／份额',min_value=.000001,value=float(default.get('quantity',100)) if old else 100.0,format='%.6f')
        price=c2.number_input('原币成交价',min_value=.000001,value=float(default.get('price',1)),format='%.6f')
        fee=c3.number_input('费用合计（人民币，已含在实际收付中）',min_value=0.0,value=float(default.get('fees_cny',0)),format='%.2f')
        cash=st.number_input('人民币实际收付金额（买入含费扣款／卖出净到账）',min_value=.01,value=float(default['cash_cny']) if old else None,format='%.2f')
        st.caption('以券商实际结算为准，均填正数。费用只留作核对，不会再次扣款。公募基金以确认份额和实际扣款登记；未确认申购不记作成交。')
        reference=st.text_input('成交编号（券商编号或唯一自编号）',value=default.get('reference','手工-'+_token(scope+'_token')[:8]))
        note=st.text_input('原因／备注',value=default.get('note',''))
        reason=st.text_input('纠错原因（纠错时必填）') if old else ''
        submitted=st.form_submit_button('保存纠正后的成交' if old else '登记成交并更新持仓',type='primary')
    if submitted:
        try:
            payload=dict(name=name,code=code,bucket=bucket,venue=venue,currency=currency,fx=str(fx),side=side,quantity=str(qty),price=str(price),fees_cny=str(fee),cash_cny=str(cash),reference=reference,note=note)
            new=event('TRADE',at,payload,event_id=_token(scope+'_token'))
            additions=([event('VOID',now_beijing().isoformat(),dict(target=old['event_id'],reason=reason))] if old else [])+[new]
            _stage(data_dir,additions)
            _saved('成交已加入本期草稿，请最终保存快照。',scope+'_token')
        except LedgerError as exc:
            st.error(str(exc))


def _cash_form(data_dir):
    with st.form('ff_cash_form'):
        action=st.selectbox('资金类型',CASH_TYPES)
        amount=st.number_input('人民币金额（正数）',min_value=.01,value=1.0,format='%.2f')
        at=st.text_input('资金发生时间',value=now_beijing().strftime('%Y-%m-%d %H:%M:%S'))
        note=st.text_input('资金说明')
        submitted=st.form_submit_button('保存资金变动')
    if submitted:
        try:
            _stage(data_dir,[event('CASH',at,dict(action=action,amount=str(amount),note=note),_token('ff_cash_token'))])
            _saved('资金变动已加入本期草稿，请最终保存快照。','ff_cash_token')
        except LedgerError as exc:st.error(str(exc))


def _stage(data_dir, additions):
    current=read_events(data_dir)
    if [e['event_id'] for e in current]!=st.session_state['ff_base_ids']:
        raise LedgerError('账户流水已更新，请重置草稿后重新核对')
    pending=st.session_state.get('ff_pending',[])
    if set(e['event_id'] for e in additions)&set(e['event_id'] for e in pending):
        raise LedgerError('这些记录已经应用到本期草稿')
    replay(current+pending+additions)
    st.session_state['ff_pending']=pending+additions
    st.session_state['ff_grid_version']=st.session_state.get('ff_grid_version',0)+1
    st.session_state['ff_reset_confirm']=True


def _resolve_instruments(data_dir, parent_dir, state, rows):
    registry=load_registry(data_dir); metadata={}; seen=set()
    for row in rows:
        key=canonical_code(row['code'])
        if key in seen: continue
        seen.add(key)
        found=find_instrument(row['code'],state,registry,parent_dir)
        if found and found.get('validation_status')=='confirmed':
            metadata[key]=found; continue
        stored=registry.get(key,{})
        default=found or stored or candidate(row['code'])
        title=f"{default.get('name') or '新标的'}（{row['code']}）"
        with st.container(border=True):
            st.write(f'确认标的信息：{title}')
            if found:
                st.caption(f"主基金参考：{found['name']} · {found['currency']} · {found['type']} · 行情代码 {found['quote_code']}")
                if st.button('确认沿用主基金标的信息',key=f'ff_accept_parent_{key}'):
                    found.update(validation_status='confirmed',verified_by='用户确认主基金持仓参考')
                    save_instrument(data_dir,found)
                    st.rerun()
            with st.form(f'ff_new_{key}'):
                name=st.text_input('标的名称',value=default.get('name',''))
                code=st.text_input('账户代码',value=default.get('code',row['code']))
                kind=st.selectbox('标的类型',TYPES,index=TYPES.index(default.get('type','股票')))
                currency=st.selectbox('币种',CURRENCIES,index=CURRENCIES.index(default.get('currency','CNY')))
                bucket=st.selectbox('资产类别',BUCKETS,index=BUCKETS.index(default.get('bucket','权益')))
                symbol=st.text_input('行情代码（场内含交易所后缀）',value=default.get('quote_code',''))
                st.caption('股票/场内ETF使用yfinance，场外基金使用天天基金。代码、名称与API返回结果须对应。')
                submitted=st.form_submit_button('查询标的并拉取一次价格')
            if submitted:
                if canonical_code(code)!=key:
                    st.error('账户代码与CSV不一致，请先修正CSV。')
                elif not name.strip():
                    st.error('请填写标的名称。')
                else:
                    meta=candidate(code,name,currency,bucket,kind,quote_code=symbol.strip())
                    verified,quote=verify_instrument(meta)
                    st.session_state[f'ff_verify_{key}']=(verified,quote)
                    draft=dict(verified,validation_status='pending')
                    save_instrument(data_dir,draft)
            verification=st.session_state.get(f'ff_verify_{key}')
            if verification:
                verified,quote=verification
                if verified['validation_status']!='confirmed':
                    st.warning(verified.get('validation_error','代码或标的身份暂未验证；草稿已保存。'))
                    continue
                st.write(f"API返回：{verified['api_name']} · {verified['quote_code']} · {verified['currency']}")
                ok=quote.get('status')=='ok'
                if ok:
                    price=float(quote['price'])
                    st.caption(f"价格 {price:.6f}；行情日期 {quote.get('date','未知')}")
                else:
                    st.warning('标的身份已验证，价格获取失败，请手动填写。')
                    price=st.number_input('新标的手动价格／净值',min_value=0.,value=0.,format='%.6f',key=f'ff_new_price_{key}')
                rate=1. if verified['currency']=='CNY' else st.number_input('新标的人民币折算汇率',min_value=.00000001,value=float(quote.get('fx_rate') or 1),format='%.8f',key=f'ff_new_fx_{key}')
                confirm=st.checkbox('确认API返回的是目标标的',key=f'ff_new_confirm_{key}')
                if st.button('确认并保存标的信息',disabled=not confirm or price<=0,key=f'ff_register_{key}'):
                    save_instrument(data_dir,verified)
                    st.session_state.setdefault('ff_values',{})[verified['code']]=dict(price=price,fx=rate,date=quote.get('date','') if ok else now_beijing().date().isoformat(),source='自动' if ok else '手动')
                    st.session_state['ff_reset_confirm']=True
                    st.session_state.pop(f'ff_verify_{key}',None)
                    st.success('标的信息已保存；交易仍需应用并保存快照。')
                    st.rerun()
    return metadata


def _csv_input(data_dir,parent_dir,events,state):
    text=st.text_area('买入／卖出CSV（每行一笔）',height=140,key='ff_trade_csv',
                      placeholder='日期,买入/卖出,代码,股数或份额,原币成交价,人民币收付金额')
    st.caption('人民币收付均填正数：买入含费扣款，卖出净到账。解析、应用均不落盘，最后保存快照。')
    if st.button('解析交易CSV',key='ff_parse_csv'):
        try:
            rows=parse_trades(text)
            st.session_state['ff_csv_distinct']=False
            st.session_state['ff_csv_rows']=rows
            st.session_state['ff_csv_text']=text
            st.session_state['ff_csv_batch']=str(uuid.uuid4())
        except LedgerError as exc:
            st.error(str(exc))
            st.session_state.pop('ff_csv_rows',None)
    rows=st.session_state.get('ff_csv_rows')
    if not rows or text!=st.session_state.get('ff_csv_text'): return
    try:
        metadata=_resolve_instruments(data_dir,parent_dir,state,rows)
    except (LedgerError,OSError):
        st.error('标的信息读取或保存失败，请核对文件后重试；交易尚未保存。')
        return
    if len(metadata)!=len(set(canonical_code(r['code']) for r in rows)):
        st.info('请先确认所有新标的信息，再应用交易。'); return
    batch=build_trade_batch(rows,metadata,st.session_state['ff_csv_batch'])
    st.dataframe(pd.DataFrame([dict(交易日期=e['effective_at'][:10],方向=e['payload']['side'],
                                  标的=f"{e['payload']['name']}（{e['payload']['code']}）",数量=e['payload']['quantity'],
                                  成交价=e['payload']['price'],人民币收付=e['payload']['cash_cny']) for e in batch]),hide_index=True,use_container_width=True)
    signature=hashlib.sha256('|'.join(trade_fingerprint(r) for r in rows).encode()).hexdigest()
    already=st.session_state['ff_csv_batch'] in st.session_state.get('ff_applied_csv',set())
    count=duplicate_counts(rows,events+st.session_state.get('ff_pending',[]))
    distinct=False
    if count:
        st.warning(f'有{count}笔内容与已保存或待保存成交相同。相同文本可能是真实的多笔成交，请核对。')
        distinct=st.checkbox('这些是另外发生的真实成交，确认新增',key='ff_csv_distinct')
    if already: st.info('本批CSV已应用，不能再次应用；新增相同真实成交请重新解析，并确认重复提示。')
    valid=True
    if not already:
        try:
            preview=replay(events+st.session_state.get('ff_pending',[])+batch)
            st.caption('应用后的持仓预览（尚未保存）')
            st.dataframe(holdings_frame(preview),hide_index=True,use_container_width=True)
            st.write(f"应用后现金 ¥{preview['cash']:,.2f}；变化 ¥{preview['cash']-state['cash']:+,.2f}。市值暂按成交价估算，应用后再刷新。")
        except LedgerError as exc:
            valid=False
            st.error(str(exc))
    if st.button('应用交易到本期持仓',disabled=already or not valid or bool(count and not distinct),key='ff_apply_csv',type='primary'):
        try:
            _stage(data_dir,batch)
            st.session_state.setdefault('ff_applied_csv',set()).add(st.session_state['ff_csv_batch'])
            _saved(f'已应用{len(batch)}笔交易，尚未保存。')
        except LedgerError as exc: st.error(str(exc))


def _valuation(data_dir,parent_dir,events,state):
    st.subheader('确认持仓数据／刷新价格')
    snapshot_date=st.date_input('快照日期',value=now_beijing().date(),max_value=now_beijing().date(),key='ff_snapshot_date')
    at=str(snapshot_date)+'T23:59:59'
    quote_default=snapshot_date-timedelta(days=max(0,snapshot_date.weekday()-4))
    if st.session_state.get('ff_manual_quote_date',quote_default)>snapshot_date:
        st.session_state['ff_manual_quote_date']=quote_default
    manual_quote_date=st.date_input('手动报价实际日期',value=quote_default,
        max_value=snapshot_date,key='ff_manual_quote_date',
        help='填写实际收盘价／净值日期。周末补录默认前周五，录入日期与报价日期分开。')
    frame=holdings_frame(state)
    values=st.session_state.setdefault('ff_values',{})
    if not frame.empty:
        table=frame[['名称','代码','币种','股数/份额','价格','折算汇率','报价时间']].copy()
        table['来源']='已保存／成交估值'
        for i,r in table.iterrows():
            value=values.get(r['代码'])
            if value:
                table.at[i,'价格']=value['price']; table.at[i,'折算汇率']=value['fx']
                table.at[i,'报价时间']=value.get('date','')
                table.at[i,'来源']=value['source']
        edited=st.data_editor(table,disabled=['名称','代码','币种','股数/份额','报价时间','来源'],
                              hide_index=True,use_container_width=True,key=f'ff_quote_grid_{st.session_state.get("ff_grid_version",0)}')
        for _,r in edited.iterrows():
            try:
                number(r['价格'],'价格','0.00000001')
                number(r['折算汇率'],'汇率','0.00000001')
            except LedgerError:
                st.error('价格和汇率须为有效正数，请补齐估值表。')
                return
            code=r['代码']; previous=values.get(code)
            changed=not previous and (float(r['价格'])!=float(state['positions'][code]['price']) or float(r['折算汇率'])!=float(state['positions'][code]['fx']))
            changed=changed or bool(previous and (float(r['价格'])!=previous['price'] or float(r['折算汇率'])!=previous['fx']))
            if changed:
                values[code]=dict(price=float(r['价格']),fx=float(r['折算汇率']),date=str(manual_quote_date),source='手动')
                st.session_state['ff_confirm_snapshot']=False
        if st.button('刷新价格／净值（拉取一次）',key='ff_refresh'):
            registry=load_registry(data_dir); results={}
            for code,p in state['positions'].items():
                if p['quantity']<=0: continue
                try:
                    meta=find_instrument(code,state,registry,parent_dir)
                    result=fetch_quote(meta)
                except Exception:
                    result=dict(status='error',price=None,msg='无法获取，请核对映射或手动填写')
                results[code]=result
                if result.get('status')=='ok':
                    rate=result.get('fx_rate') or float(p['fx'])
                    values[code]=dict(price=float(result['price']),fx=float(rate),date=result.get('date',''),source='自动')
            st.session_state['ff_refresh_results']=results
            st.session_state['ff_reset_confirm']=True
            st.session_state['ff_grid_version']=st.session_state.get('ff_grid_version',0)+1
            st.rerun()
        results=st.session_state.get('ff_refresh_results',{})
        if results:
            ok=sum(r.get('status')=='ok' for r in results.values())
            manual=sum(r.get('status')!='ok' and values.get(c,{}).get('source')=='手动' and values[c].get('date')==str(quote_default) for c,r in results.items())
            st.caption(f'自动刷新{ok}个；手动确认{manual}个；{len(results)-ok-manual}个需补充。失败不覆盖原值。')
            for code,result in results.items():
                if code not in state['positions'] or state['positions'][code]['quantity']<=0: continue
                if result.get('status')=='ok': continue
                p=state['positions'][code]
                with st.container(border=True):
                    st.write(f"{p['name']}（{code}）：自动获取失败／需手动填写")
                    current=values.get(code,{})
                    price=st.number_input('手动价格／净值',min_value=0.,value=float(current.get('price',0)),format='%.6f',key=f'ff_manual_price_{code}')
                    rate=1. if p['currency']=='CNY' else st.number_input('手动人民币折算汇率',min_value=.00000001,value=float(current.get('fx',p['fx'])),format='%.8f',key=f'ff_manual_fx_{code}')
                    if st.button('确认手动价格',key=f'ff_manual_confirm_{code}') and price>0:
                        values[code]=dict(price=price,fx=rate,date=str(manual_quote_date),source='手动')
                        st.session_state['ff_reset_confirm']=True
                        st.session_state['ff_grid_version']=st.session_state.get('ff_grid_version',0)+1
                        st.rerun()
    else:
        st.caption('当前无非零证券持仓，将保存现金快照。')
    quotes=[]; stale=[]
    for code,p in state['positions'].items():
        if p['quantity']<=0: continue
        value=values.get(code,{})
        price=value.get('price',float(p['price'])); rate=value.get('fx',float(p['fx']))
        raw_date=value.get('date') or p['quoted_at']
        date=str(raw_date)[:10]
        quote_at=date+'T23:59:59' if value else p['quoted_at']
        if date<str(quote_default) or not value or not value.get('date'): stale.append(p['name'])
        quotes.append(dict(code=code,price=str(price),fx=str(rate),as_of=quote_at,source=value.get('source','已保存／成交估值')))
    if stale: st.warning('以下标的使用旧报价／成交估值，请手动确认或接受保留：'+'、'.join(stale))
    try:
        # Validation and computation do not persist the draft.
        snapshot=event('SNAPSHOT',at,dict(quotes=quotes))
        preview=replay(events+st.session_state.get('ff_pending',[])+[snapshot])
        st.dataframe(holdings_frame(preview),hide_index=True,use_container_width=True)
        st.write(f"本期待保存：证券市值 ¥{preview['market']:,.2f}＋现金 ¥{preview['cash']:,.2f}＝总资产 ¥{preview['total']:,.2f}")
        if st.button('重算市值',key='ff_recalc'): st.success('已按数量×价格×汇率重算上方市值。')
        confirmed=st.checkbox('已核对本期交易、价格和汇率；接受明确标记的旧报价或估算值',key='ff_confirm_snapshot')
        if st.button('保存父亲账户快照',disabled=not confirmed,type='primary',key='ff_save_snapshot'):
            save_snapshot(data_dir,st.session_state['ff_base_ids'],st.session_state.get('ff_pending',[]),at,quotes,_token('ff_snapshot_token'))
            for key in ['ff_base_ids','ff_pending','ff_values','ff_applied_csv','ff_csv_rows','ff_refresh_results','ff_snapshot_token']:
                st.session_state.pop(key,None)
            st.session_state['ff_grid_version']=st.session_state.get('ff_grid_version',0)+1
            st.session_state['ff_reset_confirm']=True
            _saved('快照已保存，交易、现金、估值与净值已同步。')
    except (LedgerError,ValueError) as exc: st.error(str(exc))
    except OSError: st.error('快照保存失败，请检查数据目录后重试；本期草稿已保留。')


def render_father_fund(parent_data_dir):
    data_dir=Path(os.environ.get('FAMILYFUND_FATHER_DATA',str(Path(parent_data_dir)/'father')))
    vault=Path(os.environ.get('OBSIDIAN_VAULT_PATH',str(Path.home()/'Library/Mobile Documents/iCloud~md~obsidian/Documents/vault')))
    vault_input=vault/'familyfund/_father_input.md'
    input_path=Path(os.environ['FAMILYFUND_FATHER_INPUT']) if os.environ.get('FAMILYFUND_FATHER_INPUT') else (vault_input if vault_input.exists() else data_dir/'_father_input.md')
    template=Path(__file__).parent/'templates/father_input.md'
    text=(input_path if input_path.exists() else template).read_text(encoding='utf-8')
    st.header('Father Fund — 父亲账户')
    st.caption('独立记账 · 权益目标20%—30% · 好公司、好价格，也可通过宽基参与长期增长')
    if st.session_state.pop('ff_reset_confirm',False):
        st.session_state['ff_confirm_snapshot']=False
    if st.session_state.get('ff_message'):st.success(st.session_state.pop('ff_message'))
    try:
        events=read_events(data_dir);state=replay(events)
    except LedgerError as exc:
        st.error(str(exc));return
    if not state:
        _initialization(data_dir,text,input_path);return
    saved_events=events
    if 'ff_base_ids' not in st.session_state:
        st.session_state['ff_base_ids']=[e['event_id'] for e in events]
        st.session_state['ff_pending']=[]
    if st.session_state['ff_base_ids'] != [e['event_id'] for e in events]:
        st.warning('账户流水已变化，请重置本期草稿再核对。')
    nav,cost=performance(events)
    st.caption('以下为已保存账户；本期未保存交易只在下方预览中显示。')
    c1,c2,c3,c4=st.columns(4)
    c1.metric('账簿总资产',f"¥{state['total']:,.2f}")
    c2.metric('账簿净现金',f"¥{state['cash']:,.2f}")
    c3.metric('权益仓位',f"{state['equity']/state['total']:.2%}" if state['total'] else '—')
    c4.metric('接手以来损益',f"¥{state['pnl']:,.2f}")
    st.caption('损益＝当前总资产－期初资产－外部净入金；使用已保存估值，非实时收益。可用资金按券商对账另列。')
    p1,p2,p3=st.columns(3)
    p1.metric('单位净值',f"{nav.iloc[-1]['NAV']:.4f}")
    p2.metric('接手以来收益率（净值）',f"{nav.iloc[-1]['Cumulative_Return(%)']:+.2f}%")
    p3.metric('期初资产',f"¥{state['initial_total']:,.2f}")
    if len(nav)>1:
        st.line_chart(nav.assign(Date=pd.to_datetime(nav.Date)).set_index('Date')[['NAV']])
    if not cost.empty:
        pl=cost[(cost.Asset_Class!='Cash') & (cost.Market_Value>0)]
        st.dataframe(pl[['Name','Code','Cost_Basis','Market_Value','Profit_Loss','Profit_Loss_Rate']].rename(columns={
            'Name':'标的名称','Code':'代码','Cost_Basis':'接手以来净投入','Market_Value':'市值','Profit_Loss':'持仓盈亏','Profit_Loss_Rate':'持仓盈亏率(%)'}),hide_index=True,use_container_width=True)
        st.caption('持仓盈亏沿用主基金的累计NCF净投入口径；卖出后净投入减少，不额外引入新的成本算法。')
    lower=state['total']*Decimal('.2')
    upper=state['total']*Decimal('.3')
    st.write(f"权益目标 ¥{lower:,.2f}—¥{upper:,.2f}；当前权益 ¥{state['equity']:,.2f}。")
    if state['equity']>upper:st.info(f"降至30%需净减少权益约 ¥{state['equity']-upper:,.2f}；这是额度差额，不是自动卖出指令。")
    frame=holdings_frame(state)
    st.dataframe(frame,hide_index=True,use_container_width=True)
    if not frame.empty:
        grouped=frame.groupby('类别')['市值(元)'].sum().to_dict();grouped['净现金']=float(state['cash'])
        st.bar_chart(pd.DataFrame({'人民币金额':grouped}))
    if st.session_state.get('ff_pending'):
        st.info(f"本期有{len(st.session_state['ff_pending'])}条记录待保存，关闭页面可能丢失未保存草稿。")
    if st.button('重置本期草稿／重新读取账户',key='ff_reset_draft'):
        for key in ['ff_base_ids','ff_pending','ff_values','ff_applied_csv','ff_csv_rows','ff_refresh_results','ff_confirm_snapshot']:
            st.session_state.pop(key,None)
        st.session_state['ff_grid_version']=st.session_state.get('ff_grid_version',0)+1
        st.rerun()
    state=replay(events+st.session_state.get('ff_pending',[]))
    view=st.radio('账户操作',['登记成交','资金变动','估值更新','调整计划','交易流水与纠错','券商对账'],horizontal=True,key='ff_view')
    if view=='登记成交':
        _csv_input(data_dir,parent_data_dir,saved_events,state)
        with st.expander('手动登记已有持仓／纠正已保存成交'):
            _trade_form(data_dir,state,saved_events)
    elif view=='资金变动':_cash_form(data_dir)
    elif view=='估值更新':st.caption('在下方刷新并确认价格，再保存快照。')
    elif view=='调整计划':
        codes=list(state['positions'])
        if not codes:st.info('暂时没有标的，新增买入后可设置计划。')
        if codes:
            code=st.selectbox('设置计划的标的',codes,format_func=lambda c:f"{state['positions'][c]['name']}（{c}）",key='ff_plan_code')
            with st.form('ff_plan_form'):
                status=st.selectbox('状态',PLAN_STATUSES)
                target=st.text_input('目标市值（人民币，可留空）')
                note=st.text_area('计划与理由')
                submitted=st.form_submit_button('保存计划（不产生交易）')
            if submitted:
                try:
                    _stage(data_dir,[event('PLAN',now_beijing().isoformat(),dict(code=code,name=state['positions'][code]['name'],status=status,target_value=target,note=note))]);_saved('计划已加入本期草稿，最终保存快照后落盘。')
                except LedgerError as exc:st.error(str(exc))
            if state['plans']:
                plans=pd.DataFrame(state['plans'].values()).rename(columns={'name':'名称','code':'代码','status':'计划状态','target_value':'目标市值（元）','note':'理由'})
                st.dataframe(plans,hide_index=True,use_container_width=True)
    elif view=='交易流水与纠错':
        records=[]
        type_names={'OPEN':'期初','TRADE':'成交','CASH':'资金变动','QUOTE':'估值','PLAN':'计划','BROKER':'券商快照','VOID':'撤销','SNAPSHOT':'持仓快照'}
        for e in events:
            p=e['payload']
            records.append({'编号':p.get('reference',e['event_id'][:8]),'发生时间':e['effective_at'],'登记时间':e['recorded_at'],'类型':type_names[e['kind']],'标的':f"{p.get('name','')}（{p.get('code','')}）" if p.get('code') else '',
                            '状态':'已撤销' if e['event_id'] in state['voided'] else '有效','方向':p.get('side',p.get('action','')),'股数/份额':p.get('quantity',''),'人民币收付':p.get('cash_cny',p.get('amount','')),'备注':p.get('note',p.get('reason',''))})
        st.dataframe(pd.DataFrame(records),hide_index=True,use_container_width=True)
        st.download_button('导出完整流水CSV',(data_dir/'ledger.csv').read_bytes(),file_name='father_ledger.csv',mime='text/csv')
        candidates=[e for e in events if e['kind'] not in {'OPEN','VOID'} and e['event_id'] not in state['voided']]
        st.caption('成交内容有误可在“登记成交”中选择纠正；整笔误录可在此撤销。撤销会重新验证后续现金和持仓，不能造成超卖。')
        if candidates:
            target=st.selectbox('撤销目标',candidates,format_func=lambda e:f"{e['effective_at']} {type_names[e['kind']]} {e['payload'].get('name','')} {e['event_id'][:8]}")
            reason=st.text_input('撤销原因',key='ff_void_reason')
            if st.button('撤销误录（保留原流水）',key='ff_void'):
                try:_stage(data_dir,[event('VOID',now_beijing().isoformat(),dict(target=target['event_id'],reason=reason))]);_saved('撤销已加入本期草稿，最终保存快照后落盘。')
                except LedgerError as exc:st.error(str(exc))
    elif view=='券商对账':
        if state['broker']:_show_reconciliation(events,state['broker'])
        with st.expander('初始化时的最新券商参考'):_reference(text)
        with st.form('ff_broker_form'):
            at=st.text_input('券商快照时间',value=now_beijing().strftime('%Y-%m-%d %H:%M:%S'))
            c1,c2,c3=st.columns(3)
            total=c1.number_input('券商总资产',min_value=0.0,value=float(state['total']),format='%.2f')
            market=c2.number_input('券商证券市值',min_value=0.0,value=float(state['market']),format='%.2f')
            available=c3.number_input('券商可用资金',min_value=0.0,value=float(state['cash']),format='%.2f')
            st.caption('下面填券商全部非零持仓数量；缺少的行会按券商零持仓对账。表格预填账簿数量，请逐项核对。')
            qty_frame=frame[['名称','代码','股数/份额']].copy() if not frame.empty else pd.DataFrame(columns=['名称','代码','股数/份额'])
            broker_holdings=st.data_editor(qty_frame,num_rows='dynamic',hide_index=True,key='ff_broker_holdings')
            submitted=st.form_submit_button('保存券商快照并对账')
        if submitted:
            try:
                holdings=[dict(code=str(r['代码']),name=str(r['名称']),quantity=str(r['股数/份额'])) for _,r in broker_holdings.iterrows()]
                _stage(data_dir,[event('BROKER',at,dict(total=str(total),market=str(market),available=str(available),holdings=holdings))]);_saved('券商对账已加入本期草稿，请最终保存快照。')
            except LedgerError as exc:st.error(str(exc))

    if st.session_state.get('ff_pending'):
        with st.expander('核对本期待保存记录',expanded=True):
            names={'TRADE':'成交','CASH':'资金变动','PLAN':'计划','BROKER':'券商对账','VOID':'撤销'}
            rows=[]
            for e in st.session_state['ff_pending']:
                p=e['payload']
                rows.append(dict(时间=e['effective_at'],类型=names.get(e['kind'],'记录'),
                                 标的=f"{p.get('name','')}（{p.get('code','')}）" if p.get('code') else '',
                                 方向=p.get('side',p.get('action',p.get('status',''))),数量=p.get('quantity',''),
                                 人民币金额=p.get('cash_cny',p.get('amount','')),备注=p.get('note',p.get('reason',''))))
            st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
    _valuation(data_dir,parent_data_dir,saved_events,replay(saved_events+st.session_state.get('ff_pending',[])))
