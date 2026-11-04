"""Father Fund page: transactions commit independently of Weekly Update."""
from decimal import Decimal
from pathlib import Path
import hashlib
import os
import uuid

import pandas as pd
import streamlit as st
from father_fund import (BUCKETS, CURRENCIES, CASH_TYPES, PLAN_STATUSES, LedgerError,
                         read_events, replay, event, commit_events, cancel_event,
                         holdings_frame, parse_input, parse_reference, reconcile, now_beijing)


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
        choices=['新标的']+list(state['positions'])
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
            commit_events(data_dir,additions)
            _saved('成交已保存，持仓与现金已同步更新。',scope+'_token')
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
            commit_events(data_dir,[event('CASH',at,dict(action=action,amount=str(amount),note=note),_token('ff_cash_token'))])
            _saved('资金变动已保存。','ff_cash_token')
        except LedgerError as exc:st.error(str(exc))


def _valuation(data_dir,state):
    frame=holdings_frame(state)
    if frame.empty:
        st.info('当前无非零持仓。');return
    edited=st.data_editor(frame[['名称','代码','币种','价格','折算汇率','报价时间']],disabled=['名称','代码','币种'],hide_index=True,use_container_width=True,key='ff_quotes_editor')
    st.caption('可手动填写同一时点价格和汇率。行情更新时间逐项显示；旧报价不会覆盖更新的成交或报价。保存估值不产生交易。')
    if st.button('获取最新报价预览',key='ff_fetch_quotes'):
        from price_fetcher import fetch_latest_prices
        results=[]
        # Sequential calls only; exchange suffix distinguishes listed ETFs from OTC fund NAV.
        progress=st.progress(0)
        for idx,row in frame.iterrows():
            code=str(row['代码'])
            if code.isdigit() and len(code)==6:
                listed=row['场所']=='场内'
                routed=code+('.SS' if code.startswith(('5','6')) else '.SZ') if listed else code
            else:routed=code
            try:
                got=fetch_latest_prices(pd.DataFrame([{'Date':now_beijing().date().isoformat(),'Code':routed}])).get(routed,{})
                results.append(dict(名称=row['名称'],代码=code,价格=got.get('price'),折算汇率=got.get('fx_rate',row['折算汇率']),报价时间=got.get('date'),状态=got.get('status','error'),说明=got.get('msg','')))
            except Exception as exc:
                results.append(dict(名称=row['名称'],代码=code,状态='error',说明=str(exc)))
            progress.progress((idx+1)/len(frame))
        st.session_state['ff_quote_preview']=results
    preview=st.session_state.get('ff_quote_preview')
    if preview:
        st.dataframe(pd.DataFrame(preview),hide_index=True,use_container_width=True)
        if st.button('保存获取成功的报价',key='ff_apply_quotes'):
            now=now_beijing().isoformat(timespec='seconds')
            quotes=[dict(code=q['代码'],price=str(q['价格']),fx=str(q['折算汇率']),as_of=min(str(q['报价时间'])[:10]+'T23:59:59',now)) for q in preview if q['状态']=='ok' and q.get('价格') and q.get('报价时间')]
            if not quotes:
                st.warning('没有成功报价。')
            else:
                try:
                    commit_events(data_dir,[event('QUOTE',now_beijing().isoformat(),dict(quotes=quotes))])
                    st.session_state.pop('ff_quote_preview',None);_saved('成功报价已保存；失败项目保持原值。')
                except LedgerError as exc:st.error(str(exc))
    if st.button('保存手动估值',key='ff_manual_quotes'):
        try:
            quotes=[dict(code=str(r['代码']),price=str(r['价格']),fx=str(r['折算汇率']),as_of=str(r['报价时间'])) for _,r in edited.iterrows()]
            commit_events(data_dir,[event('QUOTE',now_beijing().isoformat(),dict(quotes=quotes))]);_saved('估值已保存。')
        except LedgerError as exc:st.error(str(exc))


