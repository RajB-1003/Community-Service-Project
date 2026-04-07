"""
session_store.py — In-memory multi-turn conversation state.

Handles WAITING_FOR_AMOUNT and WAITING_FOR_CATEGORY states
so the system can ask a follow-up question when a user says
something like "selavu panniten" (intent clear, amount missing).

Design decisions
----------------
- Pure Python dict — no Redis, no DB writes for ephemeral state
- TTL: 5 minutes per session (auto-expiry on next read)
- Thread-safe via a simple dict-level lock (GIL is sufficient for CPython)
- Each user_id has exactly one pending session at a time

State machine
-------------
  IDLE
    │ intent found, amount missing → ask "evlo selavu?"
    ▼
  WAITING_FOR_AMOUNT  (stores: intent, category, tx_date)
    │ user sends amount → complete transaction
    ▼
  IDLE

  IDLE
    │ amount found, intent missing → ask for clarification
    ▼
  WAITING_FOR_CATEGORY  (stores: amount, tx_date)
    │ user sends intent keyword → complete transaction
    ▼
  IDLE
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Literal

# ─── Constants ────────────────────────────────────────────────────────────────

SESSION_TTL_SECONDS = 300   # 5 minutes — after this, session is discarded

SessionStatus = Literal["WAITING_FOR_AMOUNT", "WAITING_FOR_CATEGORY", "IDLE"]

# ─── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class PendingSession:
    user_id:   str
    status:    SessionStatus
    intent:    Optional[str]    = None
    category:  Optional[str]    = None
    amount:    Optional[int]    = None
    tx_date:   date             = field(default_factory=date.today)
    created_at: datetime        = field(default_factory=datetime.utcnow)

    def is_expired(self) -> bool:
        elapsed = (datetime.utcnow() - self.created_at).total_seconds()
        return elapsed > SESSION_TTL_SECONDS


# ─── Store ────────────────────────────────────────────────────────────────────

_store: dict[str, PendingSession] = {}
_lock  = threading.Lock()


def get_session(user_id: str) -> Optional[PendingSession]:
    """Return active pending session for user, or None if idle/expired."""
    with _lock:
        s = _store.get(user_id)
        if s is None:
            return None
        if s.is_expired():
            del _store[user_id]
            return None
        return s


def set_session(session: PendingSession) -> None:
    """Store or update a session."""
    with _lock:
        _store[session.user_id] = session


def clear_session(user_id: str) -> None:
    """Remove session (transaction completed or abandoned)."""
    with _lock:
        _store.pop(user_id, None)


def pending_count() -> int:
    """Diagnostic: number of active (non-expired) sessions."""
    with _lock:
        return sum(1 for s in _store.values() if not s.is_expired())
