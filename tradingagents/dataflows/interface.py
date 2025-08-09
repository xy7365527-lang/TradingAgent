from typing import Annotated, Dict
from .reddit_utils import fetch_top_from_category, ticker_to_company
from .yfin_utils import *
from .stockstats_utils import *
from .googlenews_utils import *
from .finnhub_utils import (
    get_data_in_range,
    fetch_company_news_online,
    fetch_insider_sentiment_online,
    fetch_insider_transactions_online,
)
from dateutil.relativedelta import relativedelta
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import time
import json
import os
import pandas as pd
from tqdm import tqdm
import yfinance as yf
from openai import OpenAI
from .config import get_config, set_config, DATA_DIR
import requests
from .windowing import (
    compute_lookback_days,
    compute_start_end,
    compute_multi_windows,
    compute_half_lives,
)


def get_finnhub_news(
    ticker: Annotated[
        str,
        "Search query of a company's, e.g. 'AAPL, TSM, etc.",
    ],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
):
    """
    Retrieve news about a company within a time frame

    Args
        ticker (str): ticker for the company you are interested in
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns
        str: dataframe containing the news of the company in the time frame

    """

    start_date = datetime.strptime(curr_date, "%Y-%m-%d")
    before = start_date - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    result = get_data_in_range(ticker, before, curr_date, "news_data", DATA_DIR)

    if len(result) == 0:
        return ""

    combined_result = ""
    for day, data in result.items():
        if len(data) == 0:
            continue
        for entry in data:
            current_news = (
                "### " + entry["headline"] + f" ({day})" + "\n" + entry["summary"]
            )
            combined_result += current_news + "\n\n"

    return f"## {ticker} News, from {before} to {curr_date}:\n" + str(combined_result)


def get_finnhub_news_online(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
):
    """Online Finnhub news using official API with graceful fallback."""
    start_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = (start_dt - relativedelta(days=look_back_days)).strftime("%Y-%m-%d")

    raw = fetch_company_news_online(ticker, before, curr_date)
    if not raw:
        return ""

    # Flatten to markdown text grouped by date
    combined_result = ""
    for item in raw:
        headline = item.get("headline", "").strip()
        summary = item.get("summary", "").strip()
        # Finnhub 'datetime' is epoch seconds
        day = None
        ts = item.get("datetime")
        try:
            if isinstance(ts, (int, float)):
                day = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            day = None
        day = day or before
        if headline:
            combined_result += f"### {headline} ({day})\n"
            if summary:
                combined_result += summary + "\n\n"

    if not combined_result:
        return ""
    return f"## {ticker} News (Online), from {before} to {curr_date}:\n" + combined_result


def get_finnhub_company_insider_sentiment(
    ticker: Annotated[str, "ticker symbol for the company"],
    curr_date: Annotated[
        str,
        "current date of you are trading at, yyyy-mm-dd",
    ],
    look_back_days: Annotated[int, "number of days to look back"],
):
    """
    Retrieve insider sentiment about a company (retrieved from public SEC information) for the past 15 days
    Args:
        ticker (str): ticker symbol of the company
        curr_date (str): current date you are trading on, yyyy-mm-dd
    Returns:
        str: a report of the sentiment in the past 15 days starting at curr_date
    """

    date_obj = datetime.strptime(curr_date, "%Y-%m-%d")
    before = date_obj - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    data = get_data_in_range(ticker, before, curr_date, "insider_senti", DATA_DIR)

    if len(data) == 0:
        return ""

    result_str = ""
    seen_dicts = []
    for date, senti_list in data.items():
        for entry in senti_list:
            if entry not in seen_dicts:
                result_str += f"### {entry['year']}-{entry['month']}:\nChange: {entry['change']}\nMonthly Share Purchase Ratio: {entry['mspr']}\n\n"
                seen_dicts.append(entry)

    return (
        f"## {ticker} Insider Sentiment Data for {before} to {curr_date}:\n"
        + result_str
        + "The change field refers to the net buying/selling from all insiders' transactions. The mspr field refers to monthly share purchase ratio."
    )


def get_finnhub_company_insider_sentiment_online(
    ticker: Annotated[str, "ticker symbol for the company"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
    look_back_days: Annotated[int, "number of days to look back"],
):
    start_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = (start_dt - relativedelta(days=look_back_days)).strftime("%Y-%m-%d")
    items = fetch_insider_sentiment_online(ticker, before, curr_date)
    if not items:
        return ""
    seen = set()
    lines = []
    for it in items:
        year = str(it.get("year", ""))
        month = str(it.get("month", ""))
        key = (year, month, it.get("symbol"))
        if key in seen:
            continue
        seen.add(key)
        change = it.get("change", "")
        mspr = it.get("mspr", "")
        lines.append(f"### {year}-{month}:\nChange: {change}\nMonthly Share Purchase Ratio: {mspr}\n")
    if not lines:
        return ""
    return (
        f"## {ticker} Insider Sentiment Data (Online) for {before} to {curr_date}:\n"
        + "\n".join(lines)
    )


def get_finnhub_company_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[
        str,
        "current date you are trading at, yyyy-mm-dd",
    ],
    look_back_days: Annotated[int, "how many days to look back"],
):
    """
    Retrieve insider transcaction information about a company (retrieved from public SEC information) for the past 15 days
    Args:
        ticker (str): ticker symbol of the company
        curr_date (str): current date you are trading at, yyyy-mm-dd
    Returns:
        str: a report of the company's insider transaction/trading informtaion in the past 15 days
    """

    date_obj = datetime.strptime(curr_date, "%Y-%m-%d")
    before = date_obj - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    data = get_data_in_range(ticker, before, curr_date, "insider_trans", DATA_DIR)

    if len(data) == 0:
        return ""

    result_str = ""

    seen_dicts = []
    for date, senti_list in data.items():
        for entry in senti_list:
            if entry not in seen_dicts:
                result_str += f"### Filing Date: {entry['filingDate']}, {entry['name']}:\nChange:{entry['change']}\nShares: {entry['share']}\nTransaction Price: {entry['transactionPrice']}\nTransaction Code: {entry['transactionCode']}\n\n"
                seen_dicts.append(entry)

    return (
        f"## {ticker} insider transactions from {before} to {curr_date}:\n"
        + result_str
        + "The change field reflects the variation in share count—here a negative number indicates a reduction in holdings—while share specifies the total number of shares involved. The transactionPrice denotes the per-share price at which the trade was executed, and transactionDate marks when the transaction occurred. The name field identifies the insider making the trade, and transactionCode (e.g., S for sale) clarifies the nature of the transaction. FilingDate records when the transaction was officially reported, and the unique id links to the specific SEC filing, as indicated by the source. Additionally, the symbol ties the transaction to a particular company, isDerivative flags whether the trade involves derivative securities, and currency notes the currency context of the transaction."
    )


