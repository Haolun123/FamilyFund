"""SAP stock options engine — cost basis for Own SAP (ESPP) and Move SAP (RSU)."""

import os
import pandas as pd


def load_own_sap(csv_path):
    """Load own_sap.csv → DataFrame."""
    if not os.path.exists(csv_path):
        return None
    df = pd.read_csv(csv_path)
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
    return df


def load_move_sap(csv_path):
    """Load move_sap.csv → DataFrame."""
    if not os.path.exists(csv_path):
        return None
    df = pd.read_csv(csv_path)
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
    return df


def own_sap_summary(df, fx_rate=None):
    """Compute Own SAP summary.

    Args:
        df: Own SAP DataFrame
        fx_rate: current EUR/CNY rate (for break-even calculation)

    Returns:
        dict with total_shares, total_cost, avg_cost_cny, break_even_eur
    """
    total_shares = round(df['Quantity'].sum(), 4)
    total_cost = round(df['Cost_CNY'].sum(), 2)
    avg_cost_cny = round(total_cost / total_shares, 2) if total_shares > 0 else 0.0
    break_even_eur = round(avg_cost_cny / fx_rate, 2) if fx_rate and fx_rate > 0 else None
    return {
        'total_shares': total_shares,
        'total_cost': total_cost,
        'avg_cost_cny': avg_cost_cny,
        'break_even_eur': break_even_eur,
    }


def move_sap_summary(df, fx_rate=None):
    """Compute Move SAP summary.

    Args:
        df: Move SAP DataFrame
        fx_rate: current EUR/CNY rate (for break-even calculation)

    Returns:
        dict with total_shares, total_cost, avg_cost_cny, break_even_eur
    """
    total_shares = round(df['Quantity'].sum(), 4)
    total_cost = round(df['CNY'].sum(), 2)
    avg_cost_cny = round(total_cost / total_shares, 2) if total_shares > 0 else 0.0
    break_even_eur = round(avg_cost_cny / fx_rate, 2) if fx_rate and fx_rate > 0 else None
    return {
        'total_shares': total_shares,
        'total_cost': total_cost,
        'avg_cost_cny': avg_cost_cny,
        'break_even_eur': break_even_eur,
    }


def compute_sap_cost_basis(own_csv=None, move_csv=None):
    """Compute combined SAP cost basis for integration with main portfolio.

    Returns:
        dict: {'own_sap': {'total_cost', 'total_shares'}, 'move_sap': {...}}
        Keys only present if the corresponding CSV exists.
    """
    result = {}

    if own_csv and os.path.exists(own_csv):
        df = load_own_sap(own_csv)
        if df is not None and len(df) > 0:
            result['own_sap'] = {
                'total_cost': round(df['Cost_CNY'].sum(), 2),
                'total_shares': round(df['Quantity'].sum(), 4),
            }

    if move_csv and os.path.exists(move_csv):
        df = load_move_sap(move_csv)
        if df is not None and len(df) > 0:
            result['move_sap'] = {
                'total_cost': round(df['CNY'].sum(), 2),
                'total_shares': round(df['Quantity'].sum(), 4),
            }

    return result
