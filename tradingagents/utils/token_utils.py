"""
Lightweight token utilities without external deps.

We implement conservative estimation to avoid adding tiktoken dependency.
If you later add tiktoken, swap estimators accordingly.
"""

from __future__ import annotations

from typing import Any, Iterable


# Rough context windows for common models; default to 8192
MODEL_CONTEXT_LIMITS = {
    "gpt-5": 8192,
    "gpt-5-mini": 8192,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}


def context_limit_for_model(model: str | None) -> int:
    if not model:
        return 8192
    for key, limit in MODEL_CONTEXT_LIMITS.items():
        if str(model).lower().startswith(key):
            return int(limit)
    # Fallback if model unknown
    return 8192


def estimate_tokens_for_text(text: Any, model: str | None = None) -> int:
    """
    Very conservative char->token mapping.
    - Assume 1 token ~= 3 chars on average (English + symbols).
    - Add a small overhead to be safe.
    """
    try:
        s = str(text or "")
    except Exception:
        s = ""
    if not s:
        return 0
    approx = int(len(s) / 3.0) + 8
    return approx


def estimate_tokens_for_messages(messages: Iterable[Any], model: str | None = None) -> int:
    total = 0
    for m in messages or []:
        try:
            content = getattr(m, "content", m)
        except Exception:
            content = m
        total += estimate_tokens_for_text(content, model)
        # Add small overhead per message
        total += 8
    return total


