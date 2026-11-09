"""Independent three-fund reporting UI; account workflows remain separate."""
import os
from pathlib import Path
from datetime import timedelta
import pandas as pd
import streamlit as st
from three_fund_report import (ReportError, today_beijing, week_friday, period,
    account_paths, source_versions, date_rows, save_date_rows, build_report,
    save_report, sync_saved_report)


def render_three_fund_report(data_dir):
    st.header('三基金汇总报表')
    st.caption('按周五结账；可选择一周或多周。缺少快照、跨周流水或已知过期报价会阻止生成。历史报价日期未知会在报告中注明。')
    today=today_beijing()
    latest=week_friday(today)
    if latest>today:
        latest-=timedelta(days=7)
    left,right=st.columns(2)
    start=left.date_input('起始周（选择该周任意日期）',value=latest-timedelta(days=4),max_value=today,key='tfr_start')
    end=right.date_input('结束周（选择该周任意日期）',value=latest,max_value=today,key='tfr_end')
    son=os.environ.get('FAMILYFUND_SON_DATA')
    father=os.environ.get('FAMILYFUND_FATHER_DATA')
    paths=account_paths(data_dir,son,father)
    vault=os.environ.get('OBSIDIAN_VAULT_PATH',str(Path.home()/'Library/Mobile Documents/iCloud~md~obsidian/Documents/vault'))
    try:
        bounds=period(start,end)
    except ReportError as exc:
        st.error(str(exc));return
    st.info(f'统计周：{bounds["start"]}至{bounds["end"]}（周五）；一般期初基准：{bounds["baseline"]}。中途成立的基金单独显示成立期初。')
    with st.expander('核对报价日期／节假日估值日',expanded=False):
        st.caption('日期填写YYYY-MM-DD，未知留空。默认期望日为快照日；休市或非每日净值产品须明确填期望日及理由。这里仅保存绑定价格的日期证据，不修改持仓。')
        if st.button('读取本区间估值日期',key='tfr_dates_read'):
            try:
                versions=source_versions(paths)
                records=date_rows(paths,start,end)
                if source_versions(paths)!=versions:
                    raise ReportError('读取时数据已变化，请重试')
                st.session_state['tfr_dates']=(bounds,versions,records)
            except (ReportError,OSError,ValueError) as exc:
                st.error(str(exc))
        stored=st.session_state.get('tfr_dates')
        if stored and stored[0]==bounds and not stored[2].empty:
            frame=stored[2]
            selected=st.selectbox('核对快照日期',sorted(frame.Date.unique()),key='tfr_dates_day')
            subset=frame[frame.Date==selected]
            edited=st.data_editor(subset,hide_index=True,use_container_width=True,
                disabled=['Fund','Date','Code','Name','Price','FX'],key=f'tfr_dates_editor_{selected}_{hash(str(stored[1]))}')
            if st.button('保存这期日期证据',key='tfr_dates_save'):
                try:
                    save_date_rows(paths,edited,stored[1])
                    st.session_state.pop('tfr_preview',None)
                    st.session_state.pop('tfr_dates',None)
                    st.success('日期证据已保存，请重新校验报告。')
                except (ReportError,OSError,ValueError) as exc:
                    st.error('日期证据保存失败：'+str(exc))
        elif stored and stored[0]==bounds:
            st.info('本区间没有可补充日期的持仓；请先完成账户快照。')
    if st.button('校验并预览报告',type='primary',key='tfr_preview_button'):
        st.session_state.pop('tfr_preview',None)
        try:
            with st.spinner('读取三个账本并逐周校验…'):
                result=build_report(data_dir,start,end,son,father)
            st.session_state['tfr_preview']=result
        except ReportError as exc:
            for issue in exc.issues:
                st.error(issue)
        except (OSError,ValueError) as exc:
            st.error('报告读取失败：'+str(exc))
    result=st.session_state.get('tfr_preview')
    if result and (result['bounds']!=bounds or source_versions(paths)!=result['source_versions']):
        st.warning('日期或账户数据已变化，请重新校验。')
        result=None
        st.session_state.pop('tfr_preview',None)
    if result:
        st.success('三个基金的周快照与时间边界校验通过。未知历史报价日期将在正文中注明。')
        st.dataframe(pd.DataFrame([dict(基金=f['label'],实际期初=f['baseline'],期末=f['end'],
            期末资产=f['total'],外部净入金=f['external'],投资损益=f['pnl'],区间收益率=f['return_pct'])
            for f in result['funds']]),hide_index=True,use_container_width=True)
        with st.expander('查看报告全文',expanded=False):
            st.markdown(result['markdown'])
        if st.button('生成报告并保存到iCloud和Obsidian',type='primary',key='tfr_generate'):
            try:
                status=save_report(result,data_dir,vault,son,father)
                st.session_state['tfr_saved']=status
            except (ReportError,OSError,ValueError) as exc:
                st.error('生成失败：'+str(exc))
    status=st.session_state.get('tfr_saved')
    if status:
        st.success('iCloud报告已生成：'+status['filename'])
        if status['synced']:
            st.success('Obsidian同名报告已同步。')
        else:
            st.error(status.get('error','Obsidian同步失败'))
            if st.button('重试同步同一份报告',key='tfr_sync_retry'):
                new=sync_saved_report(data_dir,status['filename'],vault)
                st.session_state['tfr_saved']={**status,**new}
                st.rerun()
        report=Path(data_dir)/'weekly_summaries'/status['filename']
        if report.exists():
            st.download_button('下载Markdown报告',report.read_bytes(),file_name=status['filename'],mime='text/markdown',key='tfr_download')