def get_finnhub_company_insider_transactions_online(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
):
    start_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = (start_dt - relativedelta(days=look_back_days)).strftime("%Y-%m-%d")
    items = fetch_insider_transactions_online(ticker, before, curr_date)
    if not items:
        return ""
    seen = set()
    lines = []
    for it in items:
        key = (
            it.get("symbol"),
            it.get("transactionDate"),
            it.get("name"),
            it.get("transactionCode"),
        )
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"### Filing Date: {it.get('filingDate','')}, {it.get('name','')}:\n"
            f"Change:{it.get('change','')}\nShares: {it.get('share','')}\nTransaction Price: {it.get('transactionPrice','')}\n"
            f"Transaction Code: {it.get('transactionCode','')}\n"
        )
    if not lines:
        return ""
    return f"## {ticker} insider transactions (Online) from {before} to {curr_date}:\n" + "\n".join(lines)


def get_simfin_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[
        str,
        "reporting frequency of the company's financial history: annual / quarterly",
    ],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    data_path = os.path.join(
        DATA_DIR,
        "fundamental_data",
        "simfin_data_all",
        "balance_sheet",
        "companies",
        "us",
        f"us-balance-{freq}.csv",
    )
    try:
        if not os.path.exists(data_path):
            return get_simfin_balance_sheet_online(ticker, freq, curr_date) or ""
        df = pd.read_csv(data_path, sep=";")
    except Exception:
        return get_simfin_balance_sheet_online(ticker, freq, curr_date) or ""

    # Convert date strings to datetime objects and remove any time components
    df["Report Date"] = pd.to_datetime(df["Report Date"], utc=True).dt.normalize()
    df["Publish Date"] = pd.to_datetime(df["Publish Date"], utc=True).dt.normalize()

    # Convert the current date to datetime and normalize
    curr_date_dt = pd.to_datetime(curr_date, utc=True).normalize()

    # Filter the DataFrame for the given ticker and for reports that were published on or before the current date
    filtered_df = df[(df["Ticker"] == ticker) & (df["Publish Date"] <= curr_date_dt)]

    # Check if there are any available reports; if not, return a notification
    if filtered_df.empty:
        print("No balance sheet available before the given current date.")
        return ""

    # Get the most recent balance sheet by selecting the row with the latest Publish Date
    latest_balance_sheet = filtered_df.loc[filtered_df["Publish Date"].idxmax()]

    # drop the SimFinID column
    latest_balance_sheet = latest_balance_sheet.drop("SimFinId")

    return (
        f"## {freq} balance sheet for {ticker} released on {str(latest_balance_sheet['Publish Date'])[0:10]}: \n"
        + str(latest_balance_sheet)
        + "\n\nThis includes metadata like reporting dates and currency, share details, and a breakdown of assets, liabilities, and equity. Assets are grouped as current (liquid items like cash and receivables) and noncurrent (long-term investments and property). Liabilities are split between short-term obligations and long-term debts, while equity reflects shareholder funds such as paid-in capital and retained earnings. Together, these components ensure that total assets equal the sum of liabilities and equity."
    )


def get_simfin_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[
        str,
        "reporting frequency of the company's financial history: annual / quarterly",
    ],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    data_path = os.path.join(
        DATA_DIR,
        "fundamental_data",
        "simfin_data_all",
        "cash_flow",
        "companies",
        "us",
        f"us-cashflow-{freq}.csv",
    )

    # 若离线文件不存在或读取失败，则优先回退到在线数据，避免抛出 FileNotFoundError
    try:
        if not os.path.exists(data_path):
            return get_simfin_cashflow_online(ticker, freq, curr_date) or ""
        df = pd.read_csv(data_path, sep=";")
    except Exception:
        return get_simfin_cashflow_online(ticker, freq, curr_date) or ""

    # Convert date strings to datetime objects and remove any time components
    df["Report Date"] = pd.to_datetime(df["Report Date"], utc=True).dt.normalize()
    df["Publish Date"] = pd.to_datetime(df["Publish Date"], utc=True).dt.normalize()

    # Convert the current date to datetime and normalize
    curr_date_dt = pd.to_datetime(curr_date, utc=True).normalize()

    # Filter the DataFrame for the given ticker and for reports that were published on or before the current date
    filtered_df = df[(df["Ticker"] == ticker) & (df["Publish Date"] <= curr_date_dt)]

    # Check if there are any available reports; if not, return a notification
    if filtered_df.empty:
        print("No cash flow statement available before the given current date.")
        return ""

    # Get the most recent cash flow statement by selecting the row with the latest Publish Date
    latest_cash_flow = filtered_df.loc[filtered_df["Publish Date"].idxmax()]

    # drop the SimFinID column
    latest_cash_flow = latest_cash_flow.drop("SimFinId")

    return (
        f"## {freq} cash flow statement for {ticker} released on {str(latest_cash_flow['Publish Date'])[0:10]}: \n"
        + str(latest_cash_flow)
        + "\n\nThis includes metadata like reporting dates and currency, share details, and a breakdown of cash movements. Operating activities show cash generated from core business operations, including net income adjustments for non-cash items and working capital changes. Investing activities cover asset acquisitions/disposals and investments. Financing activities include debt transactions, equity issuances/repurchases, and dividend payments. The net change in cash represents the overall increase or decrease in the company's cash position during the reporting period."
    )


