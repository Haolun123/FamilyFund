"""Exchange rate and stock price fetcher."""

import json
import os
import requests
from datetime import datetime

FX_API_URL = "https://api.frankfurter.app"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
FX_TIMEOUT = 5.0

_DATA_DIR = os.environ.get(
    'FAMILYFUND_DATA',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'),
)
_PRICE_CACHE_PATH = os.path.join(_DATA_DIR, 'sap_price_cache.json')


def get_exchange_rate(from_currency, to_currency="CNY"):
    """Fetch latest exchange rate.

    Args:
        from_currency: Source currency code (e.g. 'EUR', 'USD')
        to_currency: Target currency code, default 'CNY'

    Returns:
        float: exchange rate

    Raises:
        requests.RequestException on network/API error
    """
    if from_currency == to_currency:
        return 1.0
    resp = requests.get(
        f"{FX_API_URL}/latest",
        params={"from": from_currency, "to": to_currency},
        timeout=FX_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["rates"][to_currency]


def get_stock_price(ticker="SAP.DE"):
    """Fetch latest stock price from Yahoo Finance.

    Returns:
        float: price in original currency, or None on failure.
    """
    resp = requests.get(
        YAHOO_CHART_URL.format(ticker=ticker),
        params={"range": "1d", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=FX_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    meta = data["chart"]["result"][0]["meta"]
    return meta["regularMarketPrice"]


def load_sap_price_cache(cache_path=None):
    """Load cached SAP price from iCloud-synced JSON. Returns dict or None."""
    path = cache_path or _PRICE_CACHE_PATH
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save_sap_price_cache(price_eur, fx_rate, cache_path=None):
    """Save SAP price and FX rate to iCloud-synced JSON cache."""
    path = cache_path or _PRICE_CACHE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump({
            "price_eur": round(price_eur, 2),
            "fx_rate": round(fx_rate, 4),
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, f)
