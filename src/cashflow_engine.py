"""
鲨鱼记账解析 + 季度现金流分析引擎。

数据源:
  - $FAMILYFUND_DATA/鲨鱼记账/<季度>.csv (UTF-16, Tab分隔)
  - $FAMILYFUND_DATA/cashflow_log.csv (鲨鱼之外的特殊现金流)
  - $FAMILYFUND_DATA/balance_sheet.csv (季末资产负债)

核心 KPI(2026-06-30 与用户确认):
  - 总收入
  - 净储蓄率 = 净储蓄 / 总收入
  - 自由现金流 = 总收入 - 总支出(含债务还本)
  - 必需支出占比 = 必需支出 / 总支出

关于"债务还本":
  - Q2 起鲨鱼记账新增"债务还本"一级分类,只录入本金部分
  - 利息部分在"贷款"分类(如有)或"居家"等其他分类
  - 净储蓄口径剔除"债务还本"(本金视为净资产转移,不算支出)
  - 自由现金流口径保留"债务还本"(反映真实现金流出)

关于"必需 vs 可选"分类:
  - 必需: 餐饮 / 居家 / 通讯 / 医疗 / 交通 / 孩子 / 长辈 / 阿姨 / 父母 / 公积金(支出) / 贷款利息
  - 可选: 购物 / 数码 / 旅行 / 社交 / 运动 / 其它
  - 不参与分类: 债务还本(本金,净资产口径剔除)
  - 这是默认分类,可以通过 NECESSARY_CATEGORIES 修改
"""
from __future__ import annotations

import os
import pandas as pd
from datetime import datetime


# ── 配置 ────────────────────────────────────────────────

# 必需支出类别(鲨鱼记账一级分类)
NECESSARY_CATEGORIES = {
    '餐饮', '居家', '通讯', '医疗', '交通',
    '孩子', '长辈', '阿姨', '父母',
    '日用', '贷款',  # 贷款分类视为利息支出,归必需
}

# 可选支出类别
DISCRETIONARY_CATEGORIES = {
    '购物', '数码', '旅行', '社交', '运动', '其它',
    '宠物', '娱乐',  # 2026-06-30 Q2 跑通后补充
}

# 净资产转移类别(本金还款,从支出中剔除以计算净储蓄)
DEBT_PRINCIPAL_CATEGORIES = {
    '债务还本',
}


# ── 解析 ────────────────────────────────────────────────

