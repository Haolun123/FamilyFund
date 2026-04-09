import pandas as pd
import matplotlib.pyplot as plt
import os
import matplotlib.ticker as mtick

# --- 配置路径 (相对于项目根目录) ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_CSV = os.path.join(BASE_DIR, 'data', 'input_fund_data.csv')
OUTPUT_CSV = os.path.join(BASE_DIR, 'data', 'output_nav_log.csv')
OUTPUT_CHART = os.path.join(BASE_DIR, 'output', 'nav_trend_chart.png')


def calculate_nav(input_csv=None):
    """核心净值核算函数。

    Args:
        input_csv: 输入 CSV 文件路径，默认使用 INPUT_CSV。

    Returns:
        pandas.DataFrame: 包含完整净值核算结果的 DataFrame，若输入文件不存在则返回 None。
    """
    if input_csv is None:
        input_csv = INPUT_CSV

    if not os.path.exists(input_csv):
        print(f"错误: 找不到输入文件 {input_csv}。请先创建该文件并填入数据。")
        return None

    # 读取原始数据
    df = pd.read_csv(input_csv)

    # 核心财务变量初始化
    current_shares = 0.0
    records = []

    for index, row in df.iterrows():
        date = row['Date']
        total_value = float(row['Total_Market_Value'])
        cash_flow = float(row['Net_Cash_Flow'])

        if index == 0:
            # 基金成立日 (Day 0) 逻辑：初始净值强行锚定 1.0000
            nav = 1.0000
            current_shares = total_value / nav
            cumulative_return = 0.0
        else:
            # 标准基金核算逻辑（必须剔除当日资金进出对市值的干扰，再算净值）
            # 1. 剥离流水后的真实期初市值
            value_before_cf = total_value - cash_flow

            # 2. 计算最新的单位净值 (真实市值 / 历史总份额)
            nav = value_before_cf / current_shares

            # 3. 按当日最新净值，折算本次资金进出带来的份额增减
            new_shares = cash_flow / nav

            # 4. 更新总份额，留给下一个周期使用
            current_shares += new_shares

            # 5. 计算累计收益率
            cumulative_return = nav - 1.0

        # 归档该期财务快照
        records.append({
            'Date': date,
            'Total_Market_Value': total_value,
            'Net_Cash_Flow': cash_flow,
            'NAV': round(nav, 4),
            'Total_Shares': round(current_shares, 2),
            'Cumulative_Return(%)': round(cumulative_return * 100, 2)
        })

    # 生成底层财务底稿
    result_df = pd.DataFrame(records)
    result_df.to_csv(OUTPUT_CSV, index=False)

    # --- 终端报告输出 ---
    latest = result_df.iloc[-1]
    print("\n" + "=" * 40)
    print(" 📊 家庭基金净值核算报告 (CIO 内部视图)")
    print("=" * 40)
    print(f"统计日期       : {latest['Date']}")
    print(f"基金总市值     : ¥ {latest['Total_Market_Value']:,.2f}")
    print(f"当前总份额     : {latest['Total_Shares']:,.2f} 份")
    print("-" * 40)
    print(f"💰 当前单位净值: {latest['NAV']:.4f}")
    print(f"📈 累计收益率  : {latest['Cumulative_Return(%)']:.2f} %")
    print("=" * 40 + "\n")

    return result_df


def plot_nav_trend(df, output_chart=None):
    """绘制专业且克制的净值走势图。

    Args:
        df: calculate_nav() 返回的 DataFrame。
        output_chart: 输出图表路径，默认使用 OUTPUT_CHART。
    """
    if output_chart is None:
        output_chart = OUTPUT_CHART

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_chart), exist_ok=True)

    # 确保日期格式正确
    df['Date'] = pd.to_datetime(df['Date'])

    plt.figure(figsize=(10, 5), dpi=150)
    plt.plot(df['Date'], df['NAV'], marker='o', linestyle='-', color='#1f77b4', linewidth=2, markersize=5)

    # 样式设置
    plt.title('Family Fund NAV Trend', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Date', fontsize=10)
    plt.ylabel('Net Asset Value (NAV)', fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.axhline(y=1.0, color='r', linestyle='-', linewidth=1, alpha=0.5)  # 1.0 的盈亏红线

    # 旋转X轴日期避免重叠
    plt.xticks(rotation=45)
    plt.tight_layout()

    # 保存并提示
    plt.savefig(output_chart)
    plt.close()
    print(f"✅ 净值走势图已生成: {output_chart}")


if __name__ == "__main__":
    df_results = calculate_nav()
    if df_results is not None:
        plot_nav_trend(df_results)
