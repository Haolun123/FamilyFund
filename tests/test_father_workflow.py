import sys
from pathlib import Path
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import father_fund as ff
import father_workflow as fw


def opening():
    return ff.event('OPEN','2026-11-01',dict(cash='10000',positions=[dict(
        code='002277',name='友阿股份',currency='CNY',bucket='权益',venue='场内',quantity='100',price='5',fx='1')]))


def meta(code='002277',**changes):
    m=fw.candidate(code,'友阿股份'); m['validation_status']='confirmed'; m.update(changes); return m


def quotes(price='6',at='2026-11-02'):
    return [dict(code='002277',price=price,fx='1',as_of=at+'T23:59:59')]


@pytest.mark.parametrize('alias',['HK0700','00700.HK','0700.HK','700.hk'])
def test_hk_alias(alias):
    assert fw.canonical_code(alias)=='0700.HK'
    assert fw.candidate(alias,kind='股票',currency='HKD')['quote_code']=='0700.HK'


def test_registry_survives_restart_and_never_changes_parent(tmp_path):
    parent=tmp_path/'portfolio.csv'; parent.write_text('untouched')
    root=tmp_path/'father'
    fw.save_instrument(root,meta())
    fw.save_instrument(root,meta('HK0700',currency='HKD'))
    assert set(fw.load_registry(root))=={'002277','0700.HK'}
    assert parent.read_text()=='untouched'
    assert list((root/'backups').glob('instrument_map_*.json'))
    state=ff.replay([opening()])
    assert fw.find_instrument('002277.SZ',state,fw.load_registry(root),tmp_path)['code']=='002277'


def test_registry_concurrent_merge_and_failed_replace(tmp_path,monkeypatch):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i:fw.save_instrument(tmp_path,meta(str(i))),range(8)))
    assert len(fw.load_registry(tmp_path))==8
    before=(tmp_path/'instrument_map.json').read_bytes()
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(fw.os,'replace',fail)
    with pytest.raises(OSError): fw.save_instrument(tmp_path,meta())
    assert (tmp_path/'instrument_map.json').read_bytes()==before


