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


def test_trade_saves_immediately_and_views_render(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch,initialized=True)
    widget(at.selectbox,'标的').select('002277').run()
    widget(at.number_input,'人民币实际收付金额（买入含费扣款／卖出净到账）').set_value(505)
    widget(at.number_input,'费用合计（人民币，已含在实际收付中）').set_value(5)
    widget(at.text_input,'成交编号（券商编号或唯一自编号）').set_value('UI-T1')
    widget(at.button,'登记成交并更新持仓').click().run()
    assert not at.exception
    s=replay(read_events(tmp_path/'father'))
    assert s['cash']==9495 and s['positions']['002277']['quantity']==200
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
