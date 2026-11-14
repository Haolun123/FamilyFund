from datetime import date
from pathlib import Path
import sys
import json
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import three_fund_report as t
from father_fund import event, commit_events


def row(at,value=100,flow=0,**kw):
    return dict(Date=at,Asset_Class='Individual_Stock',Platform='测试',Name='测试股票',
                Code='600001',Currency='CNY',Exchange_Rate=1.,Shares=10.,
                Current_Price=value/10,Total_Value=value,Net_Cash_Flow=flow,**kw)


def cash(at,value=100,flow=0):
    result=row(at,value,flow)
    result.update(Asset_Class='Cash',Code='CASH',Name='现金',Shares=value,Current_Price=1.)
    return result


def father(root,old_price=10,end_price=11,quote_day='2026-11-06',save_day='2026-11-08',extra=None):
    p=dict(code='600001',name='测试股票',currency='CNY',fx='1',price=str(old_price),
           quantity='10',bucket='权益',venue='场内')
    events=[event('OPEN','2026-11-02T00:00:00',dict(cash='100',positions=[p]))]
    if extra:
        events+=extra
    quotes=[dict(code='600001',price=str(end_price),fx='1',as_of=quote_day+'T23:59:59')]
    events.append(event('SNAPSHOT',save_day+'T23:59:59',dict(quotes=quotes)))
    commit_events(root,events)


@pytest.fixture
def accounts(tmp_path):
    for folder in [tmp_path,tmp_path/'son']:
        folder.mkdir(exist_ok=True)
        pd.DataFrame([row('2026-10-30'),cash('2026-10-30'),row('2026-11-06',110),cash('2026-11-06',150,50)]).to_csv(folder/'portfolio.csv',index=False)
    father(tmp_path/'father')
    return tmp_path


def test_weekend_accounting_and_future_week():
    assert t.week_friday('2026-11-08')==date(2026,11,6)
    assert t.week_friday('2026-11-09')==date(2026,11,13)
    b=t.period('2026-11-02','2026-11-08',today=date(2026,11,9))
    assert b['baseline']=='2026-10-30' and b['weeks']==['2026-11-06']
    with pytest.raises(t.ReportError,match='尚未结束'):
        t.period('2026-11-09','2026-11-13',today=date(2026,11,9))
    with pytest.raises(t.ReportError,match='起始周'):
        t.period('2026-11-13','2026-11-06')


def test_actual_income_removed_and_father_inception(accounts):
    hashes=t.source_versions(t.account_paths(accounts))
    result=t.build_report(accounts,'2026-11-02','2026-11-06')
    main,son,f=result['funds']
    assert main['pnl']==10 and main['return_pct']==pytest.approx(5)
    assert sum(a['value'] for a in main['allocation'])==main['total']
    assert sum(a['weight_pct'] for a in main['allocation'])==pytest.approx(100)
    assert son['external']==50 and son['total']==260
    assert f['baseline']=='2026-11-02' and f['inception']
    assert f['pnl']==10 and f['return_pct']==pytest.approx(5)
    assert any('报价日期未验证' in w for w in main['warnings'])
    assert t.source_versions(t.account_paths(accounts))==hashes
    assert '父亲基金' in result['markdown'] and '合并收益率' in result['markdown']
    assert '期末资产类别' in result['markdown']


def test_invalid_second_account_evidence_does_not_save_first(accounts):
    paths=t.account_paths(accounts)
    records=t.date_rows(paths,'2026-11-02','2026-11-06')
    records.loc[records.Fund=='son','Expected_Date']='2026-11-09'
    with pytest.raises(t.ReportError,match='期望估值日'):
        t.save_date_rows(paths,records,t.source_versions(paths))
    assert not (accounts/'valuation_dates.csv').exists()


@pytest.mark.parametrize('missing',['2026-10-30','2026-11-06'])
def test_missing_boundary_fails_without_falling_back(accounts,missing):
    p=accounts/'portfolio.csv';frame=pd.read_csv(p);frame=frame[frame.Date!=missing]
    if missing=='2026-10-30':
        # An earlier opening establishes that this is missing data, not inception.
        frame=pd.concat([pd.DataFrame([row('2026-10-23'),cash('2026-10-23')]),frame])
    frame.to_csv(p,index=False)
    with pytest.raises(t.ReportError,match=missing):
        t.build_report(accounts,'2026-11-02','2026-11-06')


