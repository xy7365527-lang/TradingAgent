import json
import os
import requests


def get_data_in_range(ticker, start_date, end_date, data_type, data_dir, period=None):
    """
    Gets finnhub data saved and processed on disk.
    Args:
        start_date (str): Start date in YYYY-MM-DD format.
        end_date (str): End date in YYYY-MM-DD format.
        data_type (str): Type of data from finnhub to fetch. Can be insider_trans, SEC_filings, news_data, insider_senti, or fin_as_reported.
        data_dir (str): Directory where the data is saved.
        period (str): Default to none, if there is a period specified, should be annual or quarterly.
    """

    if period:
        data_path = os.path.join(
            data_dir,
            "finnhub_data",
            data_type,
            f"{ticker}_{period}_data_formatted.json",
        )
    else:
        data_path = os.path.join(
            data_dir, "finnhub_data", data_type, f"{ticker}_data_formatted.json"
        )

    # Gracefully handle missing files/directories or bad JSON
    if not os.path.isfile(data_path):
        return {}
    try:
        with open(data_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:
        return {}


def fetch_company_news_online(ticker: str, start_date: str, end_date: str, api_key: str | None = None, timeout: int = 20):
    """
    Fetch company news directly from Finnhub API.

    Args:
        ticker: Company symbol, e.g. "AAPL".
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        api_key: Optional; if not provided, read from FINNHUB_API_KEY env.
        timeout: HTTP timeout seconds.

    Returns:
        List[dict]: Raw items as returned by Finnhub (each dict contains 'headline', 'summary', 'datetime', etc.).
    """
    token = api_key or os.environ.get("FINNHUB_API_KEY")
    if not token:
        # No token: return empty to let caller degrade gracefully
        return []

    url = "https://finnhub.io/api/v1/company-news"
    params = {"symbol": ticker.upper(), "from": start_date, "to": end_date, "token": token}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def fetch_insider_sentiment_online(ticker: str, start_date: str, end_date: str, api_key: str | None = None, timeout: int = 20):
    """Fetch insider sentiment (monthly) via Finnhub API."""
    token = api_key or os.environ.get("FINNHUB_API_KEY")
    if not token:
        return []
    url = "https://finnhub.io/api/v1/stock/insider-sentiment"
    params = {"symbol": ticker.upper(), "from": start_date, "to": end_date, "token": token}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json() or {}
        items = data.get("data")
        return items if isinstance(items, list) else []
    except Exception:
        return []


def fetch_insider_transactions_online(ticker: str, start_date: str, end_date: str, api_key: str | None = None, timeout: int = 20):
    """Fetch insider transactions via Finnhub API."""
    token = api_key or os.environ.get("FINNHUB_API_KEY")
    if not token:
        return []
    url = "https://finnhub.io/api/v1/stock/insider-transactions"
    params = {"symbol": ticker.upper(), "from": start_date, "to": end_date, "token": token}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json() or {}
        items = data.get("data")
        return items if isinstance(items, list) else []
    except Exception:
        return []
    # filter keys (date, str in format YYYY-MM-DD) by the date range (str, str in format YYYY-MM-DD)
    filtered_data = {}
    for key, value in data.items():
        if start_date <= key <= end_date and len(value) > 0:
            filtered_data[key] = value
    return filtered_data
