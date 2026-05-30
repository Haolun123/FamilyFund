"""一次性迁移:
1. transaction.csv 加 Price_Currency 列
2. 历史 49 行按 infer_currency 回填(腾讯 HK0700 → HKD,其他全 CNY)
3. 补登 2026-05-29 泡泡玛特建仓(用户已在 portfolio 加行,但当时新标的写入 bug 漏了 transaction)

执行后立刻产生 transaction.csv.bak.20260530 备份。

完成后此脚本可删除。
"""
import os
import shutil
import sys
from datetime import datetime
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, '/app/src')  # docker container 兼容
from fundamentals import infer_currency

DATA_DIR = os.environ.get('FAMILYFUND_DATA', '/app/data')
TX_PATH = os.path.join(DATA_DIR, 'transaction.csv')
BAK_PATH = TX_PATH + '.bak.20260530'

def main():
    if not os.path.exists(TX_PATH):
        print(f"❌ {TX_PATH} 不存在")
        return 1

    # 备份
    shutil.copy2(TX_PATH, BAK_PATH)
    print(f"✅ 备份: {BAK_PATH}")

    df = pd.read_csv(TX_PATH)
    print(f"读取 {len(df)} 行")

    # 1. 加 Price_Currency 列
    if 'Price_Currency' not in df.columns:
        # 按 Code 推断历史币种
        df['Price_Currency'] = df.apply(
            lambda r: infer_currency(str(r.get('Code', '')), str(r.get('Asset_Class', ''))),
            axis=1,
        )
        # 列序: 插在 Price 之后
        cols = list(df.columns)
        cols.remove('Price_Currency')
        insert_at = cols.index('Price') + 1
        cols.insert(insert_at, 'Price_Currency')
        df = df[cols]
        print(f"✅ 加 Price_Currency 列,按 infer_currency 回填")
        # 统计
        ccy_stats = df['Price_Currency'].value_counts().to_dict()
        print(f"   币种分布: {ccy_stats}")

    # 2. 检查泡泡玛特是否已有 transaction(避免重复登记)
    pop_mask = (
        (df['Date'] == '2026-05-29')
        & (df['Code'] == 'HK9992')
        & (df['Type'] == '买入')
    )
    if pop_mask.any():
        print(f"⏭  泡泡玛特 2026-05-29 已存在, 跳过补登")
    else:
        # 补登
        # NCF=27806.19 含费, Amount = NCF - Fee
        new_row = {
            'Date':           '2026-05-29',
            'Asset_Class':    'ETF_Stock',
            'Platform':       '中信证券',
            'Name':           '泡泡玛特',
            'Code':           'HK9992',
            'Type':           '买入',
            'Amount_CNY':     27769.08,  # 27806.19 - 37.11
            'Price':          160.4,
            'Price_Currency': 'HKD',
            'Fee_CNY':        37.11,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        # 按日期排序保持整洁
        df = df.sort_values(['Date', 'Asset_Class', 'Code']).reset_index(drop=True)
        print(f"✅ 补登泡泡玛特建仓: 200 股 × 160.4 HKD, 人民币 ¥27,769.08 + 手续费 ¥37.11")

    # 写回
    df.to_csv(TX_PATH, index=False)
    print(f"✅ 写回 {TX_PATH} ({len(df)} 行)")
    return 0

if __name__ == '__main__':
    sys.exit(main())