def test_missing_middle_week_rejected(accounts):
    for p in [accounts/'portfolio.csv',accounts/'son/portfolio.csv']:
        frame=pd.read_csv(p)
        frame=pd.concat([frame,pd.DataFrame([row('2026-11-20'),cash('2026-11-20')])])
        frame.to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='2026-11-13'):
        t.build_report(accounts,'2026-11-02','2026-11-20',today=date(2026,11,23))


def test_cashflow_chain_uses_intermediate_unrounded_values(accounts):
    p=accounts/'portfolio.csv'
    pd.DataFrame([cash('2026-10-30',100),cash('2026-11-06',210,100),cash('2026-11-13',231)]).to_csv(p,index=False)
    f=t._calculate('main',accounts,t.period('2026-11-02','2026-11-13',today=date(2026,11,16)))
    assert f['return_pct']==pytest.approx(21)
    assert f['pnl']==31 and f['external']==100


@pytest.mark.parametrize('price_day',['2026-11-05','2026-11-09'])
def test_known_stale_and_future_quote_rejected(accounts,price_day):
    p=accounts/'portfolio.csv';frame=pd.read_csv(p)
    frame['Price_Date']='';frame.loc[frame.Date=='2026-11-06','Price_Date']=price_day
    frame.to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='过期|晚于'):
        t.build_report(accounts,'2026-11-02','2026-11-06')


def test_explicit_holiday_expected_date_with_reason(accounts):
    paths=t.account_paths(accounts)
    rows=t.date_rows(paths,'2026-11-02','2026-11-06')
    rows=rows[(rows.Fund=='main')&(rows.Date=='2026-11-06')].copy()
    rows['Price_Date']='2026-11-05';rows['Expected_Date']='2026-11-05';rows['Reason']='交易市场周五休市，已核实'
    t.save_date_rows(paths,rows,t.source_versions(paths))
    f=t.build_report(accounts,'2026-11-02','2026-11-06')['funds'][0]
    assert any('用户确认期望' in w for w in f['warnings'])
    rows['Reason']=''
    with pytest.raises(t.ReportError,match='理由'):
        t.save_date_rows(paths,rows,t.source_versions(paths))


def test_date_evidence_invalidated_by_price_edit(accounts):
    paths=t.account_paths(accounts);rows=t.date_rows(paths,'2026-11-02','2026-11-06')
    rows=rows[(rows.Fund=='main')&(rows.Date=='2026-11-06')].copy();rows['Price_Date']='2026-11-06'
    t.save_date_rows(paths,rows,t.source_versions(paths))
    p=accounts/'portfolio.csv';frame=pd.read_csv(p)
    mask=(frame.Date=='2026-11-06')&(frame.Code.astype(str)=='600001')
    frame.loc[mask,'Current_Price']=12;frame.loc[mask,'Total_Value']=120;frame.to_csv(p,index=False)
    f=t.build_report(accounts,'2026-11-02','2026-11-06')['funds'][0]
    assert any('报价日期未验证' in w for w in f['warnings'])


def test_stamp_captures_provider_date_not_snapshot_date():
    frame=pd.DataFrame([row('2026-11-06',110)])
    stamped=t.stamp_quote_dates(frame,{'600001':dict(status='ok',price=11,date='2026-11-05',fx_rate=1)})
    assert stamped.iloc[0].Price_Date=='2026-11-05'
    assert stamped.iloc[0].FX_Date==''
    assert 'Price_Date' not in frame


def test_date_editor_does_not_restore_evidence_for_changed_price(accounts):
    p=accounts/'portfolio.csv';frame=pd.read_csv(p)
    frame['Price_Date']='2026-11-06'
    frame['Price_Date_Value']='999'
    frame.to_csv(p,index=False)
    records=t.date_rows(t.account_paths(accounts),'2026-11-02','2026-11-06')
    assert records[records.Fund=='main'].Price_Date.eq('').all()


