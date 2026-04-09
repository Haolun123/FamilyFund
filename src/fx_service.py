"""Exchange rate fetcher using frankfurter.app (free, no API key)."""

import requests

FX_API_URL = "https://api.frankfurter.app"
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
