from pathlib import Path
import sys
import pytest

pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from father_fund import commit_events,event,read_events,replay


def widget(collection,label):
    return next(w for w in collection if w.label==label)


def app(tmp_path,monkeypatch,initialized=False):
    project=Path(__file__).resolve().parents[1]
    monkeypatch.setenv('FAMILYFUND_FATHER_DATA',str(tmp_path/'father'))
    input_path=tmp_path/'input.md'
    input_path.write_text('''## 1. 期初账户
| 字段 | 值 |
|---|---|
| 基准时间 | 2026-11-01 08:00:00 |
| 期初净现金（人民币） | 10000 |
## 2. 期初持仓
| 名称 | 代码 | 类别 | 币种 | 数量 | 期初价格 | 折算汇率 |
|---|---|---|---|---|---|---|
| 友阿股份 | 002277 | 权益 | CNY | 100 | 5 | 1 |
''')
    monkeypatch.setenv('FAMILYFUND_FATHER_INPUT',str(input_path))
    if initialized:
        commit_events(tmp_path/'father',[event('OPEN','2026-11-01',dict(cash='10000',positions=[dict(code='002277',name='友阿股份',bucket='权益',currency='CNY',quantity='100',price='5',fx='1')]))])
    source=f"import sys\nsys.path.insert(0,{str(project/'src')!r})\nsys.path.insert(0,{str(project/'dashboard')!r})\nfrom father_tab import render_father_fund\nrender_father_fund({str(tmp_path)!r})"
    at=AppTest.from_string(source,default_timeout=20).run()
    assert not at.exception
    return at


def test_initialization_preview_then_single_commit(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch)
    widget(at.button,'预览期初与交易后的持仓').click().run()
    assert not at.exception and not (tmp_path/'father/ledger.csv').exists()
    widget(at.checkbox,'确认期初与成交范围正确，知道对账差异尚需解释').check().run()
    widget(at.button,'建立父亲账户').click().run()
    assert not at.exception
    assert replay(read_events(tmp_path/'father'))['cash']==10000
    assert len(read_events(tmp_path/'father'))==1


def test_trade_stages_until_snapshot_and_views_render(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch,initialized=True)
    widget(at.selectbox,'标的').select('002277').run()
    widget(at.number_input,'人民币实际收付金额（买入含费扣款／卖出净到账）').set_value(505)
    widget(at.number_input,'费用合计（人民币，已含在实际收付中）').set_value(5)
    widget(at.text_input,'成交编号（券商编号或唯一自编号）').set_value('UI-T1')
    widget(at.button,'登记成交并更新持仓').click().run()
    assert not at.exception
    s=replay(read_events(tmp_path/'father'))
    assert s['cash']==10000 and s['positions']['002277']['quantity']==100
    assert len(at.session_state['ff_pending'])==1
    widget(at.checkbox,'已核对本期交易、价格和汇率；接受明确标记的旧报价或估算值').check().run()
    widget(at.button,'保存父亲账户快照').click().run()
    assert not at.exception
    s=replay(read_events(tmp_path/'father'))
    assert s['cash']==9495 and s['positions']['002277']['quantity']==200
    assert len(read_events(tmp_path/'father'))==3
    for view in ['资金变动','估值更新','调整计划','交易流水与纠错','券商对账']:
        widget(at.radio,'账户操作').set_value(view).run()
        assert not at.exception,view


def test_missing_actual_cash_and_bad_date_do_not_save(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch,initialized=True)
    widget(at.selectbox,'标的').select('002277').run()
    widget(at.text_input,'成交时间（北京时间）').set_value('not-a-date')
    widget(at.button,'登记成交并更新持仓').click().run()
    assert not at.exception and len(read_events(tmp_path/'father'))==1
    assert at.error


def test_csv_repeat_application_and_distinct_fills(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch,initialized=True)
    text='2026-11-02,买入,002277,100,5,500'
    widget(at.text_area,'买入／卖出CSV（每行一笔）').set_value(text).run()
    widget(at.button,'解析交易CSV').click().run()
    widget(at.button,'应用交易到本期持仓').click().run()
    assert not at.exception and len(read_events(tmp_path/'father'))==1
    assert len(at.session_state['ff_pending'])==1
    assert widget(at.button,'应用交易到本期持仓').disabled
    # Parsing again creates a new batch, requiring explicit true-fill confirmation.
    widget(at.button,'解析交易CSV').click().run()
    assert widget(at.button,'应用交易到本期持仓').disabled
    widget(at.checkbox,'这些是另外发生的真实成交，确认新增').check().run()
    widget(at.button,'应用交易到本期持仓').click().run()
    assert len(at.session_state['ff_pending'])==2 and not at.exception
    widget(at.checkbox,'已核对本期交易、价格和汇率；接受明确标记的旧报价或估算值').check().run()
    widget(at.button,'保存父亲账户快照').click().run()
    assert not at.exception and len(read_events(tmp_path/'father'))==4
    assert replay(read_events(tmp_path/'father'))['positions']['002277']['quantity']==300
    assert not widget(at.checkbox,'已核对本期交易、价格和汇率；接受明确标记的旧报价或估算值').value