def test_new_snapshot_missing_date_blocks_report_but_legacy_warns(accounts):
    p=accounts/'portfolio.csv';frame=pd.read_csv(p)
    frame['Quote_Metadata_Version']=''
    frame.loc[frame.Date=='2026-11-06','Quote_Metadata_Version']='1'
    frame.to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='新快照缺少Price_Date'):
        t.build_report(accounts,'2026-11-02','2026-11-06')
    paths=t.account_paths(accounts);records=t.date_rows(paths,'2026-11-02','2026-11-06')
    rows=records[(records.Fund=='main')&(records.Date=='2026-11-06')].copy()
    rows['Price_Date']='2026-11-06'
    t.save_date_rows(paths,rows,t.source_versions(paths))
    result=t.build_report(accounts,'2026-11-02','2026-11-06')
    assert result['funds'][0]['pnl']==10


def test_weekend_deposit_allowed_next_workweek_trade_rejected(accounts):
    p=accounts/'son/transaction.csv'
    tx=dict(Date='2026-11-06',Trade_Date='2026-11-08',Type='入金',Name='现金',Code='CASH',Amount_CNY=50)
    pd.DataFrame([tx]).to_csv(p,index=False)
    result=t.build_report(accounts,'2026-11-02','2026-11-06')
    assert any('周末操作' in w for w in result['funds'][1]['warnings'])
    tx['Trade_Date']='2026-11-09';pd.DataFrame([tx]).to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='跨周'):
        t.build_report(accounts,'2026-11-02','2026-11-06')


def test_father_weekend_snapshot_with_stale_quote_rejected(accounts):
    p=accounts/'father/ledger.csv';frame=pd.read_csv(p)
    payload=json.loads(frame.iloc[-1].payload);payload['quotes'][0]['as_of']='2026-11-05T23:59:59'
    frame.loc[frame.index[-1],'payload']=json.dumps(payload);frame.to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='过期'):
        t.build_report(accounts,'2026-11-02','2026-11-06')


def test_father_weekend_manual_quote_does_not_break_replay_or_hide_date_rows(accounts):
    p=accounts/'father/ledger.csv';frame=pd.read_csv(p)
    payload=json.loads(frame.iloc[-1].payload)
    payload['quotes'][0]['as_of']='2026-11-08T23:59:59'
    payload['quotes'][0]['source']='手动'
    frame.loc[frame.index[-1],'payload']=json.dumps(payload);frame.to_csv(p,index=False)
    original=p.read_bytes()
    # The journal is valid on its saved Sunday; its true quote date must not
    # become an impossible Friday timestamp during report reconstruction.
    dates=t.date_rows(t.account_paths(accounts),'2026-11-02','2026-11-06')
    f=dates[(dates.Fund=='father')&(dates.Date=='2026-11-06')]
    assert len(f)==1 and f.iloc[0].Price_Date=='2026-11-08'
    with pytest.raises(t.ReportError,match='晚于截止日'):
        t.build_report(accounts,'2026-11-02','2026-11-06')
    # Explicit confirmation is bound to this value, not a journal rewrite.
    f=f.copy();f['Price_Date']='2026-11-06';f['Reason']='核对后确认实际为周五收盘价格，周日只是录入日'
    t.save_date_rows(t.account_paths(accounts),f,t.source_versions(t.account_paths(accounts)))
    assert t.build_report(accounts,'2026-11-02','2026-11-06')['funds'][2]['pnl']==10
    assert p.read_bytes()==original


def test_later_father_transaction_not_counted_into_previous_week(accounts):
    extra=event('CASH','2026-11-09T12:00:00',dict(action='入金',amount='1000'))
    commit_events(accounts/'father',[extra])
    f=t.build_report(accounts,'2026-11-02','2026-11-06')['funds'][2]
    assert f['external']==0 and f['total']==210


def test_closed_position_contribution_retained(accounts):
    p=accounts/'portfolio.csv'
    end=row('2026-11-06',0,-110);end['Shares']=0
    pd.DataFrame([row('2026-10-30'),cash('2026-10-30'),end,cash('2026-11-06',210)]).to_csv(p,index=False)
    f=t.build_report(accounts,'2026-11-02','2026-11-06')['funds'][0]
    assert f['pnl']==10 and f['contributions'][0]['pnl']==10


