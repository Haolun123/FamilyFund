from pathlib import Path
from datetime import date
import sys
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import son_bookkeeping as sb


def template(balance=18666):
    return pd.DataFrame([dict(Asset_Class='US_Growth_Fund',Platform='纳指场外',Name='南方纳指100 I类',
        Code='021000',Currency='CNY',Exchange_Rate=1.,Shares=3397.66,Current_Price=2.2607,
        Total_Value=7681.09,Net_Cash_Flow=0.),dict(Asset_Class='Cash',Platform='专属账户',Name='Son Fund现金',
        Code='CASH',Currency='CNY',Exchange_Rate=1.,Shares=22066.,Current_Price=1.,
        Total_Value=float(balance),Net_Cash_Flow=0.)])


def buy(**changes):
    row=dict(code='021000',name='南方纳指100 I类',amount=200.,shares=80.,nav=2.5,trade_date='2026-10-30',note='短信确认')
    row.update(changes);return row


def test_stale_shares_never_override_cash_balance():
    old=template();normalized=sb.normalize_cash(old)
    cash=normalized.iloc[-1]
    assert cash.Shares==cash.Total_Value==18666
    assert cash.Current_Price==cash.Exchange_Rate==1
    assert old.iloc[-1].Shares==22066
    # The old one-unit representation also follows Total_Value.
    old.loc[1,['Shares','Current_Price']]=[1,22066]
    assert sb.normalize_cash(old).iloc[-1].Shares==18666


def test_sms_buys_create_real_rows_and_decrease_cash_once():
    result,tx=sb.apply_buys(template(),[buy(),buy()],'2026-10-30')
    assert result.iloc[-1].Shares==result.iloc[-1].Total_Value==18266
    assert result.iloc[0].Shares==3557.66 and result.iloc[0].Net_Cash_Flow==400
    assert len(tx)==2 and tx[0]['Transaction_ID']!=tx[1]['Transaction_ID']
    assert tx[0]['Trade_Date']=='2026-10-30' and tx[0]['Price']==2.5


def test_manual_buy_infers_price_and_records_that_fact():
    result,tx=sb.apply_buys(template(),[buy(nav=0)],'2026-10-30')
    assert tx[0]['Price']==2.5 and '推算' in tx[0]['Note']
    assert result.iloc[-1].Shares==18466


def test_deposit_preserves_asset_ncf_and_changes_cash_external_ncf():
    original=template()
    after_deposit,deposit_tx=sb.apply_deposit(original,1000,'2026-10-30')
    after_buy,buy_tx=sb.apply_buys(after_deposit,[buy()],'2026-10-30')
    assert after_buy.iloc[-1].Shares==after_buy.iloc[-1].Total_Value==19466
    assert after_buy.iloc[-1].Net_Cash_Flow==1000
    assert after_buy.iloc[0].Net_Cash_Flow==200
    assert deposit_tx[0]['Type']=='入金' and buy_tx[0]['Type']=='买入'


def test_initial_deposit_creates_cash_in_same_units():
    empty=template().iloc[:0]
    result,tx=sb.apply_deposit(empty,10000,'2026-10-30')
    assert result.iloc[0].Shares==result.iloc[0].Total_Value==10000
    assert result.iloc[0].Current_Price==1


def test_weekend_deposit_booked_on_previous_friday_without_changing_cash():
    existing=template();existing.insert(0,'Date','2026-10-30')
    draft,tx=sb.apply_deposit(template(),5000,'2026-11-06',trade_date='2026-11-08')
    saved,trades=sb.prepare_snapshot(draft,'2026-11-06',existing,tx,today=date(2026,11,9))
    assert trades.iloc[0].Trade_Date=='2026-11-06'
    assert '原日期2026-11-08' in trades.iloc[0].Note
    assert saved.iloc[-1].Total_Value==23666
    assert tx[0]['Trade_Date']=='2026-11-08'


def test_previous_week_trade_cannot_be_mixed_into_friday_snapshot():
    existing=template();existing.insert(0,'Date','2026-10-30')
    with pytest.raises(ValueError,match='同一周'):
        sb.prepare_snapshot(template(),'2026-11-06',existing,[dict(Trade_Date='2026-10-29')],today=date(2026,11,9))


@pytest.mark.parametrize('changes',[dict(amount=30000),dict(shares=0),dict(amount=float('nan')),dict(nav=-1)])
def test_invalid_buy_preserves_original_draft(changes):
    original=template();before=original.copy()
    with pytest.raises(ValueError): sb.apply_buys(original,[buy(**changes)],'2026-10-30')
    pd.testing.assert_frame_equal(original,before)


def test_future_date_and_duplicate_snapshot_rejected():
    existing=template();existing.insert(0,'Date','2026-10-30')
    with pytest.raises(ValueError,match='未来'):
        sb.prepare_snapshot(template(),'2026-11-06',existing,[],today=date(2026,11,4))
    with pytest.raises(ValueError,match='重复'):
        sb.prepare_snapshot(template(),'2026-10-30',existing,[],today=date(2026,11,4))
    with pytest.raises(ValueError,match='早于本期'):
        sb.prepare_snapshot(template(),'2026-11-01',existing,[dict(Trade_Date='2026-11-02')],today=date(2026,11,4))