def tab_module(monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dashboard'))
    import father_tab
    return father_tab


def test_refresh_success_and_failure_manual_fallback(tmp_path,monkeypatch):
    tab=tab_module(monkeypatch)
    monkeypatch.setattr(tab,'fetch_quote',lambda m:dict(status='ok',price=6,date='2026-11-02'))
    at=app(tmp_path,monkeypatch,initialized=True)
    widget(at.button,'刷新价格／净值（拉取一次）').click().run()
    assert not at.exception and at.session_state['ff_values']['002277']['price']==6
    assert replay(read_events(tmp_path/'father'))['market']==500
    monkeypatch.setattr(tab,'fetch_quote',lambda m:dict(status='error',price=None))
    widget(at.button,'刷新价格／净值（拉取一次）').click().run()
    assert not at.exception and at.session_state['ff_values']['002277']['price']==6
    widget(at.number_input,'手动价格／净值').set_value(7).run()
    widget(at.button,'确认手动价格').click().run()
    assert not at.exception and at.session_state['ff_values']['002277']['price']==7
    widget(at.checkbox,'已核对本期交易、价格和汇率；接受明确标记的旧报价或估算值').check().run()
    widget(at.button,'保存父亲账户快照').click().run()
    assert not at.exception and replay(read_events(tmp_path/'father'))['market']==700


def test_new_instrument_repeated_rows_verified_then_manual_price(tmp_path,monkeypatch):
    from father_workflow import load_registry
    tab=tab_module(monkeypatch)
    def verify(m):
        return dict(m,validation_status='confirmed',api_name='平安银行'),dict(status='error',price=None)
    monkeypatch.setattr(tab,'verify_instrument',verify)
    at=app(tmp_path,monkeypatch,initialized=True)
    text='2026-11-02,买入,000001,100,10,1000'
    widget(at.text_area,'买入／卖出CSV（每行一笔）').set_value(text+'\n'+text).run()
    widget(at.button,'解析交易CSV').click().run()
    assert not at.exception
    widget(at.text_input,'标的名称').set_value('平安银行')
    widget(at.button,'查询标的并拉取一次价格').click().run()
    assert not at.exception
    assert load_registry(tmp_path/'father')['000001']['validation_status']=='pending'
    widget(at.number_input,'新标的手动价格／净值').set_value(10).run()
    widget(at.checkbox,'确认API返回的是目标标的').check().run()
    widget(at.button,'确认并保存标的信息').click().run()
    assert not at.exception
    assert load_registry(tmp_path/'father')['000001']['validation_status']=='confirmed'
    assert len(read_events(tmp_path/'father'))==1
    widget(at.button,'应用交易到本期持仓').click().run()
    assert not at.exception and len(at.session_state['ff_pending'])==2
    assert len(read_events(tmp_path/'father'))==1


def test_cash_only_account_can_save_in_plan_view(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch)
    commit_events(tmp_path/'father',[event('OPEN','2026-11-01',dict(cash='10000',positions=[]))])
    at.run()
    widget(at.radio,'账户操作').set_value('调整计划').run()
    assert not at.exception
    widget(at.checkbox,'已核对本期交易、价格和汇率；接受明确标记的旧报价或估算值').check().run()
    widget(at.button,'保存父亲账户快照').click().run()
    assert not at.exception and len(read_events(tmp_path/'father'))==2


def test_failed_snapshot_save_preserves_draft(tmp_path,monkeypatch):
    tab=tab_module(monkeypatch)
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(tab,'save_snapshot',fail)
    at=app(tmp_path,monkeypatch,initialized=True)
    widget(at.checkbox,'已核对本期交易、价格和汇率；接受明确标记的旧报价或估算值').check().run()
    widget(at.button,'保存父亲账户快照').click().run()
    assert not at.exception and at.error
    assert len(read_events(tmp_path/'father'))==1
