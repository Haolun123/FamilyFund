"""Son Fund draft bookkeeping: cash units, real trades and snapshot saving."""
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from decimal import Decimal
import fcntl
import hashlib
import os
import shutil
import tempfile
import uuid

import pandas as pd
from nav_engine import _atomic_write_csv


def _number(value, label, positive=False):
    try:
        result=Decimal(str(value))
    except Exception as exc:
        raise ValueError(f'{label}必须为有效数字') from exc
    if not result.is_finite() or result<0 or (positive and result==0):
        raise ValueError(f'{label}必须为有效正数' if positive else f'{label}不能为负数或无效值')
    return float(result)


def today_beijing():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def cash_rows(frame):
    return frame['Code'].fillna('').astype(str).eq('CASH') | frame['Asset_Class'].eq('Cash')


def normalize_cash(frame):
    """Total_Value is the balance; never restore cash from stale Shares."""
    result=frame.copy()
    if result.empty: return result
    mask=cash_rows(result)
    for i in result[mask].index:
        balance=_number(result.at[i,'Total_Value'],'现金余额')
        for key,value in dict(Shares=balance,Current_Price=1.,Exchange_Rate=1.,
                              Currency='CNY',Code='CASH',Asset_Class='Cash').items():
            result.at[i,key]=value
    return result


def frame_signature(frame):
    if frame.empty: return hashlib.sha256(b'empty-account').hexdigest()
    return hashlib.sha256(frame.to_json(orient='split',double_precision=10).encode()).hexdigest()


def apply_buys(template, purchases, snapshot_date):
    result=normalize_cash(template); transactions=[]; total=0.
    for purchase in purchases:
        code=str(purchase.get('code','')).strip(); name=str(purchase.get('name','')).strip()
        if not code or not name or code=='CASH': raise ValueError('买入须填写标的代码及名称')
        amount=_number(purchase.get('amount'),'买入金额',True)
        shares=_number(purchase.get('shares'),'买入份额',True)
        nav=purchase.get('nav')
        inferred=not nav or nav==0
        price=amount/shares if inferred else _number(nav,'成交净值',True)
        matched=result['Code'].astype(str).eq(code)
        if matched.any():
            i=result[matched].index[0]
            if result.at[i,'Currency']!='CNY': raise ValueError('子基金手动买入目前仅支持人民币报价')
            result.at[i,'Shares']=round(_number(result.at[i,'Shares'],'持仓份额')+shares,6)
            result.at[i,'Net_Cash_Flow']=round(float(result.at[i,'Net_Cash_Flow'])+amount,2)
        else:
            result=pd.concat([result,pd.DataFrame([dict(Asset_Class=purchase.get('asset_class','US_Growth_Fund'),
                Platform=purchase.get('platform','纳指场外'),Name=name,Code=code,Currency='CNY',Exchange_Rate=1.,
                Shares=shares,Current_Price=price,Total_Value=0.,Net_Cash_Flow=amount)])],ignore_index=True)
            i=result.index[-1]
        result.at[i,'Current_Price']=price
        result.at[i,'Total_Value']=round(float(result.at[i,'Shares'])*price*float(result.at[i,'Exchange_Rate']),2)
        row=result.loc[i]
        transactions.append(dict(Date=str(snapshot_date),Trade_Date=str(purchase.get('trade_date',snapshot_date)),
            Asset_Class=row['Asset_Class'],Platform=row['Platform'],Name=row['Name'],Code=code,Type='买入',
            Amount_CNY=amount,Price=price,Price_Currency='CNY',Fee_CNY=0.,Shares=shares,
            Note=purchase.get('note','手动登记')+('；价格按金额/份额推算' if inferred else ''),
            Transaction_ID=uuid.uuid4().hex))
        total+=amount
    cash=cash_rows(result)
    if cash.sum()>1: raise ValueError('子基金只能有一行现金池')
    if cash.any():
        i=result[cash].index[0]
        balance=round(float(result.at[i,'Total_Value'])-total,2)
        if balance<0: raise ValueError('买入金额超过子基金现金余额，请先核对入金')
        result.at[i,'Total_Value']=balance
    return normalize_cash(result),transactions


def apply_deposit(template, amount, snapshot_date, trade_date=None):
    amount=_number(amount,'入金金额',True); result=normalize_cash(template)
    cash=cash_rows(result)
    if cash.sum()>1: raise ValueError('子基金只能有一行现金池')
    if cash.any():
        i=result[cash].index[0]
        result.at[i,'Total_Value']=round(float(result.at[i,'Total_Value'])+amount,2)
        result.at[i,'Net_Cash_Flow']=round(float(result.at[i,'Net_Cash_Flow'])+amount,2)
    else:
        result=pd.concat([result,pd.DataFrame([dict(Asset_Class='Cash',Platform='专属账户',Name='Son Fund现金',
            Code='CASH',Currency='CNY',Exchange_Rate=1.,Shares=amount,Current_Price=1.,
            Total_Value=amount,Net_Cash_Flow=amount)])],ignore_index=True)
        i=result.index[-1]
    row=result.loc[i]
    transaction=dict(Date=str(snapshot_date),Trade_Date=str(trade_date or snapshot_date),Asset_Class='Cash',
        Platform=row['Platform'],Name=row['Name'],Code='CASH',Type='入金',Amount_CNY=amount,Price=1.,
        Price_Currency='CNY',Fee_CNY=0.,Shares=amount,Note='外部入金',Transaction_ID=uuid.uuid4().hex)
    return normalize_cash(result),[transaction]


