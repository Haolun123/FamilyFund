import sys
from pathlib import Path
from decimal import Decimal as D
from concurrent.futures import ThreadPoolExecutor
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import father_fund as ff


def instrument(**changes):
    p=dict(code='002277',name='友阿股份',bucket='权益',venue='场内',currency='CNY',quantity='100',price='5',fx='1')
    p.update(changes)
    return p


def opening(cash='10000',positions=None):
    return ff.event('OPEN','2026-11-01 08:00:00',dict(cash=cash,positions=positions if positions is not None else [instrument()]))


def trade(reference='T1',at='2026-11-02 09:30:00',**changes):
    p=instrument(side='买入',reference=reference,cash_cny='505',fees_cny='5')
    p.update(changes)
    return ff.event('TRADE',at,p)


def test_trade_cash_includes_fees_once_and_closed_holding(tmp_path):
    ff.commit_events(tmp_path,[opening(),trade()])
    state=ff.replay(ff.read_events(tmp_path))
    assert state['cash']==D(9495)
    assert state['positions']['002277']['quantity']==200
    assert state['pnl']==-5
    ff.commit_events(tmp_path,[trade('T2','2026-11-03',side='卖出',quantity='200',cash_cny='995')])
    state=ff.replay(ff.read_events(tmp_path))
    assert state['cash']==10490
    assert ff.holdings_frame(state).empty
    assert state['pnl']==-10


def test_hk_valuation_and_actual_cny_settlement_are_separate(tmp_path):
    hk=instrument(code='00700.HK',name='腾讯控股',currency='HKD',price='395.3',fx='.8571563369592714394')
    state=ff.commit_events(tmp_path,[opening(positions=[]),trade(**dict(hk,quantity='10',cash_cny='3400',fees_cny='12'))])
    assert state['cash']==6600
    assert abs(state['market']-D('3388.339'))<D('.00001')
    assert state['pnl'] < 0


def test_duplicate_request_id_is_idempotent_and_reference_is_unique(tmp_path):
    first=opening();buy=trade()
    ff.commit_events(tmp_path,[first,buy])
    ff.commit_events(tmp_path,[buy])
    assert len(ff.read_events(tmp_path))==2
    with pytest.raises(ff.LedgerError,match='编号重复'):
        ff.commit_events(tmp_path,[trade()])
    assert len(ff.read_events(tmp_path))==2
    changed=dict(buy,payload=dict(buy['payload'],cash_cny='600'))
    with pytest.raises(ff.LedgerError,match='不同内容'):
        ff.commit_events(tmp_path,[changed])


def test_same_day_multiple_real_fills_with_different_references(tmp_path):
    s=ff.commit_events(tmp_path,[opening(),trade('fill1'),trade('fill2')])
    assert s['positions']['002277']['quantity']==300
    assert s['cash']==8990


@pytest.mark.parametrize('change,message',[
    ({'side':'卖出','quantity':'101'},'超过'),
    ({'cash_cny':'20000'},'现金不足'),
    ({'quantity':'NaN'},'范围'),
    ({'price':'inf'},'范围'),
    ({'fx':'0.9'},'人民币资产汇率'),
    ({'venue':'未知'},'交易场所'),
    ({'name':''},'名称不能为空'),
])
def test_invalid_trade_cannot_partially_write(tmp_path,change,message):
    ff.commit_events(tmp_path,[opening()])
    before=(tmp_path/'ledger.csv').read_bytes()
    with pytest.raises(ff.LedgerError,match=message):
        ff.commit_events(tmp_path,[trade(**change)])
    assert (tmp_path/'ledger.csv').read_bytes()==before


def test_atomic_correction_and_invalid_void_preserve_originals(tmp_path):
    buy=trade(quantity='200',cash_cny='1005')
    sell=trade('T2','2026-11-03',side='卖出',quantity='250',cash_cny='1245')
    ff.commit_events(tmp_path,[opening(),buy,sell])
    with pytest.raises(ff.LedgerError,match='超过'):
        ff.cancel_event(tmp_path,buy['event_id'],'误录')
    assert len(ff.read_events(tmp_path))==3
    void=ff.event('VOID','2026-11-04',dict(target=buy['event_id'],reason='费用写错'))
    corrected=trade(quantity='200',cash_cny='1006',fees_cny='6')
    s=ff.commit_events(tmp_path,[void,corrected])
    assert s['cash']==10239
    assert s['positions']['002277']['quantity']==50
    assert len(ff.read_events(tmp_path))==5
    assert buy['event_id'] in s['voided']


def test_backdated_transactions_validate_historical_cash_not_only_final_cash(tmp_path):
    ff.commit_events(tmp_path,[opening(cash='100'),ff.event('CASH','2026-11-03',dict(action='入金',amount='1000'))])
    with pytest.raises(ff.LedgerError,match='现金不足'):
        ff.commit_events(tmp_path,[trade()])


