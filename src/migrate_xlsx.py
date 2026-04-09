"""One-time migration tool: Convert CurrentAsset.xlsx → portfolio.csv format."""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_breakdown import parse_asset_xlsx, classify_assets, CLASS_DISPLAY_NAMES
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_XLSX = os.path.join(BASE_DIR, 'CurrentAsset.xlsx')
DEFAULT_OUTPUT = os.path.join(BASE_DIR, 'data', 'portfolio.csv')


def migrate(xlsx_path=None, snapshot_date=None, output_path=None):
    """将 CurrentAsset.xlsx 转换为 portfolio.csv 的一期快照。

    Args:
        xlsx_path: XLSX 文件路径
        snapshot_date: 快照日期（字符串 YYYY-MM-DD），默认今天
        output_path: 输出 CSV 路径

    Returns:
        DataFrame or None
    """
    xlsx_path = xlsx_path or DEFAULT_XLSX
    snapshot_date = snapshot_date or date.today().isoformat()
    output_path = output_path or DEFAULT_OUTPUT

    holdings = parse_asset_xlsx(xlsx_path)
    if holdings is None:
        return None

    classified = classify_assets(holdings)

    rows = []
    for cls, items in classified.items():
        for h in items:
            rows.append({
                'Date': snapshot_date,
                'Asset_Class': cls,
                'Platform': h['platform'],
                'Name': h['name'],
                'Code': h.get('code', ''),
                'Currency': 'CNY',
                'Exchange_Rate': 1.0,
                'Shares': h['shares'],
                'Current_Price': h['current_price'],
                'Total_Value': h.get('total_value', h.get('current_value', 0)),
                'Net_Cash_Flow': h.get('total_value', h.get('current_value', 0)),
            })

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)

    # Report
    print(f"\n{'=' * 50}")
    print(f" 📋 XLSX → CSV 迁移完成")
    print(f"{'=' * 50}")
    print(f"  快照日期 : {snapshot_date}")
    print(f"  持仓数量 : {len(rows)}")
    print(f"  资产类别 : {len([c for c in classified if classified[c]])} 类")
    print(f"  总市值   : ¥ {df['Total_Value'].sum():,.2f}")
    print(f"  输出文件 : {output_path}")
    print(f"\n  ⚠️  注意: 这是建仓日快照 (Net_Cash_Flow = Total_Value)。")
    print(f"  后续每周追加新行时，Net_Cash_Flow 通常设为 0。")
    print(f"{'=' * 50}\n")

    return df


if __name__ == "__main__":
    xlsx = sys.argv[1] if len(sys.argv) > 1 else None
    snap_date = sys.argv[2] if len(sys.argv) > 2 else None
    output = sys.argv[3] if len(sys.argv) > 3 else None
    migrate(xlsx, snap_date, output)