def test_bad_values_and_duplicate_codes_rejected(accounts):
    p=accounts/'portfolio.csv';frame=pd.read_csv(p)
    pd.concat([frame,frame.iloc[-1:]]).to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='重复'):
        t.build_report(accounts,'2026-11-02','2026-11-06')
    frame.loc[frame.Date=='2026-11-06','Total_Value']=-1;frame.to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='无效'):
        t.build_report(accounts,'2026-11-02','2026-11-06')


def test_fx_date_required_for_foreign_assets(accounts):
    p=accounts/'portfolio.csv';frame=pd.read_csv(p)
    frame.loc[frame.Asset_Class!='Cash','Currency']='HKD'
    frame['Price_Date']=frame.Date
    frame['FX_Date']='2026-10-01';frame.to_csv(p,index=False)
    with pytest.raises(t.ReportError,match='过期'):
        t.build_report(accounts,'2026-11-02','2026-11-06')


def test_source_change_blocks_save_and_date_evidence(accounts,tmp_path):
    result=t.build_report(accounts,'2026-11-02','2026-11-06')
    paths=t.account_paths(accounts);versions=t.source_versions(paths)
    records=t.date_rows(paths,'2026-11-02','2026-11-06')
    p=accounts/'portfolio.csv';p.write_text(p.read_text()+'\n')
    with pytest.raises(t.ReportError,match='变化'):
        t.save_report(result,accounts,tmp_path)
    with pytest.raises(t.ReportError,match='变化'):
        t.save_date_rows(paths,records,versions)
    assert not (accounts/'weekly_summaries').exists()


def test_versions_sync_failure_retry_and_conflict(accounts,tmp_path):
    vault=tmp_path/'vault';vault.mkdir()
    result=t.build_report(accounts,'2026-11-02','2026-11-06')
    first=t.save_report(result,accounts,vault)
    second=t.save_report(result,accounts,vault)
    assert first['synced'] and second['synced'] and first['filename']!=second['filename']
    assert (vault/'familyfund/weekly_summaries'/first['filename']).read_text()==result['markdown']
    failed=t.save_report(result,accounts,tmp_path/'missing-vault')
    assert not failed['synced'] and Path(failed['data_path']).exists()
    retry=t.sync_saved_report(accounts,failed['filename'],vault)
    assert retry['synced']
    dest=vault/'familyfund/weekly_summaries'/failed['filename'];dest.write_text('独立编辑')
    retry=t.sync_saved_report(accounts,failed['filename'],vault)
    assert not retry['synced'] and dest.read_text()=='独立编辑'
    assert len(list((accounts/'weekly_summaries').glob('三基金*.md')))==3


def test_ui_has_validation_preview_and_retry():
    source=(Path(__file__).resolve().parents[1]/'dashboard/three_fund_tab.py').read_text()
    assert '校验并预览报告' in source and '重试同步同一份报告' in source
    assert 'build_report' in source and 'save_report' in source


def test_streamlit_preview_generate_and_download(accounts,tmp_path,monkeypatch):
    AppTest=pytest.importorskip('streamlit.testing.v1').AppTest
    import importlib
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dashboard'))
    ui=importlib.import_module('three_fund_tab')
    monkeypatch.setattr(ui,'today_beijing',lambda:date(2026,11,9))
    monkeypatch.setenv('FAMILYFUND_SON_DATA',str(accounts/'son'))
    monkeypatch.setenv('FAMILYFUND_FATHER_DATA',str(accounts/'father'))
    vault=tmp_path/'vault';vault.mkdir()
    monkeypatch.setenv('OBSIDIAN_VAULT_PATH',str(vault))
    app=AppTest.from_string(f'from three_fund_tab import render_three_fund_report\nrender_three_fund_report({str(accounts)!r})').run()
    assert not app.exception
    app.button(key='tfr_preview_button').click().run()
    assert not app.exception and not app.error
    app.button(key='tfr_generate').click().run()
    assert not app.exception and not app.error
    assert len(list((accounts/'weekly_summaries').glob('三基金*.md')))==1
    assert len(list((vault/'familyfund/weekly_summaries').glob('三基金*.md')))==1