def test_parent_reference_is_read_only_and_requires_confirmation(tmp_path):
    pd.DataFrame([dict(Date='2026-11-01',Code='HK0700',Name='腾讯控股',Asset_Class='Individual_Stock',
        Shares=100,Currency='HKD')]).to_csv(tmp_path/'portfolio.csv',index=False)
    (tmp_path/'yf_symbols.json').write_text('{"HK0700": "0700.HK"}')
    originals={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    found=fw.find_instrument('00700.HK',ff.replay([opening()]),{},tmp_path)
    assert found['name']=='腾讯控股' and found['quote_code']=='0700.HK'
    assert found['validation_status']=='pending'
    assert originals=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


def test_pending_registry_cannot_authorize_financial_entry(tmp_path):
    m=fw.candidate('000001','平安银行'); fw.save_instrument(tmp_path,m)
    assert fw.find_instrument('000001',ff.replay([opening()]),fw.load_registry(tmp_path),tmp_path) is None
    with pytest.raises(ff.LedgerError,match='尚未确认'):
        fw.build_trade_batch(fw.parse_trades('2026-11-02,买入,000001,100,5,500'),{'000001':m},'a')


def test_csv_retains_real_identical_fills_and_stable_batch_ids():
    text='2026-11-02,买入,002277,100,5,505'
    rows=fw.parse_trades('日期,方向,代码,数量,价格,金额\n'+text+'\n'+text)
    batch=fw.build_trade_batch(rows,{'002277':meta()},'a')
    assert len(rows)==2 and batch[0]['event_id']!=batch[1]['event_id']
    assert fw.trade_fingerprint(rows[0])==fw.trade_fingerprint(rows[1])
    assert [e['event_id'] for e in batch]==[e['event_id'] for e in fw.build_trade_batch(rows,{'002277':meta()},'a')]
    assert fw.duplicate_counts(rows,[opening()]+batch)==2
    assert ff.replay([opening()]+batch)['cash']==8990


@pytest.mark.parametrize('text',['2026-11-02,买入,002277,NaN,5,500','2026-11-02,卖出,002277,0,5,500',
    'bad,买入,002277,100,5,500','2026-11-02,买入,002277,100,5','2026-11-02,其他,002277,100,5,500'])
def test_invalid_csv(text):
    with pytest.raises(ff.LedgerError): fw.parse_trades(text)


def test_foreign_trade_uses_supplied_cny_payment_and_alias():
    rows=fw.parse_trades('2026-11-02,买入,00700.HK,100,389.2,33113.20')
    batch=fw.build_trade_batch(rows,{'0700.HK':meta('HK0700',currency='HKD',name='腾讯控股')},'hk')
    op=ff.event('OPEN','2026-11-01',dict(cash='50000',positions=[]))
    state=ff.replay([op]+batch)
    assert state['cash']==Decimal('16886.80')
    assert abs(state['market']-Decimal('33113.20'))<Decimal('.000001')
    assert state['positions']['HK0700']['quantity']==100


def test_snapshot_atomic_save_idempotence_nav_and_cost(tmp_path):
    op=opening();ff.commit_events(tmp_path,[op])
    pending=fw.build_trade_batch(fw.parse_trades('2026-11-02,买入,002277,100,5,505'),{'002277':meta()},'a')
    assert len(ff.read_events(tmp_path))==1
    state=fw.save_snapshot(tmp_path,[op['event_id']],pending,'2026-11-02T23:59:59',quotes(),'snapshot-a')
    assert state['cash']==9495 and state['market']==1200
    assert len(ff.read_events(tmp_path))==3
    fw.save_snapshot(tmp_path,[op['event_id']],pending,'2026-11-02T23:59:59',quotes(),'snapshot-a')
    nav,cost=fw.performance(ff.read_events(tmp_path))
    assert len(nav)==2 and nav.iloc[-1].Total_Value==10695
    assert nav.iloc[-1].NAV==pytest.approx(round(10695/10500,4))
    stock=cost[cost.Code=='002277'].iloc[0]
    assert stock.Cost_Basis==1005 and stock.Profit_Loss==195


def test_external_deposit_excluded_from_nav_and_dividend_is_return(tmp_path):
    op=opening();ff.commit_events(tmp_path,[op])
    pending=[ff.event('CASH','2026-11-02',dict(action='入金',amount='1000')),
             ff.event('CASH','2026-11-02',dict(action='分红',amount='20'))]
    state=fw.save_snapshot(tmp_path,[op['event_id']],pending,'2026-11-02T23:59:59',quotes('5'),'s')
    nav,_=fw.performance(ff.read_events(tmp_path))
    assert state['pnl']==20 and state['external_flow']==1000
    assert nav.iloc[-1].NAV==pytest.approx(round(1+20/10500,4))


def test_failure_stale_page_and_incomplete_quotes_do_not_partially_save(tmp_path,monkeypatch):
    op=opening();ff.commit_events(tmp_path,[op]);before=(tmp_path/'ledger.csv').read_bytes()
    pending=[ff.event('CASH','2026-11-02',dict(action='入金',amount='1'))]
    with pytest.raises(ff.LedgerError,match='完整覆盖'):
        fw.save_snapshot(tmp_path,[op['event_id']],pending,'2026-11-02',[],'s')
    assert before==(tmp_path/'ledger.csv').read_bytes()
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(ff,'_atomic_write_csv',fail)
    with pytest.raises(OSError): fw.save_snapshot(tmp_path,[op['event_id']],pending,'2026-11-02T23:59:59',quotes(),'s')
    assert before==(tmp_path/'ledger.csv').read_bytes()
    monkeypatch.undo()
    ff.commit_events(tmp_path,pending)
    with pytest.raises(ff.LedgerError,match='其他页面'):
        fw.save_snapshot(tmp_path,[op['event_id']],[],'2026-11-02T23:59:59',quotes(),'s')


def test_cash_only_snapshot_and_future_or_early_date(tmp_path):
    op=ff.event('OPEN','2026-11-01',dict(cash='10000',positions=[]));ff.commit_events(tmp_path,[op])
    with pytest.raises(ff.LedgerError,match='未来'): fw.snapshot_event([op],'2099-01-01',[])
    with pytest.raises(ff.LedgerError,match='早于'): fw.snapshot_event([op],'2026-10-31',[])
    assert fw.save_snapshot(tmp_path,[op['event_id']],[],'2026-11-02',[],'s')['total']==10000


def test_legacy_quotes_retained_and_no_extra_trade_snapshot():
    op=opening();legacy=ff.event('QUOTE','2026-11-02T12:00:00',dict(quotes=[dict(code='002277',price='6',fx='1',as_of='2026-11-02T12:00:00')]))
    rows=fw.parse_trades('2026-11-03,卖出,002277,50,6,300')
    pending=fw.build_trade_batch(rows,{'002277':meta()},'a')
    snapshot=fw.snapshot_event([op,legacy]+pending,'2026-11-03T23:59:59',quotes('7','2026-11-03'))
    frame=fw.snapshot_portfolio([op,legacy]+pending+[snapshot])
    assert frame.Date.nunique()==3
    assert frame[frame.Date==snapshot['effective_at']].Total_Value.sum()==10650
    _,cost=fw.performance([op,legacy]+pending+[snapshot])
    assert cost[cost.Code=='002277'].iloc[0].Cost_Basis==200


def test_verify_identity_is_separate_from_quote_and_sanitizes_errors(monkeypatch):
    import yfinance as yf
    class Ticker:
        info=dict(symbol='002277.SZ',quoteType='EQUITY',currency='CNY',longName='友阿股份')
    monkeypatch.setattr(yf,'Ticker',lambda code:Ticker())
    monkeypatch.setattr(fw,'fetch_quote',lambda m:dict(status='error',price=None))
    verified,quote=fw.verify_instrument(fw.candidate('002277','友阿股份'))
    assert verified['validation_status']=='confirmed' and quote['price'] is None
    Ticker.info['currency']='USD'
    assert fw.verify_instrument(fw.candidate('002277','友阿股份'))[0]['validation_status']=='pending'
    def fail(*args):raise OSError('private connection details')
    monkeypatch.setattr(yf,'Ticker',fail)
    assert 'private' not in fw.verify_instrument(fw.candidate('002277','友阿股份'))[0]['validation_error']


def test_wrong_quote_mapping_cannot_pass_validation(monkeypatch):
    monkeypatch.setattr(fw,'fetch_quote',lambda m:dict(status='ok',price=5))
    verified,_=fw.verify_instrument(fw.candidate('002277','友阿股份',quote_code='600000.SS'))
    assert verified['validation_status']=='pending'


def test_otc_identity_and_price_router_reuse(monkeypatch):
    import price_fetcher
    monkeypatch.setattr(price_fetcher,'lookup_fund_name',lambda code:'基金名称')
    calls=[]
    def prices(frame,data_dir=None):
        calls.append((frame.iloc[0].Code,data_dir));return {frame.iloc[0].Code:dict(status='ok',price=1.234,date='2026-11-02')}
    monkeypatch.setattr(price_fetcher,'fetch_latest_prices',prices)
    verified,quote=fw.verify_instrument(fw.candidate('000001','基金名称',kind='场外基金'))
    assert verified['validation_status']=='confirmed' and quote['price']==1.234
    fw.fetch_quote(meta())
    assert calls==[('000001',None),('002277.SZ',None)]


@pytest.mark.parametrize('price',[float('nan'),float('inf'),0,-1,None])
def test_invalid_price_never_overwrites_saved_quote(monkeypatch,price):
    import price_fetcher
    monkeypatch.setattr(price_fetcher,'fetch_latest_prices',lambda frame:{'002277.SZ':dict(status='ok',price=price)})
    assert fw.fetch_quote(meta())['status']=='error'