def render_father_fund(parent_data_dir):
    data_dir=Path(os.environ.get('FAMILYFUND_FATHER_DATA',str(Path(parent_data_dir)/'father')))
    vault=Path(os.environ.get('OBSIDIAN_VAULT_PATH',str(Path.home()/'Library/Mobile Documents/iCloud~md~obsidian/Documents/vault')))
    vault_input=vault/'familyfund/_father_input.md'
    input_path=Path(os.environ['FAMILYFUND_FATHER_INPUT']) if os.environ.get('FAMILYFUND_FATHER_INPUT') else (vault_input if vault_input.exists() else data_dir/'_father_input.md')
    template=Path(__file__).parent/'templates/father_input.md'
    text=(input_path if input_path.exists() else template).read_text(encoding='utf-8')
    st.header('Father Fund — 父亲账户')
    st.caption('独立记账 · 权益目标20%—30% · 好公司、好价格，也可通过宽基参与长期增长')
    if st.session_state.get('ff_message'):st.success(st.session_state.pop('ff_message'))
    try:
        events=read_events(data_dir);state=replay(events)
    except LedgerError as exc:
        st.error(str(exc));return
    if not state:
        _initialization(data_dir,text,input_path);return
    c1,c2,c3,c4=st.columns(4)
    c1.metric('账簿总资产',f"¥{state['total']:,.2f}")
    c2.metric('账簿净现金',f"¥{state['cash']:,.2f}")
    c3.metric('权益仓位',f"{state['equity']/state['total']:.2%}" if state['total'] else '—')
    c4.metric('接手以来损益',f"¥{state['pnl']:,.2f}")
    st.caption('损益＝当前总资产－期初资产－外部净入金；使用已保存估值，非实时收益。可用资金按券商对账另列。')
    lower=state['total']*Decimal('.2')
    upper=state['total']*Decimal('.3')
    st.write(f"权益目标 ¥{lower:,.2f}—¥{upper:,.2f}；当前权益 ¥{state['equity']:,.2f}。")
    if state['equity']>upper:st.info(f"降至30%需净减少权益约 ¥{state['equity']-upper:,.2f}；这是额度差额，不是自动卖出指令。")
    frame=holdings_frame(state)
    st.dataframe(frame,hide_index=True,use_container_width=True)
    if not frame.empty:
        grouped=frame.groupby('类别')['市值(元)'].sum().to_dict();grouped['净现金']=float(state['cash'])
        st.bar_chart(pd.DataFrame({'人民币金额':grouped}))
    view=st.radio('账户操作',['登记成交','资金变动','估值更新','调整计划','交易流水与纠错','券商对账'],horizontal=True,key='ff_view')
    if view=='登记成交':_trade_form(data_dir,state,events)
    elif view=='资金变动':_cash_form(data_dir)
    elif view=='估值更新':_valuation(data_dir,state)
    elif view=='调整计划':
        codes=list(state['positions'])
        if not codes:st.info('暂时没有标的，新增买入后可设置计划。');return
        code=st.selectbox('设置计划的标的',codes,format_func=lambda c:f"{state['positions'][c]['name']}（{c}）",key='ff_plan_code')
        with st.form('ff_plan_form'):
            status=st.selectbox('状态',PLAN_STATUSES)
            target=st.text_input('目标市值（人民币，可留空）')
            note=st.text_area('计划与理由')
            submitted=st.form_submit_button('保存计划（不产生交易）')
        if submitted:
            try:
                commit_events(data_dir,[event('PLAN',now_beijing().isoformat(),dict(code=code,name=state['positions'][code]['name'],status=status,target_value=target,note=note))]);_saved('计划已保存，持仓和现金不变。')
            except LedgerError as exc:st.error(str(exc))
        if state['plans']:
            plans=pd.DataFrame(state['plans'].values()).rename(columns={'name':'名称','code':'代码','status':'计划状态','target_value':'目标市值（元）','note':'理由'})
            st.dataframe(plans,hide_index=True,use_container_width=True)
    elif view=='交易流水与纠错':
        records=[]
        type_names={'OPEN':'期初','TRADE':'成交','CASH':'资金变动','QUOTE':'估值','PLAN':'计划','BROKER':'券商快照','VOID':'撤销'}
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
                try:cancel_event(data_dir,target['event_id'],reason);_saved('已追加撤销记录并重算持仓。')
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
                commit_events(data_dir,[event('BROKER',at,dict(total=str(total),market=str(market),available=str(available),holdings=holdings))]);_saved('券商快照已保存；账簿没有自动调平。')
            except LedgerError as exc:st.error(str(exc))