def parse_shark_csv(path: str) -> pd.DataFrame:
    """读取鲨鱼记账 UTF-16 Tab 分隔 CSV,返回标准化 DataFrame。

    Returns:
        DataFrame with columns: Date, Type, Category, Amount, Note
        Date 为 pd.Timestamp; Type 为 '收入' or '支出'; Amount 为 float
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f'鲨鱼记账文件不存在: {path}')

    df = pd.read_csv(path, encoding='utf-16', sep='\t')

    # 标准化列名(鲨鱼记账的原始列名是中文)
    rename = {
        '日期':   'Date',
        '收支类型': 'Type',
        '类别':   'Category',
        '金额':   'Amount',
        '备注':   'Note',
    }
    df = df.rename(columns=rename)

    # 类型转换
    df['Date'] = pd.to_datetime(df['Date'], format='%Y年%m月%d日', errors='coerce')
    df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce').fillna(0)
    df['Category'] = df['Category'].astype(str).str.strip()
    df['Type'] = df['Type'].astype(str).str.strip()
    df['Note'] = df.get('Note', '').fillna('')

    # 过滤无效行
    df = df.dropna(subset=['Date'])
    df = df[df['Type'].isin(['收入', '支出'])]

    return df.reset_index(drop=True)


def load_quarter_shark(data_dir: str, quarter: str) -> pd.DataFrame | None:
    """按季度加载鲨鱼记账数据。

    Args:
        data_dir: FAMILYFUND_DATA 目录
        quarter:  '2026Q2' 格式

    Returns:
        DataFrame 或 None(文件不存在)
    """
    path = os.path.join(data_dir, '鲨鱼记账', f'{quarter}.csv')
    if not os.path.exists(path):
        return None
    return parse_shark_csv(path)


# ── 计算 ────────────────────────────────────────────────

def categorize_expense(category: str) -> str:
    """把一级类别映射到 必需 / 可选 / 债务还本。"""
    if category in NECESSARY_CATEGORIES:
        return '必需'
    if category in DISCRETIONARY_CATEGORIES:
        return '可选'
    if category in DEBT_PRINCIPAL_CATEGORIES:
        return '债务还本'
    return '其他'  # 未归类的(理论上不应该出现)


def compute_cashflow_summary(df: pd.DataFrame) -> dict:
    """计算季度现金流核心指标。

    Args:
        df: parse_shark_csv 的返回值

    Returns:
        dict with keys:
          income_total       总收入
          expense_total      总支出(含债务还本)
          expense_necessary  必需支出
          expense_discretionary 可选支出
          debt_principal     债务还本(本金)
          free_cashflow      自由现金流 = income_total - expense_total
          net_savings        净储蓄 = income_total - expense_total + debt_principal
                             (因为本金不算"消耗",还回去等于净资产转移)
          savings_rate       净储蓄率 = net_savings / income_total
          necessary_ratio    必需支出占比 = expense_necessary / (expense_necessary + expense_discretionary)
                             (注:分母不含债务还本,反映"日常消费"中必需比例)
    """
    if df.empty:
        return {
            'income_total': 0.0,
            'expense_total': 0.0,
            'expense_necessary': 0.0,
            'expense_discretionary': 0.0,
            'debt_principal': 0.0,
            'free_cashflow': 0.0,
            'net_savings': 0.0,
            'savings_rate': 0.0,
            'necessary_ratio': 0.0,
        }

    income = df[df['Type'] == '收入']['Amount'].sum()

    expense_df = df[df['Type'] == '支出'].copy()
    expense_df['Bucket'] = expense_df['Category'].apply(categorize_expense)

    e_total = expense_df['Amount'].sum()
    e_necessary = expense_df[expense_df['Bucket'] == '必需']['Amount'].sum()
    e_discretionary = expense_df[expense_df['Bucket'] == '可选']['Amount'].sum()
    debt_principal = expense_df[expense_df['Bucket'] == '债务还本']['Amount'].sum()
    e_other = expense_df[expense_df['Bucket'] == '其他']['Amount'].sum()
    # 未归类的"其他"按必需处理(保守,不夸大可支配空间)
    e_necessary += e_other

    free_cf = income - e_total
    net_savings = income - e_total + debt_principal

    # 必需支出占比的分母不含债务还本(反映日常消费弹性)
    daily_expense = e_necessary + e_discretionary
    necessary_ratio = e_necessary / daily_expense if daily_expense > 0 else 0.0

    savings_rate = net_savings / income if income > 0 else 0.0

    return {
        'income_total':           round(float(income), 2),
        'expense_total':          round(float(e_total), 2),
        'expense_necessary':      round(float(e_necessary), 2),
        'expense_discretionary':  round(float(e_discretionary), 2),
        'debt_principal':         round(float(debt_principal), 2),
        'free_cashflow':          round(float(free_cf), 2),
        'net_savings':            round(float(net_savings), 2),
        'savings_rate':           round(float(savings_rate), 4),
        'necessary_ratio':        round(float(necessary_ratio), 4),
    }


def aggregate_by_category(df: pd.DataFrame, exp_type: str = '支出') -> pd.DataFrame:
    """按一级类别聚合金额,按金额降序。

    Args:
        df:       parse_shark_csv 返回值
        exp_type: '收入' or '支出'

    Returns:
        DataFrame with columns: Category, Amount, Count, Bucket
    """
    if df.empty or 'Type' not in df.columns:
        return pd.DataFrame(columns=['Category', 'Amount', 'Count', 'Bucket'])

    sub = df[df['Type'] == exp_type].copy()
    if sub.empty:
        return pd.DataFrame(columns=['Category', 'Amount', 'Count', 'Bucket'])

    agg = sub.groupby('Category', as_index=False).agg(
        Amount=('Amount', 'sum'),
        Count=('Amount', 'size'),
    )
    agg['Amount'] = agg['Amount'].round(2)

    if exp_type == '支出':
        agg['Bucket'] = agg['Category'].apply(categorize_expense)
    else:
        agg['Bucket'] = '收入'

    return agg.sort_values('Amount', ascending=False).reset_index(drop=True)


# ── 桑基图数据 ──────────────────────────────────────────

def build_sankey_data(df: pd.DataFrame, group_expense: bool = True) -> dict:
    """构造桑基图节点和流。

    设计:
      左侧节点 = 各收入类别(工资/分红/公积金/...)
      中间节点 = "总流入"(单一汇聚节点,让左右对称美观)
      右侧节点 = 支出/储蓄

    Args:
        df: 鲨鱼记账数据
        group_expense: 是否把右侧支出合并为 必需/可选/债务还本 三桶(默认 True,降低视觉噪音).
                       False 时退化为按一级类别展开(保留向后兼容,测试用).

    Returns:
        dict with keys:
          nodes:  list[str] 节点名称
          sources, targets, values: 流的端点和金额
          flow_amounts: dict[(source_idx, target_idx)] → 金额  (供 UI 在 link 上标注)
    """
    income_agg = aggregate_by_category(df, '收入')
    expense_agg = aggregate_by_category(df, '支出')
    summary = compute_cashflow_summary(df)

    nodes = []
    # 左侧: 收入类别
    income_names = [f'{c} 收入' for c in income_agg['Category'].tolist()]
    nodes.extend(income_names)

    # 中间: 汇聚节点
    HUB = '总流入'
    nodes.append(HUB)
    hub_idx = len(nodes) - 1

    sources, targets, values = [], [], []

    # 流: 左 → 中
    for i, (_, row) in enumerate(income_agg.iterrows()):
        sources.append(i)
        targets.append(hub_idx)
        values.append(float(row['Amount']))

    # 流: 中 → 右
    if group_expense:
        # 桶聚合模式: 必需 / 可选 / 债务还本 三桶
        necessary  = float(expense_agg.loc[expense_agg['Bucket'] == '必需',     'Amount'].sum())
        discretion = float(expense_agg.loc[expense_agg['Bucket'] == '可选',     'Amount'].sum())
        debt       = float(expense_agg.loc[expense_agg['Bucket'] == '债务还本', 'Amount'].sum())
        other      = float(expense_agg.loc[expense_agg['Bucket'] == '其他',     'Amount'].sum())
        # 未归类项默认按必需(与 compute_cashflow_summary 一致)
        necessary += other

        for label, amount in (('必需支出', necessary),
                              ('可选支出', discretion),
                              ('债务还本', debt)):
            if amount > 0:
                nodes.append(label)
                sources.append(hub_idx)
                targets.append(len(nodes) - 1)
                values.append(amount)
    else:
        # 展开模式(向后兼容): 按一级类别
        for _, row in expense_agg.iterrows():
            nodes.append(row['Category'])
            sources.append(hub_idx)
            targets.append(len(nodes) - 1)
            values.append(float(row['Amount']))

    # 流: 中 → 净储蓄
    if summary['net_savings'] > 0:
        SAVINGS = '净储蓄'
        nodes.append(SAVINGS)
        sources.append(hub_idx)
        targets.append(len(nodes) - 1)
        values.append(summary['net_savings'])
    elif summary['net_savings'] < 0:
        # 超支:桑基图不能画负数流,改用说明性节点+0值
        nodes.append('净支出(超支)')

    # flow_amounts 供 UI 用做 link label
    flow_amounts = {(s, t): v for s, t, v in zip(sources, targets, values)}

    return {
        'nodes':   nodes,
        'sources': sources,
        'targets': targets,
        'values':  [round(v, 2) for v in values],
        'flow_amounts': flow_amounts,
    }


# ── 净资产核对 ────────────────────────────────────────

# Type 枚举的分类(参考 docs/USER_MANUAL.md):
#   经营性: 影响净资产(真实新增/消耗财富)
#   资本性: 资产形态转换,不影响净资产(如卖车=车→现金)
OPERATING_INFLOW_TYPES = {'Inflow_Salary', 'Inflow_Other'}
OPERATING_OUTFLOW_TYPES = {'Outflow_Major'}
CAPITAL_TYPES = {'Capital_Inflow', 'Capital_Outflow'}


def _classify_cashflow_type(type_str: str, amount: float) -> str:
    """把 cashflow_log 的 Type 字段映射到三类:
    - 'operating_in'    经营性流入(影响净资产)
    - 'operating_out'   经营性流出(影响净资产)
    - 'capital'         资本性(不影响净资产)
    - 'unknown'         未知Type(保守按经营性处理,避免漏算)
    """
    t = (type_str or '').strip()
    if t in OPERATING_INFLOW_TYPES:
        return 'operating_in'
    if t in OPERATING_OUTFLOW_TYPES:
        return 'operating_out'
    if t in CAPITAL_TYPES:
        return 'capital'
    # 未知Type: 按金额符号回退到经营性(保守,避免漏算真实流入流出)
    return 'operating_in' if amount > 0 else 'operating_out'


def compute_net_worth_reconciliation(
    df_shark: pd.DataFrame,
    nw_prev: float,
    nw_curr: float,
    cashflow_log: pd.DataFrame | None = None,
    quarter: str | None = None,
    portfolio_df: pd.DataFrame | None = None,
    q_prev_end: str | None = None,
    q_curr_end: str | None = None,
) -> dict:
    """净资产核对公式(2026-07-01 修正: 区分经营性 vs 资本性 + 补 SAP 薪酬):

    期末净资产 ≈ 期初净资产
               + 鲨鱼收入合计                            (经营性流入)
               - 鲨鱼支出合计(剔除"债务还本")             (经营性流出)
               + cashflow_log 经营性流入(Inflow_*)        (鲨鱼外的经营性)
               - cashflow_log 经营性流出(Outflow_Major)
               + Company_Stock NCF (ESPP+RSU 薪酬)        (SAP 归属,不经过银行账户)
               ± 资产估值变化(残差)
               ─── 不含 Capital_Inflow/Outflow(资产形态转换不影响净资产)───

    Args:
        df_shark:     当季鲨鱼记账数据
        nw_prev:      期初净资产(CNY)
        nw_curr:      期末净资产(CNY)
        cashflow_log: cashflow_log.csv 数据(可选)
        quarter:      要过滤的季度,如 '2026Q2'
        portfolio_df: portfolio.csv 数据(可选,用于提取 Company_Stock NCF)
        q_prev_end:   上一季末日期 'YYYY-MM-DD'(用于 NCF 区间过滤)
        q_curr_end:   当季末日期 'YYYY-MM-DD'

    Returns:
        dict with keys:
          nw_prev, nw_curr, nw_change
          shark_income, shark_expense_ex_debt
          operating_inflow      鲨鱼外经营性流入(工资奖金/补贴/分红等)
          operating_outflow     鲨鱼外经营性流出(基金外大额支出)
          sap_vesting           SAP 归属薪酬(Company_Stock NCF,ESPP+RSU)
          capital_inflow        资本性流入(卖车/资产变现,不计入预测)
          capital_outflow       资本性流出(大额资产购置,不计入预测)
          capital_net           资本性净流(展示用)
          predicted_change      预测净资产变化(经营性 + SAP 归属)
          residual              实际变化 - 预测变化(应≈资产估值变化)
          residual_pct          残差占期初净资产比例
    """
    summary = compute_cashflow_summary(df_shark)
    shark_income = summary['income_total']
    shark_expense_ex_debt = summary['expense_total'] - summary['debt_principal']

    operating_inflow = 0.0
    operating_outflow = 0.0
    capital_inflow = 0.0
    capital_outflow = 0.0

    if cashflow_log is not None and not cashflow_log.empty and quarter:
        cfl_q = cashflow_log[cashflow_log['Quarter'] == quarter]
        for _, row in cfl_q.iterrows():
            amt = float(row.get('Amount', 0))
            type_str = row.get('Type', '')
            bucket = _classify_cashflow_type(type_str, amt)
            abs_amt = abs(amt)
            if bucket == 'operating_in':
                operating_inflow += abs_amt
            elif bucket == 'operating_out':
                operating_outflow += abs_amt
            else:  # capital
                if amt > 0:
                    capital_inflow += abs_amt
                else:
                    capital_outflow += abs_amt

    # SAP 归属薪酬: portfolio.csv 中 Company_Stock 的 NCF 累计
    #   ESPP NCF = Cost_CNY (机会成本,折扣价买入)
    #   RSU NCF = 归属市值 (无偿归属的 FMV)
    # 这两类都不出现在鲨鱼记账里(钱不经过银行/现金账户),
    # 但都真实增加净资产,必须独立纳入预测。
    # 注意剔除建仓日的 NCF(它是基准点,不是新增流入)。
    sap_vesting = 0.0
    if (portfolio_df is not None and not portfolio_df.empty
            and q_prev_end and q_curr_end):
        df_p = portfolio_df.copy()
        df_p['Date'] = pd.to_datetime(df_p['Date'])
        df_p['Net_Cash_Flow'] = pd.to_numeric(
            df_p['Net_Cash_Flow'], errors='coerce').fillna(0)
        inception_date = df_p['Date'].min()
        mask = (
            (df_p['Asset_Class'] == 'Company_Stock')
            & (df_p['Date'] > pd.to_datetime(q_prev_end))
            & (df_p['Date'] <= pd.to_datetime(q_curr_end))
            & (df_p['Date'] != inception_date)
        )
        sap_vesting = float(df_p.loc[mask, 'Net_Cash_Flow'].sum())

    # 预测变化 = 经营性 + SAP归属薪酬(不含资本性)
    predicted_change = (shark_income - shark_expense_ex_debt
                        + operating_inflow - operating_outflow
                        + sap_vesting)
    actual_change = nw_curr - nw_prev
    residual = actual_change - predicted_change
    residual_pct = abs(residual) / nw_prev if nw_prev > 0 else 0.0

    return {
        'nw_prev':              round(nw_prev, 2),
        'nw_curr':              round(nw_curr, 2),
        'nw_change':            round(actual_change, 2),
        'shark_income':         round(shark_income, 2),
        'shark_expense_ex_debt': round(shark_expense_ex_debt, 2),
        'operating_inflow':     round(operating_inflow, 2),
        'operating_outflow':    round(operating_outflow, 2),
        'sap_vesting':          round(sap_vesting, 2),
        'capital_inflow':       round(capital_inflow, 2),
        'capital_outflow':      round(capital_outflow, 2),
        'capital_net':          round(capital_inflow - capital_outflow, 2),
        'special_inflow':       round(operating_inflow, 2),
        'special_outflow':      round(operating_outflow, 2),
        'predicted_change':     round(predicted_change, 2),
        'residual':             round(residual, 2),
        'residual_pct':         round(residual_pct, 4),
    }