def get_simfin_income_statements(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[
        str,
        "reporting frequency of the company's financial history: annual / quarterly",
    ],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    data_path = os.path.join(
        DATA_DIR,
        "fundamental_data",
        "simfin_data_all",
        "income_statements",
        "companies",
        "us",
        f"us-income-{freq}.csv",
    )
    # 如果离线文件不可用，则回退到在线抓取
    try:
        if not os.path.exists(data_path):
            return get_simfin_income_statements_online(ticker, freq, curr_date) or ""
        df = pd.read_csv(data_path, sep=";")
    except Exception:
        return get_simfin_income_statements_online(ticker, freq, curr_date) or ""

    # 统一按发布日期过滤，并选择最近一期
    df["Report Date"] = pd.to_datetime(df["Report Date"], utc=True).dt.normalize()
    df["Publish Date"] = pd.to_datetime(df["Publish Date"], utc=True).dt.normalize()
    curr_date_dt = pd.to_datetime(curr_date, utc=True).normalize()
    filtered_df = df[(df["Ticker"] == ticker) & (df["Publish Date"] <= curr_date_dt)]
    if filtered_df.empty:
        print("No income statement available before the given current date.")
        return ""
    latest_income = filtered_df.loc[filtered_df["Publish Date"].idxmax()]
    latest_income = latest_income.drop("SimFinId")
    return (
        f"## {freq} income statement for {ticker} released on {str(latest_income['Publish Date'])[0:10]}: \n"
        + str(latest_income)
        + "\n\nThis includes metadata like reporting dates and currency, share details, and a comprehensive breakdown of the company's financial performance. Starting with Revenue, it shows Cost of Revenue and resulting Gross Profit. Operating Expenses are detailed, including SG&A, R&D, and Depreciation. The statement then shows Operating Income, followed by non-operating items and Interest Expense, leading to Pretax Income. After accounting for Income Tax and any Extraordinary items, it concludes with Net Income, representing the company's bottom-line profit or loss for the period."
    )


def get_simfin_balance_sheet_online(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "annual/quarterly"],
    curr_date: Annotated[str, "YYYY-mm-dd"],
):
    """Fetch latest balance sheet online via yfinance (annual or quarterly)."""
    try:
        tk = yf.Ticker(ticker.upper())
        df = tk.balance_sheet if freq.lower().startswith("annual") else tk.quarterly_balance_sheet
        if df is None or df.empty:
            return ""
        cols = [str(c) for c in df.columns]
        chosen = None
        for c in sorted(cols, reverse=True):
            try:
                if c[:10] <= curr_date:
                    chosen = c
                    break
            except Exception:
                continue
        chosen = chosen or cols[0]
        series = df[chosen]
        header = f"## {freq} balance sheet for {ticker.upper()} released on {str(chosen)[:10]}: \n"
        return header + str(series)
    except Exception:
        return ""


def get_simfin_cashflow_online(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "annual/quarterly"],
    curr_date: Annotated[str, "YYYY-mm-dd"],
):
    """Fetch latest cashflow statement online via yfinance (annual or quarterly)."""
    try:
        tk = yf.Ticker(ticker.upper())
        df = tk.cashflow if freq.lower().startswith("annual") else tk.quarterly_cashflow
        if df is None or df.empty:
            return ""
        cols = [str(c) for c in df.columns]
        chosen = None
        for c in sorted(cols, reverse=True):
            try:
                if c[:10] <= curr_date:
                    chosen = c
                    break
            except Exception:
                continue
        chosen = chosen or cols[0]
        series = df[chosen]
        header = f"## {freq} cash flow statement for {ticker.upper()} released on {str(chosen)[:10]}: \n"
        return header + str(series)
    except Exception:
        return ""


def get_simfin_income_statements_online(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "annual/quarterly"],
    curr_date: Annotated[str, "YYYY-mm-dd"],
):
    """Fetch latest income statement online via yfinance (annual or quarterly)."""
    try:
        tk = yf.Ticker(ticker.upper())
        df = tk.financials if freq.lower().startswith("annual") else tk.quarterly_financials
        if df is None or df.empty:
            return ""
        cols = [str(c) for c in df.columns]
        chosen = None
        for c in sorted(cols, reverse=True):
            try:
                if c[:10] <= curr_date:
                    chosen = c
                    break
            except Exception:
                continue
        chosen = chosen or cols[0]
        series = df[chosen]
        header = f"## {freq} income statement for {ticker.upper()} released on {str(chosen)[:10]}: \n"
        return header + str(series)
    except Exception:
        return ""


def get_google_news(
    query: Annotated[str, "Query to search with"],
    curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    query = query.replace(" ", "+")

    start_date = datetime.strptime(curr_date, "%Y-%m-%d")
    before = start_date - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    news_results = getNewsData(query, before, curr_date)

    news_str = ""

    for news in news_results:
        news_str += (
            f"### {news['title']} (source: {news['source']}) \n\n{news['snippet']}\n\n"
        )

    if len(news_results) == 0:
        return ""

    return f"## {query} Google News, from {before} to {curr_date}:\n\n{news_str}"


def get_google_news_auto(
    query: Annotated[str, "Query to search with"],
    curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
) -> str:
    """Auto windowed Google News based on config fund style/holding period."""
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "news")
    return get_google_news(query, curr_date, look_back)


def get_google_news_multi(
    query: Annotated[str, "Query to search with"],
    curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
) -> str:
    """Fetch three windows (fast/mid/slow) and concatenate outputs with headers."""
    cfg = get_config()
    wins = compute_multi_windows(cfg, "news")
    parts: list[str] = []
    for tag, w in zip(["FAST", "MID", "SLOW"], wins):
        section = get_google_news(query, curr_date, int(w))
        if section:
            parts.append(f"# [{tag}] window={w}d\n\n{section}")
    return "\n\n".join(parts)


def _safe_response_text(response) -> str:
    """从 OpenAI Responses API 结果中稳健提取文本，兼容工具调用产物。"""
    # 优先聚合文本
    try:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text
    except Exception:  # noqa: BLE001
        pass

    # 其次遍历 output，拼接 content 中的 text 片段
    try:
        outputs = getattr(response, "output", None) or []
        parts: list[str] = []
        for item in outputs:
            # item 可能是 SDK 对象，尽量取其 content/text
            content = getattr(item, "content", None)
            if isinstance(content, list):
                for seg in content:
                    txt = getattr(seg, "text", None)
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt)
            elif isinstance(item, dict):
                cnt = item.get("content")
                if isinstance(cnt, list):
                    for seg in cnt:
                        if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                            parts.append(seg["text"])
        if parts:
            return "\n".join(parts)
    except Exception:  # noqa: BLE001
        pass

    # 仍取不到则返回字符串化内容
    try:
        return str(response)
    except Exception:  # noqa: BLE001
        return ""


