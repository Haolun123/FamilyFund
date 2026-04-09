"""One-time migration: CurrentAsset.xlsx → own_sap.csv + move_sap.csv"""

import pandas as pd
import sys
import os
import re


def _parse_chinese_date(date_str):
    """Parse '2026年3月5日' → '2026-03-05'."""
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', str(date_str))
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def import_own_sap(xlsx_path, sheet_index=1):
    """Read Sheet2 (Own SAP) → DataFrame with standardized columns."""
    df = pd.read_excel(xlsx_path, sheet_name=sheet_index)

    # Filter to valid transaction rows
    txn = df[df['Activity'].isin(['Match', 'Purchase', 'Sell'])].copy()

    # Parse dates
    txn['Date'] = txn['Date'].apply(_parse_chinese_date)
    txn = txn.dropna(subset=['Date'])

    # Map Activity: Purchase with "Dividend" in Vehicle Description → "Dividend"
    is_dividend = txn['Vehicle Description'].str.contains('Dividend', case=False, na=False)
    txn.loc[is_dividend, 'Activity'] = 'Dividend'

    # Build output DataFrame
    out = pd.DataFrame({
        'Date': txn['Date'],
        'Activity': txn['Activity'],
        'Price_EUR': txn['Purchase/Sell price'].round(6),
        'Quantity': txn['Quantity'].round(6),
        'Discount_Ratio': txn['Discount Ratio'].round(4),
        'CNY': txn['CNY'].round(2),
        'Cost_CNY': txn['Cost'].round(2),
    })

    return out.sort_values('Date').reset_index(drop=True)


def import_move_sap(xlsx_path, sheet_index=2):
    """Read Sheet3 (Move SAP) → DataFrame with standardized columns."""
    df = pd.read_excel(xlsx_path, sheet_name=sheet_index)

    # Filter to valid transaction rows
    txn = df[df['Activity'].isin(['Award', 'Purchase'])].copy()

    # Parse dates
    txn['Date'] = txn['Date'].apply(_parse_chinese_date)
    txn = txn.dropna(subset=['Date'])

    # Map Activity: Purchase with "Dividend" in Vehicle Description → "Dividend"
    is_dividend = txn['Vehicle Description'].str.contains('Dividend', case=False, na=False)
    txn.loc[is_dividend, 'Activity'] = 'Dividend'

    # Build output DataFrame
    out = pd.DataFrame({
        'Date': txn['Date'],
        'Activity': txn['Activity'],
        'Price_EUR': txn['Purchase/Sell price'].round(6),
        'Quantity': txn['Quantity'].round(6),
        'FX_Rate': txn['Ratio'].round(4),
        'CNY': txn['CNY'].round(2),
    })

    return out.sort_values('Date').reset_index(drop=True)


def main():
    if len(sys.argv) < 3:
        print("Usage: python import_sap_xlsx.py <xlsx_path> <output_dir>")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    output_dir = sys.argv[2]

    if not os.path.exists(xlsx_path):
        print(f"Error: {xlsx_path} not found")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # Own SAP
    own_df = import_own_sap(xlsx_path)
    own_path = os.path.join(output_dir, 'own_sap.csv')
    own_df.to_csv(own_path, index=False)
    own_shares = own_df['Quantity'].sum()
    own_cost = own_df['Cost_CNY'].sum()
    print(f"Own SAP: {len(own_df)} rows → {own_path}")
    print(f"  Shares: {own_shares:.4f}")
    print(f"  Cost:   {own_cost:.2f} CNY")

    # Move SAP
    move_df = import_move_sap(xlsx_path)
    move_path = os.path.join(output_dir, 'move_sap.csv')
    move_df.to_csv(move_path, index=False)
    move_shares = move_df['Quantity'].sum()
    move_cost = move_df['CNY'].sum()
    print(f"\nMove SAP: {len(move_df)} rows → {move_path}")
    print(f"  Shares: {move_shares:.4f}")
    print(f"  Cost:   {move_cost:.2f} CNY")

    print(f"\nCombined: {own_shares + move_shares:.4f} shares")


if __name__ == '__main__':
    main()
