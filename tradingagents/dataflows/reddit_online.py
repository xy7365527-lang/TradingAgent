from __future__ import annotations

import os
import json
from datetime import datetime, timedelta
from typing import Iterable, List, Optional

from .config import get_config, DATA_DIR


def _ensure_dirs(base_dir: str) -> tuple[str, str]:
    reddit_dir = os.path.join(base_dir, "reddit_data")
    global_dir = os.path.join(reddit_dir, "global_news")
    company_dir = os.path.join(reddit_dir, "company_news")
    os.makedirs(global_dir, exist_ok=True)
    os.makedirs(company_dir, exist_ok=True)
    return global_dir, company_dir


def _get_time_window(curr_date: str, look_back_days: int) -> tuple[int, int]:
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=int(look_back_days))
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _open_jsonl(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return open(path, "a", encoding="utf-8")


def _submission_to_record(s) -> dict:
    # PRAW Submission: fallback to score if ups not available
    ups = getattr(s, "ups", None)
    if ups is None:
        ups = getattr(s, "score", 0)
    return {
        "id": getattr(s, "id", ""),
        "created_utc": float(getattr(s, "created_utc", 0.0) or 0.0),
        "title": getattr(s, "title", "") or "",
        "selftext": getattr(s, "selftext", "") or "",
        "url": getattr(s, "url", "") or "",
        "ups": int(ups) if isinstance(ups, (int, float)) else 0,
        "subreddit": str(getattr(getattr(s, "subreddit", None), "display_name", "")) or "",
    }


def _iter_submissions(subreddit, start_ts: int, end_ts: int, hard_limit: int = 800) -> Iterable:
    # Iterate newest posts and stop when older than start_ts
    count = 0
    for s in subreddit.new(limit=hard_limit):
        try:
            ts = float(getattr(s, "created_utc", 0.0) or 0.0)
        except Exception:
            continue
        if ts < start_ts:
            break
        if ts <= end_ts:
            yield s
            count += 1
        if count >= hard_limit:
            break


def _write_jsonl(path: str, records: List[dict]) -> int:
    if not records:
        return 0
    with _open_jsonl(path) as f:
        for r in records:
            try:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            except Exception:
                continue
    return len(records)


def _load_praw():
    import praw  # lazy import

    cid = os.environ.get("REDDIT_CLIENT_ID")
    csec = os.environ.get("REDDIT_CLIENT_SECRET")
    uag = os.environ.get("REDDIT_USER_AGENT") or "TradingAgents/1.0 by TauricResearch"
    if not cid or not csec:
        raise RuntimeError("Missing Reddit API credentials: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET")
    return praw.Reddit(client_id=cid, client_secret=csec, user_agent=uag)


def fetch_and_cache_global(curr_date: str, look_back_days: int, per_subreddit_limit: int = 120,
                           subreddits: Optional[List[str]] = None) -> int:
    """Fetch last N days of global/macro news posts and append to cache JSONL files.

    Returns number of records written.
    """
    cfg = get_config()
    base = cfg.get("data_dir", DATA_DIR) or DATA_DIR
    global_dir, _ = _ensure_dirs(base)

    start_ts, end_ts = _get_time_window(curr_date, look_back_days)
    reddit = _load_praw()

    subs = subreddits or [
        "worldnews", "news", "business", "stocks", "StockMarket", "investing", "finance", "technology",
    ]

    total = 0
    for name in subs:
        try:
            sub = reddit.subreddit(name)
            # Pull some buffer and filter by time; then trim to per_subreddit_limit
            items = []
            for s in _iter_submissions(sub, start_ts, end_ts, hard_limit=max(200, per_subreddit_limit * 3)):
                items.append(_submission_to_record(s))
                if len(items) >= per_subreddit_limit:
                    break
            if not items:
                continue
            path = os.path.join(global_dir, f"{name}.jsonl")
            total += _write_jsonl(path, items)
        except Exception:
            continue
    return total


def fetch_and_cache_company(ticker: str, curr_date: str, look_back_days: int, per_subreddit_limit: int = 160,
                            subreddits: Optional[List[str]] = None) -> int:
    """Fetch company-related posts for N days and append to company cache JSONL files.

    Note: The reader in reddit_utils will filter by ticker/company terms at read time.
    Returns number of records written.
    """
    cfg = get_config()
    base = cfg.get("data_dir", DATA_DIR) or DATA_DIR
    _, company_dir = _ensure_dirs(base)

    start_ts, end_ts = _get_time_window(curr_date, look_back_days)
    reddit = _load_praw()

    subs = subreddits or [
        "stocks", "StockMarket", "investing", "wallstreetbets", "technology", "Microsoft",
    ]

    total = 0
    for name in subs:
        try:
            sub = reddit.subreddit(name)
            items = []
            for s in _iter_submissions(sub, start_ts, end_ts, hard_limit=max(300, per_subreddit_limit * 4)):
                items.append(_submission_to_record(s))
                if len(items) >= per_subreddit_limit:
                    break
            if not items:
                continue
            path = os.path.join(company_dir, f"{name}.jsonl")
            total += _write_jsonl(path, items)
        except Exception:
            continue
    return total


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch Reddit posts into TradingAgents cache")
    parser.add_argument("--ticker", type=str, default="MSFT")
    parser.add_argument("--date", type=str, required=True, help="YYYY-MM-DD end date")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--global", dest="do_global", action="store_true")
    parser.add_argument("--company", dest="do_company", action="store_true")
    parser.add_argument("--per", dest="per_limit", type=int, default=120)
    args = parser.parse_args()

    wrote = 0
    if args.do_global:
        wrote += fetch_and_cache_global(args.date, args.days, per_subreddit_limit=args.per_limit)
    if args.do_company:
        wrote += fetch_and_cache_company(args.ticker, args.date, args.days, per_subreddit_limit=args.per_limit)
    print(f"Wrote {wrote} records to cache under {os.path.join(DATA_DIR or get_config().get('data_dir', ''), 'reddit_data')}")