def _to_epoch(date_str: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _pushshift_fetch(query: str, start_date: str, end_date: str, subreddits: list[str] | None = None, size: int = 100) -> list[dict]:
    after = _to_epoch(start_date)
    before = _to_epoch(end_date, end_of_day=True)
    url = "https://api.pushshift.io/reddit/submission/search/"
    params_base = {
        "q": query,
        "after": after,
        "before": before,
        "size": size,
        "sort": "desc",
        "sort_type": "score",
    }
    results: list[dict] = []
    try:
        if subreddits:
            for sub in subreddits:
                params = dict(params_base)
                params["subreddit"] = sub
                r = requests.get(url, params=params, timeout=15)
                if r.status_code == 200:
                    data = r.json() or {}
                    items = data.get("data") or data.get("results")
                    if isinstance(items, list):
                        results.extend(items)
        else:
            r = requests.get(url, params=params_base, timeout=15)
            if r.status_code == 200:
                data = r.json() or {}
                items = data.get("data") or data.get("results")
                if isinstance(items, list):
                    results.extend(items)
    except Exception:
        pass
    return results


def _format_reddit_posts(posts: list[dict]) -> str:
    if not posts:
        return ""
    seen = set()
    lines: list[str] = []
    for p in sorted(posts, key=lambda x: x.get("score", 0), reverse=True):
        pid = p.get("id") or p.get("url") or p.get("title")
        if pid in seen:
            continue
        seen.add(pid)
        title = (p.get("title") or "").strip()
        selftext = (p.get("selftext") or "").strip()
        url = p.get("url") or ""
        created_utc = p.get("created_utc")
        try:
            day = datetime.utcfromtimestamp(int(created_utc)).strftime("%Y-%m-%d") if created_utc else ""
        except Exception:
            day = ""
        if title:
            block = f"### {title} ({day})\n"
            if selftext:
                block += selftext + "\n\n"
            if url:
                block += url
            lines.append(block)
    return "\n\n".join(lines)


def _is_substantive_news_output(text: str) -> bool:
    """Heuristic check: ensure the model output looks like actual news/posts rather than clarifying prompts.

    Criteria (any of):
    - contains URLs ("http")
    - contains markdown headings like "###" or list-like bullets
    - length is sufficiently long (>= 200 chars) and does not look like a question seeking clarification
    """
    if not isinstance(text, str):
        return False
    s = text.strip()
    if not s:
        return False
    if "http" in s:
        return True
    if "###" in s or "- " in s or "* " in s:
        return True
    # If too short or dominated by questions, treat as non-substantive
    if len(s) < 200 and s.count("?") >= 1:
        return False
    # Common clarifying phrases
    lower = s.lower()
    bad_phrases = [
        "need more information",
        "could you clarify",
        "what exactly",
        "which sources",
        "please specify",
        "do you mean",
    ]
    if any(p in lower for p in bad_phrases):
        return False
    return True


def get_reddit_global_news(
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
    max_limit_per_day: Annotated[int, "Maximum number of news per day"],
) -> str:
    """
    Retrieve the latest top reddit news
    Args:
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
    Returns:
        str: A formatted dataframe containing the latest news articles posts on reddit and meta information in these columns: "created_utc", "id", "title", "selftext", "score", "num_comments", "url"
    """

    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    before = start_date - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    posts = []
    # iterate from start_date to end_date
    curr_date = datetime.strptime(before, "%Y-%m-%d")

    total_iterations = (start_date - curr_date).days + 1
    pbar = tqdm(desc=f"Getting Global News on {start_date}", total=total_iterations)

    while curr_date <= start_date:
        curr_date_str = curr_date.strftime("%Y-%m-%d")
        fetch_result = fetch_top_from_category(
            "global_news",
            curr_date_str,
            max_limit_per_day,
            data_path=os.path.join(DATA_DIR, "reddit_data"),
        )
        posts.extend(fetch_result)
        curr_date += relativedelta(days=1)
        pbar.update(1)

    pbar.close()

    if len(posts) == 0:
        return ""

    news_str = ""
    for post in posts:
        if post["content"] == "":
            news_str += f"### {post['title']}\n\n"
        else:
            news_str += f"### {post['title']}\n\n{post['content']}\n\n"

    return f"## Global News Reddit, from {before} to {curr_date}:\n{news_str}"


def get_reddit_global_news_auto(curr_date: Annotated[str, "Current date in yyyy-mm-dd format"]) -> str:
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "social")
    limit = int(cfg.get("news_max_per_day", 5))
    return get_reddit_global_news(curr_date, look_back, limit)


def get_reddit_global_news_multi(curr_date: Annotated[str, "Current date in yyyy-mm-dd format"]) -> str:
    cfg = get_config()
    wins = compute_multi_windows(cfg, "social")
    limit = int(cfg.get("news_max_per_day", 5))
    parts: list[str] = []
    for tag, w in zip(["FAST", "MID", "SLOW"], wins):
        section = get_reddit_global_news(curr_date, int(w), limit)
        if section:
            parts.append(f"# [{tag}] window={w}d\n\n{section}")
    return "\n\n".join(parts)


def get_reddit_company_news(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
    max_limit_per_day: Annotated[int, "Maximum number of news per day"],
) -> str:
    """
    Retrieve the latest top reddit news
    Args:
        ticker: ticker symbol of the company
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
    Returns:
        str: A formatted dataframe containing the latest news articles posts on reddit and meta information in these columns: "created_utc", "id", "title", "selftext", "score", "num_comments", "url"
    """

    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    before = start_date - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    posts = []
    # iterate from start_date to end_date
    curr_date = datetime.strptime(before, "%Y-%m-%d")

    total_iterations = (start_date - curr_date).days + 1
    pbar = tqdm(
        desc=f"Getting Company News for {ticker} on {start_date}",
        total=total_iterations,
    )

    while curr_date <= start_date:
        curr_date_str = curr_date.strftime("%Y-%m-%d")
        fetch_result = fetch_top_from_category(
            "company_news",
            curr_date_str,
            max_limit_per_day,
            ticker,
            data_path=os.path.join(DATA_DIR, "reddit_data"),
        )
        posts.extend(fetch_result)
        curr_date += relativedelta(days=1)

        pbar.update(1)

    pbar.close()

    if len(posts) == 0:
        return ""

    news_str = ""
    for post in posts:
        if post["content"] == "":
            news_str += f"### {post['title']}\n\n"
        else:
            news_str += f"### {post['title']}\n\n{post['content']}\n\n"

    return f"##{ticker} News Reddit, from {before} to {curr_date}:\n\n{news_str}"


def get_reddit_company_news_auto(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "social")
    limit = int(cfg.get("news_max_per_day", 5))
    return get_reddit_company_news(ticker, curr_date, look_back, limit)


