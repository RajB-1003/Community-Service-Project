"""
decision_engine.py — Deterministic Priority Router (v7.0 — Financial Advisor)

Changes from v6.2:
  1. NEW PRIORITIES:
       Priority 4  → GOAL HINT   (safe redirect for set_goal intent)
       Priority 5  → DEBT RISK   (unchanged)
       Priority 6  → GOAL STATUS (show goal progress after successful log)
       Priority 7  → PREDICTION  (expense forecast as secondary context)
       Priority 8  → SCHEME      (enhanced_scheme replaces match_schemes)
       Priority 9  → LOG         (default acknowledgment)
  2. Decision class gains `financial_insights: dict` field.
  3. financial_insights is built and attached at Priority 6+ (LOG/SCHEME paths).
     It is EMPTY dict {} for RETRY / FOLLOWUP / ANOMALY / DEBT paths.
  4. `decide()` signature extended with `savings_data` optional kwarg.

Priority order:
  1. RETRY         ← confidence NONE
  2. FOLLOWUP      ← missing amount
  3. FOLLOWUP      ← missing category
  4. GOAL HINT     ← set_goal intent detected (NLU safe mode)
  5. ANOMALY       ← amount > 1.5× avg
  6. DEBT RISK     ← informal lending keywords
  7. GOAL STATUS   ← goal progress insight (only if goal set)
  8. PREDICTION    ← expense forecast in response
  9. SCHEME        ← enhanced_scheme() suggestions
 10. LOG           ← default acknowledgment
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from financial_engine import is_anomaly, has_debt_risk

log = logging.getLogger("decision_engine")


# ─── Response Templates ───────────────────────────────────────────────────────

_RETRY_MSG       = (
    "Puriyala, marubadi sollunga. "
    "Example: 'kooli 500 vandhuchu' or 'selavu 200 aachu'."
)
_ASK_AMOUNT_MSG  = "Evlo amount? Please sollunga."
_ASK_CATEGORY_MSG= "Idhu enna selavu? (food, travel, hospital?)"

_GOAL_HINT_MSG   = (
    "Neenga goal set panna poringa pola iruku! 🎯 "
    "Duration evlo naal nu sollunga, apuram POST /api/goal use pannunga. "
    "Example: {{ \"target_amount\": 10000, \"duration_days\": 100 }}"
)

_ANOMALY_MSG = (
    "Ithu {category} selavukku romba jaasthi — ₹{amount}! "
    "Ungal average ₹{avg:.0f} thaan. Correct-aa?"
)
_DEBT_MSG = (
    "⚠️ Vaddi / kadan katradhu romba risk! "
    "Kandu vaddi, meter vaddi kaaranoda irunga vilagi irunga — "
    "idhu unga valkaiku romba paadhippu seyyum. "
    "Maha kudumbam or bank loan-ku mari ponga."
)
_SCHEME_MSG      = "Nallathu! Neenga apply pannalamg: {names}. Ungal panchayat office-ku poi check panunga."
_LOG_INCOME_MSG  = "₹{amount} {category} income save aachhu. Nallathu! 💚"
_LOG_EXPENSE_MSG = "₹{amount} {category} selavu noted."
_LOG_DEBT_MSG    = "₹{amount} kadan / vaddi record aachu."
_LOG_UNKNOWN_MSG = "Record aachu. Thodarnthu sollunga!"
_ERROR_MSG       = "System thappu. Konjam wait panni marubadi try panunga."


# ─── Decision Object ──────────────────────────────────────────────────────────

class Decision:
    """
    Carries the primary response AND optional financial insights.

    financial_insights is empty dict for fast paths (RETRY/FOLLOWUP/ANOMALY/DEBT).
    It is a populated FinancialInsights-compatible dict for LOG/SCHEME paths.
    """
    __slots__ = ("action", "response", "needs_followup", "financial_insights")

    def __init__(
        self,
        action:              str,
        response:            str,
        needs_followup:      bool = False,
        financial_insights:  Optional[Dict[str, Any]] = None,
    ) -> None:
        self.action             = action
        self.response           = response
        self.needs_followup     = needs_followup
        self.financial_insights = financial_insights or {}


# ─── Financial Insights Builder ───────────────────────────────────────────────

def _build_financial_insights(
    user_id:  str,
    text:     str,
    income:   int,
    expense:  int,
    savings:  int,
    debt:     int,
) -> Dict[str, Any]:
    """
    Build the financial_insights dict by calling the financial engine functions.

    Called ONLY from LOG and SCHEME priority paths.
    All sub-functions are safe (no exceptions propagate).
    """
    from financial_engine import predict_expense, goal_plan, enhanced_scheme

    prediction_data = predict_expense(user_id)
    goal_data       = goal_plan(user_id)

    context = {
        "text":       text,
        "income":     income,
        "expense":    expense,
        "savings":    savings,
        "debt_total": debt,
    }
    schemes = enhanced_scheme(user_id, context)

    return {
        "savings":         savings,
        "income":          income,
        "expense":         expense,
        "savings_status":  _infer_savings_status(income, savings),
        "savings_message": _savings_summary_msg(income, savings),
        "prediction": {
            "status":      prediction_data.get("status", "insufficient_data"),
            "predicted":   prediction_data.get("predicted", 0),
            "daily_avg":   prediction_data.get("daily_avg", 0),
            "active_days": prediction_data.get("active_days", 0),
            "message":     prediction_data.get("message", ""),
        },
        "goal": {
            "status":         goal_data.get("status", "no_goal"),
            "target":         goal_data.get("target", 0),
            "duration_days":  goal_data.get("duration_days", 100),
            "progress":       goal_data.get("progress", 0),
            "daily_required": goal_data.get("daily_required", 0),
            "percent":        goal_data.get("percent", 0),
            "message":        goal_data.get("message", ""),
        },
        "schemes": schemes,
    }


def _infer_savings_status(income: int, savings: int) -> str:
    if income == 0:
        return "no_income"
    if savings < 0:
        return "warning"
    if savings == 0:
        return "zero"
    if savings < 500:
        return "low"
    return "good"


def _savings_summary_msg(income: int, savings: int) -> str:
    status = _infer_savings_status(income, savings)
    msgs = {
        "no_income": "Income record panunga — bills mattum irukku.",
        "warning":   "⚠️ Selavu income-ai vida jaasthi! Kandu pidikkanum.",
        "zero":      "Income = Selavu. Oru rubaiyum midhakkala.",
        "low":       "Savings romba kammiya irukku. Konjam kuraikkanum.",
        "good":      "Savings nalla iruku! RD scheme try pannunga.",
    }
    return msgs.get(status, "")


# ─── Decision Engine ──────────────────────────────────────────────────────────

def decide(
    *,
    text:             str,
    intent:           str,
    category:         str,
    amount:           int,
    user_id:          str,
    income_total:     int,
    debt_total:       int,
    confidence:       str  = "HIGH",
    missing_amount:   bool = False,
    missing_category: bool = False,
    expense_total:    int  = 0,       # ← new in v7.0 (passed from _run_analysis)
) -> Decision:
    """
    Priority decision router (v7.0 — 10-level).

    Priority order:
      1. RETRY        → confidence NONE
      2. FOLLOWUP     → missing amount
      3. FOLLOWUP     → missing category
      4. GOAL HINT    → set_goal intent (safe redirect, no DB write)
      5. ANOMALY      → amount > 1.5× avg
      6. DEBT RISK    → informal lending keywords
      7. GOAL STATUS  → goal set & income or expense logged → show progress
      8. PREDICTION   → add forecast to LOG response
      9. SCHEME       → enhanced scheme suggestions
     10. LOG          → default acknowledgment (always has financial_insights)
    """

    # ── Priority 1: RETRY ────────────────────────────────────────────────────
    if confidence == "NONE":
        return Decision(action="retry", response=_RETRY_MSG)

    # ── Priority 2: FOLLOWUP — missing amount ────────────────────────────────
    if missing_amount:
        return Decision(action="followup", response=_ASK_AMOUNT_MSG, needs_followup=True)

    # ── Priority 3: FOLLOWUP — missing category ──────────────────────────────
    if missing_category and intent == "expense" and amount > 0:
        return Decision(action="followup", response=_ASK_CATEGORY_MSG, needs_followup=True)

    # ── Priority 4: GOAL HINT (safe mode — never writes to DB) ──────────────
    if intent == "set_goal":
        return Decision(action="goal_hint", response=_GOAL_HINT_MSG)

    # ── Priority 5: ANOMALY ──────────────────────────────────────────────────
    if amount > 0 and is_anomaly(user_id, category, amount):
        from db import get_category_avg
        avg = get_category_avg(user_id, category)
        return Decision(
            action="alert",
            response=_ANOMALY_MSG.format(category=category, amount=amount, avg=avg),
        )

    # ── Priority 6: DEBT RISK ────────────────────────────────────────────────
    if has_debt_risk(text):
        return Decision(action="alert", response=_DEBT_MSG)

    # ── Compute savings for priorities 7–10 ──────────────────────────────────
    savings = income_total - expense_total

    # ── Priority 7: GOAL STATUS — show progress if goal is set ──────────────
    # Triggered when income OR expense was just logged (a real transaction)
    if intent in ("income", "expense") and amount > 0:
        try:
            from financial_engine import goal_plan
            gp = goal_plan(user_id)
            if gp.get("status") not in ("no_goal", "error"):
                # Build full insights + prepend goal progress to log msg
                insights = _build_financial_insights(
                    user_id=user_id, text=text,
                    income=income_total, expense=expense_total,
                    savings=savings, debt=debt_total,
                )
                base_msg = (
                    _LOG_INCOME_MSG.format(amount=amount, category=category)
                    if intent == "income"
                    else _LOG_EXPENSE_MSG.format(amount=amount, category=category)
                )
                goal_suffix = f" 🎯 Goal: {gp['percent']}% — {gp['message']}"
                return Decision(
                    action="log",
                    response=base_msg + goal_suffix,
                    financial_insights=insights,
                )
        except Exception as exc:
            log.warning("Goal status fetch failed (non-fatal): %s", exc)

    # ── Priority 8: PREDICTION — attach forecast to income/expense LOG ───────
    if intent in ("income", "expense", "debt_repayment") and amount > 0:
        try:
            from financial_engine import predict_expense as _predict
            pred = _predict(user_id)
            insights = _build_financial_insights(
                user_id=user_id, text=text,
                income=income_total, expense=expense_total,
                savings=savings, debt=debt_total,
            )
            if intent == "income":
                base_msg = _LOG_INCOME_MSG.format(amount=amount, category=category)
            elif intent == "expense":
                base_msg = _LOG_EXPENSE_MSG.format(amount=amount, category=category)
            else:
                base_msg = _LOG_DEBT_MSG.format(amount=amount, category=category)

            if pred.get("status") == "ok":
                base_msg += f" 📊 Predicted month total: ₹{pred['predicted']}."

            return Decision(action="log", response=base_msg, financial_insights=insights)
        except Exception as exc:
            log.warning("Prediction fetch failed (non-fatal): %s", exc)

    # ── Priority 9: SCHEME ───────────────────────────────────────────────────
    try:
        from financial_engine import enhanced_scheme
        ctx = {
            "text":       text,
            "income":     income_total,
            "expense":    expense_total,
            "savings":    savings,
            "debt_total": debt_total,
        }
        schemes = enhanced_scheme(user_id, ctx)
        if schemes:
            names    = ", ".join(s["name"] for s in schemes)
            insights = _build_financial_insights(
                user_id=user_id, text=text,
                income=income_total, expense=expense_total,
                savings=savings, debt=debt_total,
            )
            return Decision(
                action="scheme",
                response=_SCHEME_MSG.format(names=names),
                financial_insights=insights,
            )
    except Exception as exc:
        log.warning("Scheme match failed (non-fatal): %s", exc)

    # ── Priority 10: DEFAULT LOG ─────────────────────────────────────────────
    if intent == "income":
        msg = _LOG_INCOME_MSG.format(amount=amount, category=category)
    elif intent == "expense":
        msg = _LOG_EXPENSE_MSG.format(amount=amount, category=category)
    elif intent == "debt_repayment":
        msg = _LOG_DEBT_MSG.format(amount=amount, category=category)
    else:
        msg = _LOG_UNKNOWN_MSG

    # Build insights for all successful LOG paths
    try:
        insights = _build_financial_insights(
            user_id=user_id, text=text,
            income=income_total, expense=expense_total,
            savings=savings, debt=debt_total,
        )
    except Exception as exc:
        log.warning("Insights build failed (non-fatal): %s", exc)
        insights = {}

    return Decision(action="log", response=msg, financial_insights=insights)
