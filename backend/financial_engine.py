"""
financial_engine.py — Deterministic Financial Advisor Engine (v7.0)

New in v7.0:
  predict_expense(user_id)      → 30-day expense forecast from last 7-day data
  calculate_savings(user_id)    → monthly savings = income - expense + messages
  goal_plan(user_id)            → goal progress, daily requirement, status
  enhanced_scheme(user_id, ctx) → savings-aware scheme suggestions

All functions are:
  - Pure deterministic logic (no ML, no randomness)
  - Latency < 10ms (single SQL per function, cached monthly_summary reused)
  - Safe: return graceful fallback dicts on missing data, never raise to caller
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from db import (
    get_monthly_summary   as _db_summary,
    get_category_avg,
    get_last_7_days_expense,
    get_goal,
)

log = logging.getLogger("financial_engine")


# ─── Tamil Fallback Messages ───────────────────────────────────────────────────

# Prediction
_MSG_INSUFFICIENT    = "Konjam naal data collect aaganum. Marubadi check panonga."
_MSG_ZERO_EXPENSE    = "Ithu varai selavu record ilai. Nalla iruku!"

# Savings
_MSG_NO_INCOME       = "Income record panunga — bills mattum irukku."
_MSG_NEGATIVE_SAVING = "⚠️ Selavu income-ai vida jaasthi! Kandu pidikkanum."
_MSG_LOW_SAVING      = "Savings romba kammiya irukku. Konjam kuraikkanum."
_MSG_GOOD_SAVING     = "Savings nalla iruku! RD scheme try pannunga."
_MSG_ZERO_SAVING     = "Income = Selavu. Oru rubaiyum midhakkala. Kuraikka parunga."

# Goal
_MSG_NO_GOAL         = "Goal set aagala. POST /api/goal use pannunga."
_MSG_GOAL_GREAT      = "🎉 Lakshyam neraiverindeenga! Puthusa goal set panonga."
_MSG_GOAL_GOOD       = "Nalla poguthu! Keep it up — daily savings maintain pannunga."
_MSG_GOAL_MID        = "Correct direction la poringa. Konjam speed up pannunga."
_MSG_GOAL_LOW        = "Innum pora vazhiyai inla. Daily savings increase pannunga."
_MSG_GOAL_START      = "Pudhusa start aachhu. Oru naal oru naalaa savings pannunga."

# Schemes
_REASON_RD           = "savings_positive"
_REASON_KMUT         = "low_income"
_REASON_SSY          = "child_expense"
_REASON_MGNREGA      = "no_income"


# ─── Existing Functions (unchanged from v6.2) ─────────────────────────────────

_DEBT_RISK_KEYWORDS = {
    "vaddi", "kandu vaddi", "meter vaddi", "kandu vadi",
    "vadi", "kadan", "informal loan", "moneylender", "blade company",
    "50 paise", "50p vaddi",
}


def has_debt_risk(text: str) -> bool:
    """Returns True if text mentions any high-risk informal lending keyword."""
    lower = text.lower()
    return any(kw in lower for kw in _DEBT_RISK_KEYWORDS)


ANOMALY_THRESHOLD = 1.5


def is_anomaly(user_id: str, category: str, amount: int) -> bool:
    """Return True if amount > 1.5× the 90-day average for (user, category)."""
    if amount <= 0:
        return False
    avg = get_category_avg(user_id, category)
    if avg == 0.0:
        return False
    return amount > (avg * ANOMALY_THRESHOLD)


def monthly_summary(user_id: str) -> dict:
    """Wrapper around db.get_monthly_summary."""
    return _db_summary(user_id)


# ─── Phase 2: Expense Prediction ──────────────────────────────────────────────

def predict_expense(user_id: str) -> Dict[str, Any]:
    """
    Predict monthly expense using last 7 calendar days of data.

    Algorithm:
      1. Fetch (total_7d, active_days) from DB
      2. If active_days < 3 → insufficient data (can't predict reliably)
      3. daily_avg = total_7d / 7   (always divide by 7 calendar days)
      4. predicted_month = daily_avg * 30

    Returns dict:
      status:         "ok" | "insufficient_data" | "no_expense"
      predicted:      int (₹ predicted monthly expense)
      daily_avg:      int (₹ per day average)
      active_days:    int (days with recorded expenses in last 7 days)
      message:        Tamil explanation string
    """
    try:
        total_7d, active_days = get_last_7_days_expense(user_id)
    except Exception as exc:
        log.error("predict_expense DB error: %s", exc)
        return {"status": "error", "predicted": 0, "daily_avg": 0,
                "active_days": 0, "message": _MSG_INSUFFICIENT}

    if total_7d == 0:
        return {
            "status":      "no_expense",
            "predicted":   0,
            "daily_avg":   0,
            "active_days": active_days,
            "message":     _MSG_ZERO_EXPENSE,
        }

    if active_days < 3:
        return {
            "status":      "insufficient_data",
            "predicted":   0,
            "daily_avg":   0,
            "active_days": active_days,
            "message":     _MSG_INSUFFICIENT,
        }

    daily_avg       = total_7d / 7.0
    predicted_month = int(daily_avg * 30)
    daily_avg_int   = int(daily_avg)

    message = (
        f"Ungal daily selavu average ₹{daily_avg_int}. "
        f"Ithu madam approximate ₹{predicted_month} aagum."
    )

    log.debug(
        "PREDICT | user=%s 7d_total=%d active_days=%d daily=%.1f predicted=%d",
        user_id, total_7d, active_days, daily_avg, predicted_month,
    )

    return {
        "status":      "ok",
        "predicted":   predicted_month,
        "daily_avg":   daily_avg_int,
        "active_days": active_days,
        "message":     message,
    }


# ─── Phase 3: Savings Calculation ─────────────────────────────────────────────

def calculate_savings(user_id: str) -> Dict[str, Any]:
    """
    Calculate current-month savings = income - expense.

    Rules (in order):
      income == 0        → no income recorded yet
      savings <= 0       → spending more than earning (warning)
      savings == 0       → exactly break even
      0 < savings < 500  → low savings suggestion
      savings >= 500     → eligible for saving scheme

    Returns dict:
      income:               int
      expense:              int
      savings:              int  (can be negative)
      eligible_for_scheme:  bool (savings >= 500)
      savings_status:       "good" | "low" | "warning" | "no_income" | "zero"
      message:              Tamil string
    """
    try:
        summary = _db_summary(user_id)
    except Exception as exc:
        log.error("calculate_savings DB error: %s", exc)
        return {
            "income": 0, "expense": 0, "savings": 0,
            "eligible_for_scheme": False,
            "savings_status": "error",
            "message": _MSG_NO_INCOME,
        }

    income  = int(summary.get("income_total",  0))
    expense = int(summary.get("expense_total", 0))
    savings = income - expense

    if income == 0:
        status  = "no_income"
        message = _MSG_NO_INCOME
        eligible = False
    elif savings < 0:
        status  = "warning"
        message = _MSG_NEGATIVE_SAVING
        eligible = False
    elif savings == 0:
        status  = "zero"
        message = _MSG_ZERO_SAVING
        eligible = False
    elif savings < 500:
        status  = "low"
        message = _MSG_LOW_SAVING
        eligible = False
    else:
        status  = "good"
        message = _MSG_GOOD_SAVING
        eligible = True

    log.debug(
        "SAVINGS | user=%s income=%d expense=%d savings=%d status=%s",
        user_id, income, expense, savings, status,
    )

    return {
        "income":              income,
        "expense":             expense,
        "savings":             savings,
        "eligible_for_scheme": eligible,
        "savings_status":      status,
        "message":             message,
    }


# ─── Phase 4: Goal-Based Savings ──────────────────────────────────────────────

def goal_plan(user_id: str) -> Dict[str, Any]:
    """
    Fetch the user's savings goal and compute progress.

    Logic:
      goal = get_goal(user_id)
      progress = max(current_month_savings, 0)   # negative savings = 0 progress
      daily_required = ceil(target / duration_days)
      percent = int((progress / target) * 100)

    Status thresholds:
      percent == 0           → "start"
      1 <= percent < 25      → "low"
      25 <= percent < 75     → "mid"
      75 <= percent < 100    → "good"
      percent >= 100         → "achieved"

    Returns dict:
      status:         "ok" | "no_goal" | "achieved" | "error"
      target:         int
      duration_days:  int
      progress:       int  (monthly savings so far, floored at 0)
      daily_required: int  (₹ per day needed to hit goal)
      percent:        int  (0–100+)
      message:        Tamil string
    """
    try:
        goal = get_goal(user_id)
    except Exception as exc:
        log.error("goal_plan get_goal error: %s", exc)
        return {"status": "error", "target": 0, "duration_days": 100,
                "progress": 0, "daily_required": 0, "percent": 0,
                "message": _MSG_NO_GOAL}

    if goal is None:
        return {
            "status":         "no_goal",
            "target":         0,
            "duration_days":  100,
            "progress":       0,
            "daily_required": 0,
            "percent":        0,
            "message":        _MSG_NO_GOAL,
        }

    target        = int(goal["target_amount"])
    duration_days = int(goal["duration_days"])
    daily_req     = math.ceil(target / max(duration_days, 1))

    # Progress = current month savings (never negative)
    savings_data = calculate_savings(user_id)
    progress     = max(savings_data["savings"], 0)

    if target > 0:
        percent = int((progress / target) * 100)
    else:
        percent = 0

    # Status message
    if percent >= 100:
        goal_status = "achieved"
        message     = _MSG_GOAL_GREAT
    elif percent >= 75:
        goal_status = "good"
        message     = f"{_MSG_GOAL_GOOD} ({percent}% complete)"
    elif percent >= 25:
        goal_status = "mid"
        message     = f"{_MSG_GOAL_MID} ({percent}% complete)"
    elif percent >= 1:
        goal_status = "low"
        message     = (
            f"{_MSG_GOAL_LOW} — ₹{daily_req}/day savings pannunga. "
            f"({percent}% complete)"
        )
    else:
        goal_status = "start"
        message     = (
            f"{_MSG_GOAL_START} Target: ₹{target} in {duration_days} days. "
            f"Daily: ₹{daily_req}."
        )

    log.debug(
        "GOAL | user=%s target=%d progress=%d pct=%d status=%s",
        user_id, target, progress, percent, goal_status,
    )

    return {
        "status":         goal_status,
        "target":         target,
        "duration_days":  duration_days,
        "progress":       progress,
        "daily_required": daily_req,
        "percent":        percent,
        "message":        message,
    }


# ─── Phase 5: Enhanced Scheme Suggestion ──────────────────────────────────────

# SSY trigger keywords — child / girl / education related
_SSY_KEYWORDS = {
    "ponnu", "daughter", "girl child", "girl", "school fee",
    "school fees", "ponnukaaga", "penn", "beti", "nandriyam",
}

# Income threshold for KMUT (₹8,000/month ≈ ₹96,000/year)
_KMUT_INCOME_THRESHOLD = 8_000


def enhanced_scheme(user_id: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Savings-behaviour-aware scheme suggestions.

    Rules (evaluated in priority order, all independent):
      1. savings > 0 AND debt == 0  → suggest Post Office RD
      2. 0 < income < 8000          → suggest KMUT
      3. 'ponnu'/'daughter'/etc in text → suggest SSY
      4. income == 0                → suggest MGNREGA

    Args:
      user_id: str
      context: dict with keys:
        - text: str (raw user utterance)
        - income: int (current month income, if already fetched)
        - expense: int (current month expense, if already fetched)
        - savings: int (income - expense)
        - debt_total: int

    Returns list of dicts: [{id, name, description, reason}]
    """
    text      = context.get("text", "").lower()
    income    = int(context.get("income",    0))
    savings   = int(context.get("savings",   0))
    debt      = int(context.get("debt_total", 0))

    matched: List[Dict[str, Any]] = []

    # Rule 1: Savings > 0 and no debt → RD
    if savings > 0 and debt == 0:
        matched.append({
            "id":          "RD",
            "name":        "Post Office Recurring Deposit (RD)",
            "description": "Start saving ₹100/month at the Post Office — safe, guaranteed returns",
            "reason":      _REASON_RD,
        })

    # Rule 2: Low income → KMUT
    if 0 < income < _KMUT_INCOME_THRESHOLD:
        matched.append({
            "id":          "KMUT",
            "name":        "Kalaignar Magalir Urimai Thittam",
            "description": "₹1000/month for women in Tamil Nadu with household income < ₹2.5 lakh/yr",
            "reason":      _REASON_KMUT,
        })

    # Rule 3: Child-related expense → SSY
    if any(kw in text for kw in _SSY_KEYWORDS):
        matched.append({
            "id":          "SSY",
            "name":        "Sukanya Samriddhi Yojana (SSY)",
            "description": "High-interest Post Office savings for girl child's education/marriage",
            "reason":      _REASON_SSY,
        })

    # Rule 4: No income at all → MGNREGA
    if income == 0:
        matched.append({
            "id":          "MGNREGA",
            "name":        "MGNREGA 100-Day Work Scheme",
            "description": "Guaranteed 100 days of wage employment per year for rural households",
            "reason":      _REASON_MGNREGA,
        })

    log.debug(
        "ENHANCED SCHEME | user=%s income=%d savings=%d debt=%d matched=%s",
        user_id, income, savings, debt, [s["id"] for s in matched],
    )

    return matched