def get_reddit_company_news_multi(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    cfg = get_config()
    wins = compute_multi_windows(cfg, "social")
    limit = int(cfg.get("news_max_per_day", 5))
    parts: list[str] = []
    for tag, w in zip(["FAST", "MID", "SLOW"], wins):
        section = get_reddit_company_news(ticker, curr_date, int(w), limit)
        if section:
            parts.append(f"# [{tag}] window={w}d\n\n{section}")
    return "\n\n".join(parts)


def get_reddit_company_news_online(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
):
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "social")
    start_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = (start_dt - relativedelta(days=look_back)).strftime("%Y-%m-%d")

    alias = ticker_to_company.get(ticker.upper(), "")
    terms = [ticker.upper()]
    if alias:
        if " OR " in alias:
            terms.extend([t.strip() for t in alias.split(" OR ") if t.strip()])
        else:
            terms.append(alias)
    query = " OR ".join(terms)

    posts = _pushshift_fetch(query, before, curr_date, subreddits=["stocks", "investing", "wallstreetbets", "stockmarket"], size=150)
    text = _format_reddit_posts(posts)
    if text:
        return f"##{ticker.upper()} News Reddit (Online), from {before} to {curr_date}:\n\n" + text

    provider = cfg.get("llm_provider", "").lower()
    use_responses = bool(cfg.get("use_openai_responses", False))
    if provider == "openai" and use_responses:
        client = OpenAI(base_url=cfg["backend_url"])
        prompt = (
            f"Search reddit.com for discussions about {ticker} from {before} to {curr_date}. "
            f"Only include posts in that date range. Summarize each with title and 1-2 lines."
        )
        kwargs = dict(
            model=cfg["quick_think_llm"],
            input=[{"role": "system", "content": [{"type": "input_text", "text": prompt}]}],
            text={"format": {"type": "text"}},
            reasoning={},
            tools=[{"type": "web_search_preview", "user_location": {"type": "approximate"}, "search_context_size": "low"}],
            temperature=1,
            max_output_tokens=2048,
            top_p=1,
            store=True,
        )
        try:
            if bool(cfg.get("oai_responses_streaming", True)):
                with client.responses.stream(**kwargs) as stream:
                    for _ in stream:
                        pass
                    response = stream.get_final_response()
            else:
                response = client.responses.create(**kwargs)
            text = _safe_response_text(response)
            return f"##{ticker.upper()} News Reddit (Online via web_search), from {before} to {curr_date}:\n\n" + text
        except Exception:
            pass

    # Fallback 2 (still online): use Reddit API via PRAW to fetch and then read from cache
    try:
        from .reddit_online import fetch_and_cache_company
        from .reddit_utils import fetch_top_from_category
        # Fetch online via PRAW into cache
        fetch_and_cache_company(ticker, curr_date, look_back_days=look_back, per_subreddit_limit=160)
        # Read back for each day in window and format
        news_str = ""
        curr = datetime.strptime(before, "%Y-%m-%d")
        end = datetime.strptime(curr_date, "%Y-%m-%d")
        while curr <= end:
            day = curr.strftime("%Y-%m-%d")
            items = fetch_top_from_category("company_news", day, int(cfg.get("news_max_per_day", 5)), ticker, data_path=os.path.join(DATA_DIR, "reddit_data"))
            for post in items:
                title = (post.get("title") or "").strip()
                content = (post.get("content") or "").strip()
                if title:
                    news_str += f"### {title} ({day})\n"
                    if content:
                        news_str += content + "\n\n"
            curr += relativedelta(days=1)
        if news_str.strip():
            return f"##{ticker.upper()} News Reddit (Online via PRAW), from {before} to {curr_date}:\n\n" + news_str
    except Exception:
        pass
    return ""


def get_reddit_global_news_online(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "social")
    start_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = (start_dt - relativedelta(days=look_back)).strftime("%Y-%m-%d")

    subs = ["worldnews", "news", "stocks", "investing"]
    posts = _pushshift_fetch("", before, curr_date, subreddits=subs, size=200)
    text = _format_reddit_posts(posts)
    if text:
        return f"## Global News Reddit (Online), from {before} to {curr_date}:\n\n" + text

    provider = cfg.get("llm_provider", "").lower()
    use_responses = bool(cfg.get("use_openai_responses", False))
    if provider == "openai" and use_responses:
        client = OpenAI(base_url=cfg["backend_url"])
        prompt = (
            f"Search reddit.com for global/macro news discussions from {before} to {curr_date}. "
            f"Summarize key threads with titles."
        )
        kwargs = dict(
            model=cfg["quick_think_llm"],
            input=[{"role": "system", "content": [{"type": "input_text", "text": prompt}]}],
            text={"format": {"type": "text"}},
            reasoning={},
            tools=[{"type": "web_search_preview", "user_location": {"type": "approximate"}, "search_context_size": "low"}],
            temperature=1,
            max_output_tokens=2048,
            top_p=1,
            store=True,
        )
        try:
            if bool(cfg.get("oai_responses_streaming", True)):
                with client.responses.stream(**kwargs) as stream:
                    for _ in stream:
                        pass
                    response = stream.get_final_response()
            else:
                response = client.responses.create(**kwargs)
            text = _safe_response_text(response)
            return f"## Global News Reddit (Online via web_search), from {before} to {curr_date}:\n\n" + text
        except Exception:
            pass

    # Fallback 2 (still online): use Reddit API via PRAW to fetch and then read from cache
    try:
        from .reddit_online import fetch_and_cache_global
        from .reddit_utils import fetch_top_from_category
        # Fetch online via PRAW into cache
        fetch_and_cache_global(curr_date, look_back_days=look_back, per_subreddit_limit=120)
        # Read back for each day in window and format
        news_str = ""
        curr = datetime.strptime(before, "%Y-%m-%d")
        end = datetime.strptime(curr_date, "%Y-%m-%d")
        while curr <= end:
            day = curr.strftime("%Y-%m-%d")
            items = fetch_top_from_category("global_news", day, int(cfg.get("news_max_per_day", 5)), data_path=os.path.join(DATA_DIR, "reddit_data"))
            for post in items:
                title = (post.get("title") or "").strip()
                content = (post.get("content") or "").strip()
                if title:
                    news_str += f"### {title} ({day})\n"
                    if content:
                        news_str += content + "\n\n"
            curr += relativedelta(days=1)
        if news_str.strip():
            return f"## Global News Reddit (Online via PRAW), from {before} to {curr_date}:\n\n" + news_str
    except Exception:
        pass
    return ""
