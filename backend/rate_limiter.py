"""
rate_limiter.py — Lightweight in-memory rate limiter.

Implementation: Token-bucket per (user_id) — no Redis, no external deps.
Resets every WINDOW_SECONDS.

Limits (configurable at top of file):
  - MAX_REQUESTS_PER_WINDOW per user_id

Usage (in any route):
    from rate_limiter import check_rate_limit
    check_rate_limit(user_id)          # raises HTTP 429 if exceeded

Thread-safe via lock around counter dict.
"""

from __future__ import annotations

import threading
import time
from fastapi import HTTPException

# ─── Config ───────────────────────────────────────────────────────────────────

MAX_REQUESTS_PER_WINDOW = 30    # max requests per user per window
WINDOW_SECONDS          = 60    # rolling window length in seconds

# ─── Store ────────────────────────────────────────────────────────────────────
# { user_id: (request_count, window_start_timestamp) }

_counters: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def check_rate_limit(user_id: str) -> None:
    """
    Increment request counter for user_id.
    Raises HTTP 429 if the user has exceeded MAX_REQUESTS_PER_WINDOW
    within the current WINDOW_SECONDS window.
    """
    now = time.monotonic()
    with _lock:
        count, window_start = _counters.get(user_id, (0, now))

        # Reset window if expired
        if now - window_start >= WINDOW_SECONDS:
            count        = 0
            window_start = now

        count += 1
        _counters[user_id] = (count, window_start)

        if count > MAX_REQUESTS_PER_WINDOW:
            remaining = int(WINDOW_SECONDS - (now - window_start))
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many requests. Please wait {remaining}s before trying again. "
                    "Kொஞ்சம் wait பண்ணுங்க."
                ),
                headers={"Retry-After": str(remaining)},
            )
