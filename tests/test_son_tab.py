import ast
from datetime import date
from pathlib import Path
import sys
import pandas as pd
import pytest

pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def widget(collection,label):
    return next(w for w in collection if w.label==label)


def app(tmp_path,monkeypatch):
    project=Path(__file__).resolve().parents[1]
    monkeypatch.setenv('FAMILYFUND_SON_DATA',str(tmp_path))
    pd.DataFrame([dict(Date='2026-10-24',Asset_Class='US_Growth_Fund',Platform='场外基金',Name='南方纳指100 I类',
        Code='021000',Currency='CNY',Exchange_Rate=1.,Shares=100.,Current_Price=2.,Total_Value=200.,Net_Cash_Flow=200.),
        dict(Date='2026-10-24',Asset_Class='Cash',Platform='专属账户',Name='Son Fund现金',Code='CASH',
             Currency='CNY',Exchange_Rate=1.,Shares=22066.,Current_Price=1.,Total_Value=18666.,Net_Cash_Flow=18666.)
    ]).to_csv(tmp_path/'portfolio.csv',index=False)
    tree=ast.parse((project/'dashboard/app.py').read_text())
    son=next(n for n in ast.walk(tree) if isinstance(n,ast.With) and any(
        isinstance(item.context_expr,ast.Name) and item.context_expr.id=='tab_son' for item in n.items))
    source='\n'.join(ast.unparse(n) for n in son.body)
    prelude=f'''import sys, os
sys.path.insert(0,{str(project/'src')!r})
import streamlit as st
import pandas as pd
import plotly.express as px
from nav_engine import *
csv_path={str(tmp_path/'main_portfolio.csv')!r}
'''
    at=AppTest.from_string(prelude+source,default_timeout=20).run()
    assert not at.exception
    widget(at.date_input,'本次快照日期（请选实际估值日期）').set_value(date(2026,10,30)).run()
    return at


def test_manual_buy_stages_real_transaction_with_cash_units(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch)
    widget(at.button,'＋ 买入').click().run()
    widget(at.text_input,'Code').set_value('021000')
    widget(at.text_input,'Name').set_value('南方纳指100 I类')
    widget(at.number_input,'金额(¥)').set_value(200.)
    widget(at.number_input,'份额').set_value(80.)
    widget(at.date_input,'成交确认日期').set_value(date(2026,10,29))
    widget(at.button,'✅ 应用买入').click().run()
    assert not at.exception
    assert len(at.session_state['son_pending_transactions'])==1
    assert not (tmp_path/'transaction.csv').exists()
    cash=at.session_state['son_update_template'].iloc[-1]
    assert cash.Shares==cash.Total_Value==18466
    assert at.session_state['son_pending_transactions'][0]['Trade_Date']=='2026-10-29'


def test_sms_application_creates_pending_transactions(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch)
    at.session_state['son_sms_parsed']=[dict(matched_code='021000',matched_name='南方纳指100 I类',
        confirm_date='2026-10-29',fund_name='南方纳指',shares=80.,amount=200.,nav=2.5)]
    at.run()
    widget(at.button,'✅ 应用解析结果').click().run()
    assert not at.exception
    assert len(at.session_state['son_pending_transactions'])==1
    assert at.session_state['son_update_template'].iloc[-1].Shares==18466
    widget(at.checkbox,'确认子基金数据无误').check().run()
    widget(at.button,'💾 保存子基金快照').click().run()
    assert not at.exception and len(pd.read_csv(tmp_path/'transaction.csv'))==1


def test_deposit_creates_cash_flow_record_and_reset_preserves_saved_account(tmp_path,monkeypatch):
    at=app(tmp_path,monkeypatch)
    before=(tmp_path/'portfolio.csv').read_bytes()
    widget(at.number_input,'本周打入专属账户金额（¥）').set_value(1000.).run()
    widget(at.button,'✅ 登记入金').click().run()
    assert not at.exception
    assert at.session_state['son_pending_transactions'][0]['Type']=='入金'
    assert at.session_state['son_update_template'].iloc[-1].Shares==19666
    widget(at.button,'重置子基金本期草稿').click().run()
    assert not at.exception
    assert before==(tmp_path/'portfolio.csv').read_bytes()
    assert not at.session_state.filtered_state.get('son_pending_transactions')