def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    look_back_days: Annotated[int, "how many days to look back"],
    online: Annotated[bool, "to fetch data online or offline"],
) -> str:

    best_ind_params = {
        # Moving Averages
        "close_50_sma": (
            "50 SMA: A medium-term trend indicator. "
            "Usage: Identify trend direction and serve as dynamic support/resistance. "
            "Tips: It lags price; combine with faster indicators for timely signals."
        ),
        "close_200_sma": (
            "200 SMA: A long-term trend benchmark. "
            "Usage: Confirm overall market trend and identify golden/death cross setups. "
            "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
        ),
        "close_10_ema": (
            "10 EMA: A responsive short-term average. "
            "Usage: Capture quick shifts in momentum and potential entry points. "
            "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
        ),
        # MACD Related
        "macd": (
            "MACD: Computes momentum via differences of EMAs. "
            "Usage: Look for crossovers and divergence as signals of trend changes. "
            "Tips: Confirm with other indicators in low-volatility or sideways markets."
        ),
        "macds": (
            "MACD Signal: An EMA smoothing of the MACD line. "
            "Usage: Use crossovers with the MACD line to trigger trades. "
            "Tips: Should be part of a broader strategy to avoid false positives."
        ),
        "macdh": (
            "MACD Histogram: Shows the gap between the MACD line and its signal. "
            "Usage: Visualize momentum strength and spot divergence early. "
            "Tips: Can be volatile; complement with additional filters in fast-moving markets."
        ),
        # Momentum Indicators
        "rsi": (
            "RSI: Measures momentum to flag overbought/oversold conditions. "
            "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
            "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
        ),
        # Volatility Indicators
        "boll": (
            "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
            "Usage: Acts as a dynamic benchmark for price movement. "
            "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
        ),
        "boll_ub": (
            "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
            "Usage: Signals potential overbought conditions and breakout zones. "
            "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
        ),
        "boll_lb": (
            "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
            "Usage: Indicates potential oversold conditions. "
            "Tips: Use additional analysis to avoid false reversal signals."
        ),
        "atr": (
            "ATR: Averages true range to measure volatility. "
            "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
            "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
        ),
        # Volume-Based Indicators
        "vwma": (
            "VWMA: A moving average weighted by volume. "
            "Usage: Confirm trends by integrating price action with volume data. "
            "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
        ),
        "mfi": (
            "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
            "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
            "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
        ),
    }

    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(best_ind_params.keys())}"
        )

    end_date = curr_date
    curr_date = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date - relativedelta(days=look_back_days)

    if not online:
        # read from YFin data
        data = pd.read_csv(
            os.path.join(
                DATA_DIR,
                f"market_data/price_data/{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
            )
        )
        data["Date"] = pd.to_datetime(data["Date"], utc=True)
        dates_in_df = data["Date"].astype(str).str[:10]

        ind_string = ""
        while curr_date >= before:
            # only do the trading dates
            if curr_date.strftime("%Y-%m-%d") in dates_in_df.values:
                indicator_value = get_stockstats_indicator(
                    symbol, indicator, curr_date.strftime("%Y-%m-%d"), online
                )

                ind_string += f"{curr_date.strftime('%Y-%m-%d')}: {indicator_value}\n"

            curr_date = curr_date - relativedelta(days=1)
    else:
        # online gathering
        ind_string = ""
        while curr_date >= before:
            indicator_value = get_stockstats_indicator(
                symbol, indicator, curr_date.strftime("%Y-%m-%d"), online
            )

            ind_string += f"{curr_date.strftime('%Y-%m-%d')}: {indicator_value}\n"

            curr_date = curr_date - relativedelta(days=1)

    result_str = (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + best_ind_params.get(indicator, "No description available.")
    )

    return result_str


def get_stock_stats_indicators_auto(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    online: Annotated[bool, "to fetch data online or offline"],
) -> str:
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "technical")
    return get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back, online)


def get_stock_stats_indicators_multi(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator"],
    curr_date: Annotated[str, "YYYY-mm-dd"],
    online: Annotated[bool, "online or offline"],
) -> str:
    cfg = get_config()
    wins = compute_multi_windows(cfg, "technical")
    parts: list[str] = []
    for tag, w in zip(["FAST", "MID", "SLOW"], wins):
        section = get_stock_stats_indicators_window(symbol, indicator, curr_date, int(w), online)
        if section:
            parts.append(f"# [{tag}] window={w}d\n\n{section}")
    return "\n\n".join(parts)


def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    online: Annotated[bool, "to fetch data online or offline"],
) -> str:

    curr_date = datetime.strptime(curr_date, "%Y-%m-%d")
    curr_date = curr_date.strftime("%Y-%m-%d")

    try:
        indicator_value = StockstatsUtils.get_stock_stats(
            symbol,
            indicator,
            curr_date,
            os.path.join(DATA_DIR, "market_data", "price_data"),
            online=online,
        )
    except Exception as e:
        print(
            f"Error getting stockstats indicator data for indicator {indicator} on {curr_date}: {e}"
        )
        return ""

    return str(indicator_value)


def get_YFin_data_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    # 计算窗口起始日期
    date_obj = datetime.strptime(curr_date, "%Y-%m-%d")
    before = date_obj - relativedelta(days=look_back_days)
    start_date = before.strftime("%Y-%m-%d")

    # 复用具备离线/缓存/在线回退的 get_YFin_data
    result = get_YFin_data(symbol, start_date, curr_date)

    # 统一格式化输出
    if hasattr(result, "to_string"):
        with pd.option_context(
            "display.max_rows", None, "display.max_columns", None, "display.width", None
        ):
            df_string = result.to_string()
        return f"## Raw Market Data for {symbol} from {start_date} to {curr_date}:\n\n" + df_string

    # 若为字符串（例如无数据或联网失败的友好提示），直接返回
    return str(result)