def test_save_appends_snapshot_and_transactions_with_separate_trade_date(tmp_path):
    existing=template();existing.insert(0,'Date','2026-10-24')
    existing.to_csv(tmp_path/'portfolio.csv',index=False)
    draft,tx=sb.apply_buys(template(),[buy(trade_date='2026-10-29')],'2026-10-30')
    signature=sb.frame_signature(existing)
    sb.save_update(tmp_path,draft,'2026-10-30',tx,signature)
    portfolio=pd.read_csv(tmp_path/'portfolio.csv',dtype={'Code':str})
    trades=pd.read_csv(tmp_path/'transaction.csv',dtype={'Code':str})
    assert len(portfolio)==4 and len(trades)==1
    assert trades.iloc[0].Date=='2026-10-30' and trades.iloc[0].Trade_Date=='2026-10-29'
    cash=portfolio[(portfolio.Date=='2026-10-30') & (portfolio.Code=='CASH')].iloc[0]
    assert cash.Shares==cash.Total_Value==18466
    assert list((tmp_path/'backups').glob('portfolio_*.csv'))


def test_failed_second_write_restores_original_both_files(tmp_path,monkeypatch):
    existing=template();existing.insert(0,'Date','2026-10-24')
    existing.to_csv(tmp_path/'portfolio.csv',index=False)
    (tmp_path/'transaction.csv').write_text('Date,Code,Type,Amount_CNY\n')
    originals={p.name:p.read_bytes() for p in tmp_path.glob('*.csv')}
    real_write=sb._atomic_write_csv
    real_replace=sb.os.replace
    def same_filesystem_restore(source,destination):
        if str(destination).endswith('.csv') and '.snapshot_restore_' in str(source):
            assert Path(source).parent.parent==Path(destination).parent
        return real_replace(source,destination)
    monkeypatch.setattr(sb.os,'replace',same_filesystem_restore)
    def fail_second(df,path):
        if str(path).endswith('portfolio.csv'):raise OSError('storage error')
        return real_write(df,path)
    monkeypatch.setattr(sb,'_atomic_write_csv',fail_second)
    draft,tx=sb.apply_buys(template(),[buy()],'2026-10-30')
    with pytest.raises(OSError):sb.save_update(tmp_path,draft,'2026-10-30',tx,sb.frame_signature(existing))
    assert originals=={p.name:p.read_bytes() for p in tmp_path.glob('*.csv')}
    assert not list(tmp_path.glob('.snapshot_restore_*'))


def test_stale_draft_rejected_before_any_write(tmp_path):
    original=template();original.insert(0,'Date','2026-10-24')
    changed=original.copy();changed.loc[0,'Total_Value']=999
    changed.to_csv(tmp_path/'portfolio.csv',index=False)
    before=(tmp_path/'portfolio.csv').read_bytes()
    with pytest.raises(ValueError,match='数据已变化'):
        sb.save_update(tmp_path,template(),'2026-10-30',[],sb.frame_signature(original))
    assert (tmp_path/'portfolio.csv').read_bytes()==before


def test_history_cash_normalization_does_not_change_value_or_ncf():
    first=template(19666);first.insert(0,'Date','2026-10-30')
    last=template();last.insert(0,'Date','2026-11-06')
    original=pd.concat([first,last],ignore_index=True)
    repaired=sb.normalize_cash(original)
    repaired['Date']=repaired.Date.replace({'2026-10-30':'2026-10-24','2026-11-06':'2026-10-30'})
    assert repaired.Date.unique().tolist()==['2026-10-24','2026-10-30']
    pd.testing.assert_series_equal(original.Total_Value,repaired.Total_Value)
    pd.testing.assert_series_equal(original.Net_Cash_Flow,repaired.Net_Cash_Flow)
    assert repaired[repaired.Code=='CASH'].Shares.tolist()==[19666.,18666.]


def test_wrong_asset_value_and_nonfinite_ncf_cannot_be_saved():
    wrong=template();wrong.loc[0,'Total_Value']=0
    with pytest.raises(ValueError,match='重算市值'):
        sb.prepare_snapshot(wrong,'2026-10-30',pd.DataFrame(),[])
    wrong=template();wrong.loc[0,'Net_Cash_Flow']=float('nan')
    with pytest.raises(ValueError,match='NCF'):
        sb.prepare_snapshot(wrong,'2026-10-30',pd.DataFrame(),[])


def test_empty_header_account_can_save_first_snapshot(tmp_path):
    empty=template().iloc[:0]
    empty.assign(Date=pd.Series(dtype=str)).to_csv(tmp_path/'portfolio.csv',index=False)
    draft,tx=sb.apply_deposit(empty,10000,'2026-10-30')
    sb.save_update(tmp_path,draft,'2026-10-30',tx,sb.frame_signature(pd.DataFrame()))
    assert len(pd.read_csv(tmp_path/'portfolio.csv'))==1
    assert len(pd.read_csv(tmp_path/'transaction.csv'))==1