def test_quotes_do_not_change_quantities_and_stale_quotes_do_not_replace_trades(tmp_path):
    buy=trade(at='2026-11-03 10:00:00',price='6',cash_cny='605')
    old=ff.event('QUOTE','2026-11-04',dict(quotes=[dict(code='002277',price='2',fx='1',as_of='2026-11-02')]))
    new=ff.event('QUOTE','2026-11-04 11:00:00',dict(quotes=[dict(code='002277',price='7',fx='1',as_of='2026-11-04 10:00:00')]))
    s=ff.commit_events(tmp_path,[opening(),buy,old,new])
    assert s['positions']['002277']['quantity']==200
    assert s['market']==1400
    assert s['cash']==9395
    assert s['positions']['002277']['quoted_at']=='2026-11-04T10:00:00'


def test_dividends_and_external_flows_have_different_pnl_semantics(tmp_path):
    records=[opening()]
    for action,amount in [('入金','1000'),('出金','100'),('分红','30'),('利息','2'),('费用','1')]:
        records.append(ff.event('CASH','2026-11-02',dict(action=action,amount=amount)))
    s=ff.commit_events(tmp_path,records)
    assert s['external_flow']==900
    assert s['pnl']==31


def test_plans_do_not_move_cash_or_holdings(tmp_path):
    s=ff.commit_events(tmp_path,[opening(),ff.event('PLAN','2026-11-02',dict(code='002277',status='待退出',target_value='0',note='能力圈'))])
    assert s['cash']==10000 and s['market']==500
    assert ff.holdings_frame(s).iloc[0]['计划状态']=='待退出'


def test_latest_broker_reconciliation_uses_its_timestamp(tmp_path):
    checkpoint=ff.event('BROKER','2026-11-02 08:00:00',dict(total='10500',market='500',available='10258.75',holdings=[dict(code='002277',quantity='100')]))
    events=[opening(),checkpoint,trade(at='2026-11-03')]
    ff.commit_events(tmp_path,events)
    r=ff.reconcile(events,checkpoint)
    assert r['available_gap']==D('258.75')
    assert r['total_difference']==0 and r['holdings']==[]


def test_missing_broker_holding_is_reported():
    check=ff.event('BROKER','2026-11-02',dict(total='10500',market='500',available='10000',holdings=[]))
    r=ff.reconcile([opening(),check],check)
    assert r['holdings'][0]['code']=='002277'


def test_concurrent_appends_no_lost_transactions(tmp_path):
    ff.commit_events(tmp_path,[opening()])
    def deposit(i):
        ff.commit_events(tmp_path,[ff.event('CASH','2026-11-02',dict(action='入金',amount='10',note=str(i)))])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(deposit,range(8)))
    assert len(ff.read_events(tmp_path))==9
    assert ff.replay(ff.read_events(tmp_path))['cash']==10080
    assert list((tmp_path/'backups').glob('ledger_*.csv'))


def test_failed_storage_does_not_claim_commit(tmp_path,monkeypatch):
    ff.commit_events(tmp_path,[opening()]);before=(tmp_path/'ledger.csv').read_bytes()
    def fail(*args):raise OSError('simulated full disk')
    monkeypatch.setattr(ff,'_atomic_write_csv',fail)
    with pytest.raises(OSError):ff.commit_events(tmp_path,[trade()])
    assert (tmp_path/'ledger.csv').read_bytes()==before


def test_invalid_initialization_and_original_source_isolation(tmp_path):
    parent=tmp_path/'parent';parent.mkdir();sentinel=parent/'portfolio.csv';sentinel.write_text('unchanged')
    child=parent/'father'
    with pytest.raises(ff.LedgerError):ff.commit_events(child,[opening(positions=[instrument(),instrument()])])
    assert not (child/'ledger.csv').exists()
    ff.commit_events(child,[opening()])
    with pytest.raises(ff.LedgerError):ff.commit_events(child,[opening()])
    assert sentinel.read_text()=='unchanged'


def markdown():
    return '''## 1. 期初账户
| 字段 | 值 |
|---|---|
| 基准时间 | 2026-11-01 08:00:00 |
| 期初净现金（人民币） | 10000 |
## 2. 期初持仓
| 名称 | 代码 | 类别 | 币种 | 数量 | 期初价格 | 折算汇率 |
|---|---|---|---|---|---|---|
| 友阿股份 | 002277 | 权益 | CNY | 100 | 5 | 1 |
| 腾讯控股 | 00700.HK | 权益 | HKD | 0 | | |
## 3. 基准之后的成交
| 成交编号 | 成交时间 | 方向 | 名称 | 代码 | 类别 | 币种 | 数量 | 成交价 | 折算汇率 | 人民币实际收付 | 费用（已含） | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 | 2026-11-02 09:30:00 | 卖出 | 友阿股份 | 002277 | 权益 | CNY | 100 | 5.1 | 1 | 505 | 5 | 清仓 |
## 4. 其他资金变动
| 发生时间 | 类型 | 人民币金额 | 备注 |
|---|---|---|---|
| | | | |
'''