def get_YFin_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):
    # Validate dates
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    # Try online first with lightweight retry. If force_online=True,禁用一切离线/缓存回退
    data = None
    last_err: Exception | None = None
    cfg_now = get_config()
    force_online = bool(cfg_now.get("force_online", False))
    for attempt in range(3):
        try:
            ticker = yf.Ticker(symbol.upper())
            tmp = ticker.history(start=start_date, end=end_date)
            if not tmp.empty:
                data = tmp
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
        # Backoff: 1s, 2s, 4s
        time.sleep(1 * (2 ** attempt))

    # 在强制在线模式下，直接返回错误信息，不做任何回退
    if force_online and (data is None or data.empty):
        msg = (
            f"No data available for '{symbol}' between {start_date} and {end_date} "
            f"(force_online enabled; network error: {last_err})"
        )
        return msg

    # Fallback to cache saved by stockstats utils（仅在非强制在线模式）
    if data is None or data.empty:
        cfg = cfg_now
        cache_dir = cfg.get("data_cache_dir")
        if cache_dir and os.path.isdir(cache_dir):
            import glob
            pattern = os.path.join(cache_dir, f"{symbol}-YFin-data-*.csv")
            candidates = sorted(glob.glob(pattern), reverse=True)
            for path in candidates:
                try:
                    df = pd.read_csv(path)
                    if "Date" in df.columns:
                        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
                        df = df[(df["Date"].dt.strftime("%Y-%m-%d") >= start_date) & (df["Date"].dt.strftime("%Y-%m-%d") <= end_date)]
                        if not df.empty:
                            data = df.set_index("Date")
                            break
                except Exception:
                    continue

    # Fallback to offline packaged CSVs
    if data is None or data.empty:
        offline_path = os.path.join(
            DATA_DIR,
            "market_data",
            "price_data",
            f"{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
        )
        if os.path.exists(offline_path):
            df = pd.read_csv(offline_path)
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
                df = df[(df["Date"].dt.strftime("%Y-%m-%d") >= start_date) & (df["Date"].dt.strftime("%Y-%m-%d") <= end_date)]
                if not df.empty:
                    data = df.set_index("Date")

    # If still none, return a friendly message instead of raising
    if data is None or data.empty:
        msg = (
            f"No data available for '{symbol}' between {start_date} and {end_date} "
            f"(network error: {last_err})"
        )
        return msg

    # Remove timezone info from index for cleaner output
    if getattr(data.index, "tz", None) is not None:
        data.index = data.index.tz_localize(None)

    # Round numerical values to 2 decimal places for cleaner display
    numeric_columns = ["Open", "High", "Low", "Close", "Adj Close"]
    for col in numeric_columns:
        if col in data.columns:
            data[col] = data[col].round(2)

    # Convert DataFrame to CSV string
    csv_string = data.to_csv()

    # Add header information
    header = f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(data)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    return header + csv_string


