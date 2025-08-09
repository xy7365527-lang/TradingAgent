import json
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import time
import random
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_result,
)


def is_rate_limited(response):
    """Check if the response indicates rate limiting (status code 429)"""
    return response.status_code == 429


@retry(
    retry=(retry_if_result(is_rate_limited)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(7),
    # 当重试耗尽时，不抛出 RetryError，而是返回最后一次响应（可能仍为 429）
    retry_error_callback=lambda rs: (rs.outcome.result() if rs and rs.outcome else None),
)
def make_request(url, headers):
    """Make a request with retry logic for rate limiting"""
    # Random delay before each request to avoid detection
    time.sleep(random.uniform(2, 6))
    response = requests.get(url, headers=headers, timeout=20)
    return response


def getNewsData(query, start_date, end_date):
    """
    Scrape Google News search results for a given query and date range.
    query: str - search query
    start_date: str - start date in the format yyyy-mm-dd or mm/dd/yyyy
    end_date: str - end date in the format yyyy-mm-dd or mm/dd/yyyy
    """
    if "-" in start_date:
        start_date = datetime.strptime(start_date, "%Y-%m-%d")
        start_date = start_date.strftime("%m/%d/%Y")
    if "-" in end_date:
        end_date = datetime.strptime(end_date, "%Y-%m-%d")
        end_date = end_date.strftime("%m/%d/%Y")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/101.0.4951.54 Safari/537.36"
        )
    }

    def _select_text(root, selectors):
        if not root:
            return ""
        if isinstance(selectors, str):
            selectors = [selectors]
        for selector in selectors:
            try:
                node = root.select_one(selector) if selector else None
                if node is not None:
                    text = node.get_text(strip=True)
                    if isinstance(text, str) and text:
                        return text
            except Exception:
                continue
        return ""

    def _find_link(root):
        if not root:
            return ""
        try:
            a = root.find("a")
            if a is not None:
                href = a.get("href")
                return href or ""
        except Exception:
            return ""
        return ""

    debug = bool(os.environ.get("DEBUG_GOOGLE_NEWS", ""))

    news_results = []
    page = 0
    while True:
        offset = page * 10
        url = (
            f"https://www.google.com/search?q={query}"
            f"&tbs=cdr:1,cd_min:{start_date},cd_max:{end_date}"
            f"&tbm=nws&start={offset}"
        )

        try:
            response = make_request(url, headers)
            # 若重试耗尽后仍无可用响应或继续被限流，则安静退出循环，返回已有结果
            if response is None or getattr(response, "status_code", 0) == 429:
                # 可选择性地在调试时打印：被限流，返回部分结果
                break

            soup = BeautifulSoup(response.content, "html.parser")
            # Primary container selector for Google News vertical; keep a fallback list
            results_on_page = soup.select("div.SoaBEf")
            if not results_on_page:
                # Alternate containers occasionally seen in Google results markup
                results_on_page = soup.select("div.dbsr, div.g")

            if not results_on_page:
                break  # No more results found

            for el in results_on_page:
                try:
                    link = _find_link(el)
                    title = _select_text(el, [
                        "div.MBeuO",            # common title container
                        "div.JheGif.nDgy9d",    # legacy title
                        "h3",                    # generic fallback
                    ])
                    snippet = _select_text(el, [
                        ".GI74Re",               # common snippet container
                        ".st",                   # legacy snippet
                        ".xGQ6f.k1sGcf",         # alt snippet
                        ".Y3v8qd",               # generic snippet
                    ])
                    date = _select_text(el, [
                        ".LfVVr",                # common date
                        "time",                  # semantic time tag
                        "span.OSrXXb.eoY5cb",    # alt date
                    ])
                    source = _select_text(el, [
                        ".NUnG9d span",          # common source
                        ".CEMjEf.NUnG9d span",   # alt source
                        ".XQmXSd",               # alt source
                        "div.SVJrMe span",       # alt source
                        ".NUnG9d",               # fallback
                    ])

                    # Only record entries with at least a title or snippet
                    if title or snippet:
                        news_results.append(
                            {
                                "link": link,
                                "title": title,
                                "snippet": snippet,
                                "date": date,
                                "source": source,
                            }
                        )
                except Exception as e:
                    if debug:
                        # Optional debug output when DEBUG_GOOGLE_NEWS is set
                        print(f"[GoogleNews] Skip one result due to: {e}")
                    continue

            # Update the progress bar with the current count of results scraped

            # Check for the "Next" link (pagination)
            next_link = soup.find("a", id="pnnext")
            if not next_link:
                break

            page += 1

        except Exception:
            # 其它异常也返回已有结果，避免噪音（force_online 下保持在线，但仍可能部分为空）
            break

    return news_results
