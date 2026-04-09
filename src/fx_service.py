"""Exchange rate and stock price fetcher."""

import requests

FX_API_URL = "https://api.frankfurter.app"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
FX_TIMEOUT = 5.0


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