def get_YFin_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Prefer本地离线CSV；若不存在则回退到缓存目录，再不行则在线抓取。

    返回类型保持向后兼容：
    - 若为DataFrame，将被上层使用 `to_string()` 渲染；
    - 若全部失败，返回说明性字符串而非抛异常。
    """
    cfg0 = get_config()
    if bool(cfg0.get("force_online", False)):
        # 严格在线：改道到在线接口
        try:
            ticker = yf.Ticker(symbol.upper())
            online_df = ticker.history(start=start_date, end=end_date)
            if getattr(online_df.index, "tz", None) is not None:
                online_df.index = online_df.index.tz_localize(None)
            if not online_df.empty:
                online_df = online_df.copy()
                online_df.reset_index(inplace=True)
                online_df.rename(columns={online_df.columns[0]: "Date"}, inplace=True)
                online_df["Date"] = pd.to_datetime(online_df["Date"]).dt.strftime("%Y-%m-%d")
                for col in ["Open", "High", "Low", "Close", "Adj Close"]:
                    if col in online_df.columns:
                        online_df[col] = pd.to_numeric(online_df[col], errors="coerce").round(2)
                return online_df.reset_index(drop=True)
        except Exception:
            return f"No market data available for '{symbol}' between {start_date} and {end_date} (force_online)"

    # 先尝试读取打包的离线CSV（非严格在线时）
    offline_path = os.path.join(
        DATA_DIR,
        "market_data",
        "price_data",
        f"{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
    )

    def _filter_and_format(df: pd.DataFrame) -> pd.DataFrame:
        # 标准化 Date 列到 YYYY-mm-dd 字符串
        if "Date" in df.columns:
            try:
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
            except Exception:
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.tz_localize(None)
        else:
            # yfinance 通常以 DatetimeIndex 返回
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.columns[0]: "Date"})
            elif "Unnamed: 0" in df.columns:  # 常见CSV索引列名
                df = df.rename(columns={"Unnamed: 0": "Date"})
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.tz_localize(None)

        if "Date" in df.columns:
            df["DateOnly"] = df["Date"].dt.strftime("%Y-%m-%d")
            df = df[(df["DateOnly"] >= start_date) & (df["DateOnly"] <= end_date)]
            df = df.drop(columns=[c for c in ["DateOnly"] if c in df.columns])
            # 输出时将 Date 转为无时区字符串
            df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")

        # 数值列做轻度四舍五入，便于展示
        for col in ["Open", "High", "Low", "Close", "Adj Close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

        return df.reset_index(drop=True)

    try:
        if os.path.exists(offline_path):
            data = pd.read_csv(offline_path)
            # 若请求超出离线数据覆盖范围，则回退到在线抓取，而不是抛异常
            if end_date > "2025-03-25":
                raise FileNotFoundError  # 触发回退
            filtered = _filter_and_format(data)
            if not filtered.empty:
                return filtered
    except Exception:
        # 离线读取失败则继续尝试其它路径
        pass

    # 尝试缓存目录（由 stockstats 或其它流程写入）
    cfg = get_config()
    cache_dir = cfg.get("data_cache_dir")
    if cache_dir and os.path.isdir(cache_dir):
        try:
            import glob

            pattern = os.path.join(cache_dir, f"{symbol}-YFin-data-*.csv")
            candidates = sorted(glob.glob(pattern), reverse=True)
            for path in candidates:
                try:
                    df = pd.read_csv(path)
                    df = _filter_and_format(df)
                    if not df.empty:
                        return df
                except Exception:
                    continue
        except Exception:
            pass

    # 最后回退：在线抓取（yfinance），并写入缓存以便后续使用
    try:
        ticker = yf.Ticker(symbol.upper())
        online_df = ticker.history(start=start_date, end=end_date)
        if getattr(online_df.index, "tz", None) is not None:
            online_df.index = online_df.index.tz_localize(None)
        if not online_df.empty:
            online_df = online_df.copy()
            online_df.reset_index(inplace=True)
            online_df.rename(columns={online_df.columns[0]: "Date"}, inplace=True)
            online_df["Date"] = pd.to_datetime(online_df["Date"]).dt.strftime("%Y-%m-%d")
            for col in ["Open", "High", "Low", "Close", "Adj Close"]:
                if col in online_df.columns:
                    online_df[col] = pd.to_numeric(online_df[col], errors="coerce").round(2)

            # 写入缓存
            try:
                if cache_dir:
                    os.makedirs(cache_dir, exist_ok=True)
                    out_path = os.path.join(
                        cache_dir, f"{symbol}-YFin-data-{start_date}-{end_date}.csv"
                    )
                    online_df.to_csv(out_path, index=False)
            except Exception:
                pass

            return online_df.reset_index(drop=True)
    except Exception as e:
        last_err = e  # 仅用于最终消息
    
    # 所有途径均失败：返回友好提示，避免抛出异常
    msg = (
        f"No market data available for '{symbol}' between {start_date} and {end_date}. "
        f"Checked: offline '{offline_path}', cache_dir='{cache_dir}'."
    )
    try:
        msg += f" Online fetch failed."
    except Exception:
        pass
    return msg


def get_YFin_data_online_auto(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "technical")
    start_date, end_date = compute_start_end(curr_date, look_back)
    return get_YFin_data_online(symbol, start_date, end_date)


def get_YFin_data_online_multi(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    cfg = get_config()
    wins = compute_multi_windows(cfg, "technical")
    parts: list[str] = []
    for tag, w in zip(["FAST", "MID", "SLOW"], wins):
        start_date, end_date = compute_start_end(curr_date, int(w))
        section = get_YFin_data_online(symbol, start_date, end_date)
        if section:
            parts.append(f"# [{tag}] window={w}d\n\n{section}")
    return "\n\n".join(parts)


def get_YFin_data_auto(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    cfg = get_config()
    look_back = compute_lookback_days(cfg, "technical")
    start_date, end_date = compute_start_end(curr_date, look_back)
    return get_YFin_data(symbol, start_date, end_date)


def get_YFin_data_multi(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    cfg = get_config()
    wins = compute_multi_windows(cfg, "technical")
    parts: list[str] = []
    for tag, w in zip(["FAST", "MID", "SLOW"], wins):
        start_date, end_date = compute_start_end(curr_date, int(w))
        section = get_YFin_data(symbol, start_date, end_date)
        try:
            # If DataFrame, convert to string with header
            import pandas as pd  # type: ignore
            if hasattr(section, "to_string"):
                df_str = section.to_string()
                parts.append(f"# [{tag}] window={w}d\n\n{df_str}")
                continue
        except Exception:
            pass
        if section is not None:
            parts.append(f"# [{tag}] window={w}d\n\n{section}")
    return "\n\n".join(parts)


def get_stock_news_openai(ticker, curr_date):
    config = get_config()
    provider = config.get("llm_provider", "").lower()
    use_responses = bool(config.get("use_openai_responses", False))

    # 若未启用 Responses 或非 OpenAI 提供商，则回退至 Google News
    if provider != "openai" or not use_responses:
        return get_google_news(ticker, curr_date, 7)

    client = OpenAI(base_url=config["backend_url"])
    kwargs = dict(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Can you search Social Media for {ticker} from 7 days before {curr_date} to {curr_date}? "
                            "Make sure you only get the data posted during that period."
                        ),
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    # 优先使用流式以自动完成工具调用
    if bool(config.get("oai_responses_streaming", True)):
        try:
            with client.responses.stream(**kwargs) as stream:
                for _ in stream:
                    pass
                response = stream.get_final_response()
            text = _safe_response_text(response)
            if not _is_substantive_news_output(text):
                # 尝试一次非流式作为补救
                try:
                    response = client.responses.create(**kwargs)
                    text2 = _safe_response_text(response)
                    if _is_substantive_news_output(text2):
                        return text2
                except Exception:
                    pass
                # 最后回退至 Google News 抓取，保证产出
                return get_google_news(ticker, curr_date, 7)
            return text
        except Exception:
            # 回退至非流式
            try:
                response = client.responses.create(**kwargs)
                text = _safe_response_text(response)
                if not _is_substantive_news_output(text):
                    return get_google_news(ticker, curr_date, 7)
                return text
            except Exception:
                return get_google_news(ticker, curr_date, 7)
    else:
        try:
            response = client.responses.create(**kwargs)
            text = _safe_response_text(response)
            if not _is_substantive_news_output(text):
                return get_google_news(ticker, curr_date, 7)
            return text
        except Exception:
            return get_google_news(ticker, curr_date, 7)


def get_global_news_openai(curr_date):
    config = get_config()
    provider = config.get("llm_provider", "").lower()
    use_responses = bool(config.get("use_openai_responses", False))

    if provider != "openai" or not use_responses:
        return get_google_news("global macroeconomics stock market", curr_date, 7)

    client = OpenAI(base_url=config["backend_url"])
    kwargs = dict(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Can you search global or macroeconomics news from 7 days before {curr_date} to {curr_date} "
                            "that would be informative for trading purposes? Make sure you only get the data posted during that period."
                        ),
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    if bool(config.get("oai_responses_streaming", True)):
        try:
            with client.responses.stream(**kwargs) as stream:
                for _ in stream:
                    pass
                response = stream.get_final_response()
            text = _safe_response_text(response)
            if not _is_substantive_news_output(text):
                try:
                    response = client.responses.create(**kwargs)
                    text2 = _safe_response_text(response)
                    if _is_substantive_news_output(text2):
                        return text2
                except Exception:
                    pass
                return get_google_news("global macroeconomics stock market", curr_date, 7)
            return text
        except Exception:
            try:
                response = client.responses.create(**kwargs)
                text = _safe_response_text(response)
                if not _is_substantive_news_output(text):
                    return get_google_news("global macroeconomics stock market", curr_date, 7)
                return text
            except Exception:
                return get_google_news("global macroeconomics stock market", curr_date, 7)
    else:
        try:
            response = client.responses.create(**kwargs)
            text = _safe_response_text(response)
            if not _is_substantive_news_output(text):
                return get_google_news("global macroeconomics stock market", curr_date, 7)
            return text
        except Exception:
            return get_google_news("global macroeconomics stock market", curr_date, 7)


def get_fundamentals_openai(ticker, curr_date):
    config = get_config()
    provider = config.get("llm_provider", "").lower()
    use_responses = bool(config.get("use_openai_responses", False))

    if provider != "openai" or not use_responses:
        return get_google_news(f"{ticker} fundamentals PE PS cash flow", curr_date, 30)

    client = OpenAI(base_url=config["backend_url"])
    kwargs = dict(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Can you search Fundamental for discussions on {ticker} during of the month before {curr_date} to the month of {curr_date}. "
                            "Make sure you only get the data posted during that period. List as a table, with PE/PS/Cash flow/ etc"
                        ),
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    if bool(config.get("oai_responses_streaming", True)):
        try:
            with client.responses.stream(**kwargs) as stream:
                for _ in stream:
                    pass
                response = stream.get_final_response()
            return _safe_response_text(response)
        except Exception:
            try:
                response = client.responses.create(**kwargs)
                return _safe_response_text(response)
            except Exception:
                return get_google_news(f"{ticker} fundamentals PE PS cash flow", curr_date, 30)
    else:
        try:
            response = client.responses.create(**kwargs)
            return _safe_response_text(response)
        except Exception:
            return get_google_news(f"{ticker} fundamentals PE PS cash flow", curr_date, 30)
