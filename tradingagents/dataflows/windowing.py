from __future__ import annotations

from datetime import datetime
from dateutil.relativedelta import relativedelta
from typing import Dict, Tuple, List


def _style_ranges(style: str) -> Dict[str, Tuple[int, int]]:
    """Return recommended window ranges (min_days, max_days) per category by fund style.

    Categories: "technical", "news", "social", "fundamentals".
    Values are trading-day approximations for news/social as calendar days.
    """
    style = (style or "medium_turnover").lower()
    if style == "high_turnover":  # 数日–2周
        return {
            "technical": (10, 20),
            "news": (1, 5),
            "social": (3, 10),
            "fundamentals": (30, 30),
        }
    if style == "low_turnover":  # 1–6个月
        return {
            "technical": (60, 120),
            "news": (21, 63),
            "social": (21, 63),
            "fundamentals": (30, 30),
        }
    # medium_turnover: 2–8周（默认）
    return {
        "technical": (20, 60),
        "news": (7, 21),
        "social": (7, 21),
        "fundamentals": (30, 30),
    }


def _clip(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def compute_lookback_days(config: Dict, category: str) -> int:
    """Compute a single look-back window in days based on fund style and holding period.

    Heuristic: pick ~0.7 * holding_period_days, then clip into style range.
    Falls back to fixed defaults if windowing_mode != "adaptive".
    """
    mode = str(config.get("windowing_mode", "adaptive")).lower()
    if mode != "adaptive":
        # current fixed defaults in the codebase
        if category == "technical":
            return 30
        if category in ("news", "social"):
            return 7
        return 30

    style = str(config.get("fund_style", "medium_turnover")).lower()
    H = int(config.get("hold_period_days", 28))
    base = max(1, int(round(0.7 * H)))
    lo, hi = _style_ranges(style).get(category, (7, 30))
    return _clip(base, lo, hi)


def compute_start_end(curr_date: str, look_back_days: int) -> Tuple[str, str]:
    """Return (start_date, end_date) as YYYY-MM-DD strings for a given look-back from curr_date."""
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - relativedelta(days=int(look_back_days))
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def compute_multi_windows(config: Dict, category: str) -> List[int]:
    """Return list of windows [fast, mid, slow] for a category.

    Defaults to [3, 14, 60] calendar days if not configured.
    """
    key = f"multi_{category}_windows"
    default = [3, 14, 60]
    windows = config.get(key)
    if isinstance(windows, (list, tuple)) and len(windows) == 3:
        try:
            vals = [int(x) for x in windows]
            return [max(1, v) for v in vals]
        except Exception:
            return default
    return default


def compute_half_lives(config: Dict, category: str) -> List[int]:
    """Return list of half-life days [fast, mid, slow] for a category.

    Defaults to [2, 7, 30].
    """
    key = f"half_life_{category}_days"
    default = [2, 7, 30]
    hls = config.get(key)
    if isinstance(hls, (list, tuple)) and len(hls) == 3:
        try:
            vals = [int(x) for x in hls]
            return [max(1, v) for v in vals]
        except Exception:
            return default
    return default


