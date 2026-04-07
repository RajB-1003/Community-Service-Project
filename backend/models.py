"""
models.py — Hardened Pydantic data contracts (v7.0 — Financial Advisor)

Changes from v6.2:
  - NEW: GoalInsight, FinancialInsights nested response models
  - NEW: has_insights: bool + financial_insights field in AnalyzeResponse
  - NEW: SetGoalRequest, SetGoalResponse for POST /api/goal
  - NEW: InsightsResponse for GET /api/insights
  - BACKWARD COMPATIBLE: All v6.2 fields unchanged
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


# ─── User ID Validation ───────────────────────────────────────────────────────

_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def _validate_user_id(v: str) -> str:
    """Accept alphanumeric + underscore/dash, max 64 chars."""
    v = v.strip()
    if not v:
        return "default"
    if not _USER_ID_RE.match(v):
        raise ValueError(
            "user_id must be 1–64 alphanumeric characters (a-z, 0-9, _, -)."
        )
    return v


# ─── Request Models ───────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """POST /api/analyze — text input."""
    text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Raw user utterance (Tamil / Tanglish / English). Max 500 chars.",
    )
    user_id: str = Field(
        default="default",
        description="Device or anonymous user identifier.",
    )

    @field_validator("text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()

    @field_validator("user_id")
    @classmethod
    def validate_uid(cls, v: str) -> str:
        return _validate_user_id(v)


class FollowUpRequest(BaseModel):
    """POST /api/followup — second-turn input in a multi-turn conversation."""
    text: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(default="default")

    @field_validator("text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()

    @field_validator("user_id")
    @classmethod
    def validate_uid(cls, v: str) -> str:
        return _validate_user_id(v)


class SetGoalRequest(BaseModel):
    """
    POST /api/goal — create or replace a savings goal.

    Rules:
      - target_amount must be >= 100 (at least ₹100 goal)
      - duration_days defaults to 100 if not supplied
      - duration_days must be between 1 and 3650 (10 years max)
    """
    user_id:       str = Field(default="default")
    target_amount: int = Field(
        ..., ge=100, le=10_000_000,
        description="Target savings amount in rupees. Minimum ₹100.",
    )
    duration_days: int = Field(
        default=100, ge=1, le=3650,
        description="Number of days to achieve the goal. Default: 100.",
    )

    @field_validator("user_id")
    @classmethod
    def validate_uid(cls, v: str) -> str:
        return _validate_user_id(v)


# ─── Nested Financial Insight Models ─────────────────────────────────────────

class GoalInsight(BaseModel):
    """Goal progress snapshot embedded in financial_insights."""
    status:         str = Field(default="no_goal",
                                description="no_goal | start | low | mid | good | achieved | error")
    target:         int = Field(default=0,   description="Goal target in rupees")
    duration_days:  int = Field(default=100, description="Goal duration in days")
    progress:       int = Field(default=0,   description="Current savings progress (≥ 0)")
    daily_required: int = Field(default=0,   description="Daily savings needed to hit goal")
    percent:        int = Field(default=0,   description="Completion percentage (0–100+)")
    message:        str = Field(default="",  description="Tamil status message")


class PredictionInsight(BaseModel):
    """Expense prediction snapshot embedded in financial_insights."""
    status:      str = Field(default="insufficient_data",
                             description="ok | insufficient_data | no_expense | error")
    predicted:   int = Field(default=0, description="Forecast monthly expense in rupees")
    daily_avg:   int = Field(default=0, description="Daily average expense in rupees")
    active_days: int = Field(default=0, description="Days with expense data in last 7 days")
    message:     str = Field(default="", description="Tamil explanation")


class FinancialInsights(BaseModel):
    """
    Full financial snapshot — embedded in AnalyzeResponse when has_insights=True.

    Selectively computed:
      - ONLY when a transaction is successfully logged (action == 'log' or 'scheme')
      - OR when GET /api/insights is called directly
      - NEVER computed for retry, followup, or alert (anomaly/debt) responses
    """
    savings:           int             = Field(default=0)
    income:            int             = Field(default=0)
    expense:           int             = Field(default=0)
    savings_status:    str             = Field(default="no_income",
                                               description="good | low | warning | zero | no_income")
    savings_message:   str             = Field(default="")
    prediction:        PredictionInsight = Field(default_factory=PredictionInsight)
    goal:              GoalInsight       = Field(default_factory=GoalInsight)
    schemes:           List[Dict[str, Any]] = Field(default_factory=list,
                                                description="Enhanced scheme suggestions")


# ─── Response Models ──────────────────────────────────────────────────────────

class AnalyzeResponse(BaseModel):
    """
    Strict JSON contract returned by /api/analyze, /api/followup, /api/process.

    action values:
      log       → acknowledged, stored, no special action needed
      alert     → highlight (anomaly or debt risk)
      scheme    → show scheme recommendation card
      retry     → unrecognised — ask user to repeat
      followup  → system is waiting for more info (multi-turn)
      goal_hint → user mentioned goal intent; redirect to /api/goal

    has_insights:
      True  → financial_insights block is populated (transaction was logged)
      False → financial_insights block is empty defaults (not computed)
    """
    text:                str              = Field(description="Original or transcribed user text")
    intent:              str              = Field(description="income | expense | debt_repayment | set_goal | unknown")
    category:            str              = Field(description="Food | Travel | Debt | Health | Education | Household | SHG | Income | Other")
    amount:              int              = Field(description="Extracted rupee amount; 0 if not found")
    response:            str              = Field(description="Human-readable reply (Tamil / Tanglish)")
    action:              str              = Field(description="log | alert | scheme | retry | followup | goal_hint")
    confidence:          str              = Field(default="HIGH", description="HIGH | MEDIUM | LOW | NONE")
    needs_followup:      bool             = Field(default=False)
    has_insights:        bool             = Field(default=False,
                                                  description="True = financial_insights is populated")
    financial_insights:  FinancialInsights = Field(
                                                  default_factory=FinancialInsights,
                                                  description="Populated only when has_insights=True")


# ─── Goal & Insights API Models ───────────────────────────────────────────────

class SetGoalResponse(BaseModel):
    """Response for POST /api/goal."""
    status:        str = Field(description="ok | error")
    user_id:       str
    target_amount: int
    duration_days: int
    daily_required: int = Field(description="Rupees per day needed to hit the goal")
    message:       str  = Field(description="Tamil confirmation message")


class InsightsResponse(BaseModel):
    """Response for GET /api/insights — full financial snapshot."""
    user_id:   str
    savings:   Dict[str, Any]
    prediction: Dict[str, Any]
    goal:      Dict[str, Any]
    schemes:   List[Dict[str, Any]]


# ─── DB / History Models ──────────────────────────────────────────────────────

class TransactionRecord(BaseModel):
    """Row returned from the transactions table."""
    id:       int
    user_id:  str
    amount:   int
    type:     str
    category: str
    tx_date:  Optional[str] = None
    ts:       str


class MonthlySummary(BaseModel):
    """Response for GET /api/summary."""
    user_id:           str
    income_total:      int
    expense_total:     int
    debt_total:        int
    transaction_count: int
    suggested_action:  Optional[str] = None