def test_markdown_import_preserves_codes_and_can_close_position():
    events=ff.parse_input(markdown());s=ff.replay(events)
    assert s['cash']==10505 and s['market']==0
    assert events[1]['payload']['code']=='002277'


def test_markdown_incomplete_opening_does_not_guess_cash_or_date():
    with pytest.raises(ff.LedgerError):ff.parse_input(markdown().replace('10000',''))
    with pytest.raises(ff.LedgerError):ff.parse_input(markdown().replace('| 100 | 5 |','| | 5 |'))


def test_reference_is_not_an_opening():
    text='''## 5. 最新券商对账
| 字段 | 值 |
|---|---|
| 记录日期 | 2026-11-04 |
| 对账时间（选填） | |
| 总资产（人民币） | 110000 |
| 证券市值（人民币） | 75000 |
| 可用资金（人民币） | 35258.75 |
## 6. 最新持仓参考
| 名称 | 代码 | 数量 |
|---|---|---|
| 腾讯控股 | 00700.HK | 100 |
'''
    ref=ff.parse_reference(text)
    assert ref['effective_at']==''
    assert ref['payload']['available']=='35258.75'
    with pytest.raises(ff.LedgerError):ff.parse_input(text)


def test_bundled_template_can_be_filled_without_placeholder_import_errors():
    template=(Path(__file__).resolve().parents[1]/'dashboard/templates/father_input.md').read_text()
    template=template.replace('| 基准时间 | |','| 基准时间 | 2026-11-01 08:00:00 |').replace('| 期初净现金（人民币） | |','| 期初净现金（人民币） | 1000 |')
    lines=[];section=''
    for line in template.splitlines():
        if line.startswith('## '):section=line[3:]
        if section=='2. 期初持仓' and line.startswith('|'):
            cells=[c.strip() for c in line.strip('|').split('|')]
            if len(cells)==8 and cells[1] not in {'代码','---'}:
                cells[4]='0'
                line='| '+' | '.join(cells)+' |'
        lines.append(line)
    s=ff.replay(ff.parse_input('\n'.join(lines)))
    assert s['cash']==1000 and s['market']==0


def test_default_record_time_is_beijing_even_if_host_uses_utc(monkeypatch):
    from datetime import datetime,timezone
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz=None):
            fixed=datetime(2026,11,3,17,0,0,tzinfo=timezone.utc)
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(ff,'datetime',FixedClock)
    assert ff.now_beijing().isoformat()=='2026-11-04T01:00:00'
    assert ff.event('CASH','2026-11-04',dict(action='入金',amount='1'))['recorded_at'].startswith('2026-11-04T01:00:00')


def test_snapshot_requires_complete_unique_quotes_and_one_per_day(tmp_path):
    ff.commit_events(tmp_path,[opening()]);before=(tmp_path/'ledger.csv').read_bytes()
    quote=dict(code='002277',price='6',fx='1',as_of='2026-11-02')
    for quotes in [[],[quote,quote],[dict(quote,code='not-held')]]:
        with pytest.raises(ff.LedgerError,match='完整覆盖'):
            ff.commit_events(tmp_path,[ff.event('SNAPSHOT','2026-11-02',dict(quotes=quotes))])
        assert (tmp_path/'ledger.csv').read_bytes()==before
    ff.commit_events(tmp_path,[ff.event('SNAPSHOT','2026-11-02',dict(quotes=[quote]))])
    with pytest.raises(ff.LedgerError,match='已有快照'):
        ff.commit_events(tmp_path,[ff.event('SNAPSHOT','2026-11-02T23:59:59',dict(quotes=[quote]))])


def test_expected_ids_checked_under_account_lock(tmp_path):
    op=opening();ff.commit_events(tmp_path,[op])
    ff.commit_events(tmp_path,[ff.event('CASH','2026-11-02',dict(action='入金',amount='10'))])
    with pytest.raises(ff.LedgerError,match='流水已更新'):
        ff.commit_events(tmp_path,[trade()],expected_ids=[op['event_id']])
    assert len(ff.read_events(tmp_path))==2


def test_backdated_trade_does_not_replace_later_saved_valuation():
    quote=ff.event('QUOTE','2026-11-03',dict(quotes=[dict(code='002277',price='7',fx='1',as_of='2026-11-03')]))
    state=ff.replay([opening(),quote,trade(at='2026-11-02',price='3',cash_cny='300')])
    assert state['positions']['002277']['price']==7
    assert state['market']==1400


def test_stale_cny_snapshot_with_invalid_fx_is_rejected():
    bad=ff.event('SNAPSHOT','2026-11-03',dict(quotes=[dict(code='002277',price='6',fx='.9',as_of='2026-11-01')]))
    with pytest.raises(ff.LedgerError,match='人民币报价汇率'):
        ff.replay([opening(),trade(),bad])