def prepare_snapshot(template, day, existing, transactions, today=None):
    day=date.fromisoformat(str(day)); today=today or today_beijing()
    if day>today: raise ValueError('快照日期不能在未来，请选择实际估值日期')
    if not existing.empty and str(day)<=str(existing.Date.max()):
        raise ValueError('快照日期须晚于已有快照，不能重复或倒序追加')
    saved=normalize_cash(template)
    if saved.empty: raise ValueError('快照不能为空')
    if saved['Code'].fillna('').astype(str).duplicated().any(): raise ValueError('快照标的代码不能重复')
    if saved['Code'].fillna('').astype(str).eq('').any(): raise ValueError('持仓代码不能为空')
    if cash_rows(saved).sum()>1: raise ValueError('子基金只能有一行现金池')
    for _,row in saved.iterrows():
        _number(row['Shares'],'持仓份额'); _number(row['Total_Value'],'市值')
        _number(row['Exchange_Rate'],'汇率',True)
        try:
            if not Decimal(str(row['Net_Cash_Flow'])).is_finite(): raise ValueError()
        except Exception as exc:
            raise ValueError('NCF必须为有效数字') from exc
        if row['Code']!='CASH' and float(row['Shares'])>0:
            _number(row['Current_Price'],'价格／净值',True)
            expected=round(float(row['Shares'])*float(row['Current_Price'])*float(row['Exchange_Rate']),2)
            if abs(expected-float(row['Total_Value']))>.01:
                raise ValueError('持仓市值与份额×价格×汇率不一致，请点击重算市值')
    saved.insert(0,'Date',str(day))
    tx=[dict(t,Date=str(day)) for t in transactions]
    for t in tx:
        if date.fromisoformat(t['Trade_Date'])>day: raise ValueError('快照日期不能早于本期成交或入金日期')
    return saved,pd.DataFrame(tx)


def save_update(data_dir, template, day, transactions, expected_signature):
    """Lock the account, back up originals, write both files and roll back errors.

    Each CSV uses the existing atomic writer. This is not a crash-proof multi-file
    transaction; caught storage errors restore both original files.
    """
    root=Path(data_dir); root.mkdir(parents=True,exist_ok=True)
    portfolio=root/'portfolio.csv'; tx_path=root/'transaction.csv'
    with (root/'snapshot_save.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        existing=pd.read_csv(portfolio,dtype={'Code':str}) if portfolio.exists() else pd.DataFrame()
        if frame_signature(existing)!=expected_signature:
            raise ValueError('子基金数据已变化，请重置草稿后重新核对')
        saved,tx=prepare_snapshot(template,day,existing,transactions)
        combined=pd.concat([existing,saved],ignore_index=True) if not existing.empty else saved
        originals={}
        audit_backup=Path(tempfile.mkdtemp(prefix='son_snapshot_original_'))
        # Restoration must rename within the same mounted filesystem.
        backup_root=Path(tempfile.mkdtemp(prefix='.snapshot_restore_',dir=root))
        for path in [portfolio,tx_path]:
            if path.exists():
                target=backup_root/path.name; shutil.copy2(path,target); originals[path]=target
                shutil.copy2(path,audit_backup/path.name)
            else: originals[path]=None
        try:
            if not tx.empty:
                old=pd.read_csv(tx_path,dtype={'Code':str}) if tx_path.exists() else pd.DataFrame()
                if 'Transaction_ID' in old.columns and set(old.Transaction_ID.dropna()) & set(tx.Transaction_ID):
                    raise ValueError('这些流水已经存在，请核对保存状态，不能重复写入')
                if not old.empty: tx=pd.concat([old,tx],ignore_index=True)
                _atomic_write_csv(tx,str(tx_path))
            _atomic_write_csv(combined,str(portfolio))
            check=pd.read_csv(portfolio,dtype={'Code':str})
            if len(check)!=len(existing)+len(saved): raise OSError('快照保存后行数校验失败')
        except Exception:
            for path,backup in originals.items():
                if backup is not None: os.replace(backup,path)
                elif path.exists(): path.unlink()
            raise
        finally:
            shutil.rmtree(backup_root)
    return len(saved),len(transactions)
