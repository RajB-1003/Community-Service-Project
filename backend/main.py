"""
main.py — Project Nyaya Financial Advisor  v7.0
Voice-first micro-finance advisor for rural Tamil Nadu users.

New in v7.0:
  ✓ financial_insights block in AnalyzeResponse (selective — LOG/SCHEME only)
  ✓ has_insights: bool flag on every response
  ✓ POST /api/goal      — create / replace a savings goal
  ✓ GET  /api/insights  — full financial snapshot on demand
  ✓ decide() called with expense_total for savings calculation
  ✓ set_goal intent → goal_hint (safe — never writes to DB directly)

Routes
------
POST /api/analyze           → text input  → AnalyzeResponse (with financial_insights)
POST /api/followup          → multi-turn second turn
POST /api/process           → audio input → AnalyzeResponse
POST /api/goal              → set savings goal → SetGoalResponse      [NEW v7.0]
GET  /api/insights          → full financial snapshot                 [NEW v7.0]
GET  /api/history           → recent transactions
GET  /api/summary           → monthly totals
GET  /api/health            → health probe
"""

from __future__ import annotations

import logging
import math
import os
import traceback
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from models import (
    AnalyzeRequest, AnalyzeResponse, FollowUpRequest,
    FinancialInsights, GoalInsight, PredictionInsight,
    SetGoalRequest, SetGoalResponse,
    InsightsResponse,
    TransactionRecord, MonthlySummary,
)
from nlu import parse
from decision_engine import decide
from financial_engine import (
    monthly_summary,
    predict_expense,
    calculate_savings,
    goal_plan,
    enhanced_scheme,
)
from db import (
    init_db, insert_transaction,
    get_recent_transactions, is_db_connected, touch_user,
    upsert_goal, get_goal,
)
from audio import validate_audio, transcribe
from session_store import (
    get_session, set_session, clear_session, PendingSession, pending_count,
)
from rate_limiter import check_rate_limit

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv()

BASE_DIR   = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# ─── Lifecycle ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="Rural Finance Advisor — Nyaya v7.0",
    description=(
        "Deterministic, offline-friendly voice-first financial advisor "
        "for rural Tamil Nadu. Rule-based NLU + financial intelligence. No LLM."
    ),
    version="7.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Tamil Error Responses ────────────────────────────────────────────────────

_ERR_SYSTEM  = "System thappu. Konjam wait panni marubadi try panunga."
_ERR_INVALID = "Request sari illai. Marubadi try panunga."


# ─── Financial Insights Builder ───────────────────────────────────────────────

def _build_insights(user_id: str, text: str, income: int, expense: int, debt: int) -> FinancialInsights:
    """
    Build a FinancialInsights Pydantic model for a given user.

    Called ONLY when action is 'log' or 'scheme' (selective computation).
    All sub-functions are safe (never raise).
    """
    savings = income - expense

    # Savings
    sav_data = calculate_savings(user_id)

    # Prediction
    pred_data = predict_expense(user_id)
    prediction = PredictionInsight(
        status      = pred_data.get("status", "insufficient_data"),
        predicted   = pred_data.get("predicted", 0),
        daily_avg   = pred_data.get("daily_avg", 0),
        active_days = pred_data.get("active_days", 0),
        message     = pred_data.get("message", ""),
    )

    # Goal
    gp_data = goal_plan(user_id)
    goal = GoalInsight(
        status         = gp_data.get("status", "no_goal"),
        target         = gp_data.get("target", 0),
        duration_days  = gp_data.get("duration_days", 100),
        progress       = gp_data.get("progress", 0),
        daily_required = gp_data.get("daily_required", 0),
        percent        = gp_data.get("percent", 0),
        message        = gp_data.get("message", ""),
    )

    # Enhanced schemes
    ctx = {
        "text":       text,
        "income":     income,
        "expense":    expense,
        "savings":    savings,
        "debt_total": debt,
    }
    schemes = enhanced_scheme(user_id, ctx)

    return FinancialInsights(
        savings          = savings,
        income           = income,
        expense          = expense,
        savings_status   = sav_data.get("savings_status", "no_income"),
        savings_message  = sav_data.get("message", ""),
        prediction       = prediction,
        goal             = goal,
        schemes          = schemes,
    )


# ─── Core Analysis Pipeline ───────────────────────────────────────────────────

def _run_analysis(text: str, user_id: str) -> AnalyzeResponse:
    """
    Shared inner pipeline: NLU → DB fetch → DB insert → Decide → Response.

    v7.0 changes:
    - expense_total passed to decide() for savings calculation
    - financial_insights built and attached when action is 'log' or 'scheme'
    - set_goal intent short-circuits to goal_hint (no insert, no insights)
    - has_insights flag set correctly
    """
    touch_user(user_id)

    # ── Step 1: NLU ──────────────────────────────────────────────────────────
    parsed = parse(text)
    log.info(
        "ANALYZE | user=%s input=%r → intent=%s cat=%s amt=%d conf=%s",
        user_id, text, parsed.intent, parsed.category, parsed.amount, parsed.confidence,
    )

    # ── Step 2: Current month totals (pre-insert) ────────────────────────────
    summary      = monthly_summary(user_id)
    income_total = int(summary.get("income_total",  0))
    expense_total= int(summary.get("expense_total", 0))
    debt_total   = int(summary.get("debt_total",    0))
    log.debug("PRE-INSERT TOTALS | user=%s income=%d expense=%d debt=%d",
              user_id, income_total, expense_total, debt_total)

    # ── Step 3: Decide (before insert, so followup/goal_hint short-circuit) ──
    decision = decide(
        text             = text,
        intent           = parsed.intent,
        category         = parsed.category,
        amount           = parsed.amount,
        user_id          = user_id,
        income_total     = income_total,
        debt_total       = debt_total,
        confidence       = parsed.confidence,
        missing_amount   = parsed.missing_amount,
        missing_category = parsed.missing_category,
        expense_total    = expense_total,
    )

    # ── Fast-path: followup / goal_hint / retry / alert ──────────────────────
    if decision.action in ("followup", "goal_hint", "retry", "alert"):
        if decision.action == "followup":
            if parsed.missing_amount:
                set_session(PendingSession(
                    user_id  = user_id,
                    status   = "WAITING_FOR_AMOUNT",
                    intent   = parsed.intent,
                    category = parsed.category,
                    tx_date  = parsed.tx_date,
                ))
                log.info("SESSION | user=%s → WAITING_FOR_AMOUNT (intent=%s cat=%s)",
                         user_id, parsed.intent, parsed.category)
            elif parsed.missing_category:
                set_session(PendingSession(
                    user_id  = user_id,
                    status   = "WAITING_FOR_CATEGORY",
                    amount   = parsed.amount,
                    tx_date  = parsed.tx_date,
                ))
                log.info("SESSION | user=%s → WAITING_FOR_CATEGORY (amt=%d)",
                         user_id, parsed.amount)

        return AnalyzeResponse(
            text           = text,
            intent         = parsed.intent,
            category       = parsed.category,
            amount         = parsed.amount,
            response       = decision.response,
            action         = decision.action,
            confidence     = parsed.confidence,
            needs_followup = decision.needs_followup,
            has_insights   = False,
            # financial_insights defaults to empty FinancialInsights()
        )

    # ── Step 4: Persist — only when amount > 0 and intent is actionable ──────
    inserted_row = 0
    if parsed.amount > 0 and parsed.intent not in ("unknown", "set_goal"):
        inserted_row = insert_transaction(
            user_id  = user_id,
            amount   = parsed.amount,
            tx_type  = parsed.intent,
            category = parsed.category,
            tx_date  = parsed.tx_date,
        )
    elif parsed.amount > 0 and parsed.intent == "unknown":
        inserted_row = insert_transaction(
            user_id  = user_id,
            amount   = parsed.amount,
            tx_type  = "expense",
            category = parsed.category,
            tx_date  = parsed.tx_date,
        )
        log.warning("FALLBACK INSERT | user=%s amt=%d as expense (intent=unknown)",
                    user_id, parsed.amount)

    # Refresh totals after insert
    if inserted_row:
        summary       = monthly_summary(user_id)
        income_total  = int(summary.get("income_total",  0))
        expense_total = int(summary.get("expense_total", 0))
        debt_total    = int(summary.get("debt_total",    0))
        log.info("POST-INSERT TOTALS | user=%s income=%d expense=%d debt=%d",
                 user_id, income_total, expense_total, debt_total)

    # ── Step 5: Build financial_insights (only for log / scheme actions) ─────
    has_insights = decision.action in ("log", "scheme") and inserted_row > 0
    insights_model = FinancialInsights()

    if has_insights:
        try:
            insights_model = _build_insights(
                user_id = user_id,
                text    = text,
                income  = income_total,
                expense = expense_total,
                debt    = debt_total,
            )
        except Exception as exc:
            log.warning("Insights build failed (non-fatal): %s", exc)
            has_insights = False

    log.info("RESPONSE | user=%s action=%s has_insights=%s response=%r",
             user_id, decision.action, has_insights, decision.response[:60])

    return AnalyzeResponse(
        text                = text,
        intent              = parsed.intent,
        category            = parsed.category,
        amount              = parsed.amount,
        response            = decision.response,
        action              = decision.action,
        confidence          = parsed.confidence,
        needs_followup      = decision.needs_followup,
        has_insights        = has_insights,
        financial_insights  = insights_model,
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/api/analyze", response_model=AnalyzeResponse, tags=["core"])
async def analyze(request: AnalyzeRequest):
    """
    **Primary text input route.**

    Accepts Tamil / Tanglish / English text.
    Returns deterministic financial analysis.

    When action = 'goal_hint': user mentioned a savings goal; redirect them
    to POST /api/goal to create it with a specific target and duration.

    When has_insights = true: the financial_insights block contains prediction,
    savings calculation, goal progress and scheme suggestions.

    Example:
    ```json
    { "text": "kooli 800 vandhuchu", "user_id": "user_42" }
    ```
    """
    check_rate_limit(request.user_id)
    try:
        return _run_analysis(request.text, request.user_id)
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=_ERR_SYSTEM) from exc


@app.post("/api/followup", response_model=AnalyzeResponse, tags=["core"])
async def followup(request: FollowUpRequest):
    """
    **Multi-turn second-turn input.**

    Call when a previous /api/analyze returned action='followup'.
    If no pending session exists, treats the input as a fresh analysis.
    """
    check_rate_limit(request.user_id)

    session = get_session(request.user_id)
    if session is None:
        try:
            return _run_analysis(request.text, request.user_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=_ERR_SYSTEM) from exc

    touch_user(request.user_id)
    parsed = parse(request.text)

    # ── WAITING_FOR_AMOUNT ─────────────────────────────────────────────────
    if session.status == "WAITING_FOR_AMOUNT":
        amount = parsed.amount if parsed.amount > 0 else 0
        if amount == 0:
            log.info("FOLLOWUP | user=%s still no amount in %r", request.user_id, request.text)
            return AnalyzeResponse(
                text           = request.text,
                intent         = session.intent or "unknown",
                category       = session.category or "Other",
                amount         = 0,
                response       = "Evlo rupees? Thozhil amount sollunga. (e.g. 500)",
                action         = "followup",
                confidence     = "LOW",
                needs_followup = True,
                has_insights   = False,
            )

        effective_intent   = session.intent   or "expense"
        effective_category = session.category or "Other"
        clear_session(request.user_id)   # clear before insert (duplicate guard)
        log.info("FOLLOWUP COMPLETE | user=%s intent=%s cat=%s amt=%d",
                 request.user_id, effective_intent, effective_category, amount)
        insert_transaction(
            user_id  = request.user_id,
            amount   = amount,
            tx_type  = effective_intent,
            category = effective_category,
            tx_date  = session.tx_date,
        )
        summary       = monthly_summary(request.user_id)
        income_total  = int(summary.get("income_total",  0))
        expense_total = int(summary.get("expense_total", 0))
        debt_total    = int(summary.get("debt_total",    0))
        decision = decide(
            text=request.text, intent=effective_intent, category=effective_category,
            amount=amount, user_id=request.user_id,
            income_total=income_total, debt_total=debt_total,
            expense_total=expense_total,
        )
        # Build insights for completed followup transactions
        insights_model = FinancialInsights()
        has_insights   = decision.action in ("log", "scheme")
        if has_insights:
            try:
                insights_model = _build_insights(
                    user_id=request.user_id, text=request.text,
                    income=income_total, expense=expense_total, debt=debt_total,
                )
            except Exception:
                has_insights = False

        return AnalyzeResponse(
            text=request.text, intent=effective_intent,
            category=effective_category, amount=amount,
            response=decision.response, action=decision.action,
            confidence="HIGH", needs_followup=False,
            has_insights=has_insights, financial_insights=insights_model,
        )

    # ── WAITING_FOR_CATEGORY ───────────────────────────────────────────────
    if session.status == "WAITING_FOR_CATEGORY":
        amount           = session.amount or parsed.amount or 0
        category         = parsed.category if parsed.category != "Other" else "Other"
        effective_intent = parsed.intent   if parsed.intent != "unknown" else "expense"
        clear_session(request.user_id)
        log.info("FOLLOWUP CATEGORY | user=%s intent=%s cat=%s amt=%d",
                 request.user_id, effective_intent, category, amount)
        insert_transaction(
            user_id  = request.user_id,
            amount   = amount,
            tx_type  = effective_intent,
            category = category,
            tx_date  = session.tx_date,
        )
        summary       = monthly_summary(request.user_id)
        income_total  = int(summary.get("income_total",  0))
        expense_total = int(summary.get("expense_total", 0))
        debt_total    = int(summary.get("debt_total",    0))
        decision = decide(
            text=request.text, intent=effective_intent, category=category,
            amount=amount, user_id=request.user_id,
            income_total=income_total, debt_total=debt_total,
            expense_total=expense_total,
        )
        insights_model = FinancialInsights()
        has_insights   = decision.action in ("log", "scheme")
        if has_insights:
            try:
                insights_model = _build_insights(
                    user_id=request.user_id, text=request.text,
                    income=income_total, expense=expense_total, debt=debt_total,
                )
            except Exception:
                has_insights = False

        return AnalyzeResponse(
            text=request.text, intent=effective_intent,
            category=category, amount=amount,
            response=decision.response, action=decision.action,
            confidence="HIGH", needs_followup=False,
            has_insights=has_insights, financial_insights=insights_model,
        )

    # Fallback — unexpected session state
    clear_session(request.user_id)
    return AnalyzeResponse(
        text=request.text, intent="unknown", category="Other", amount=0,
        response="Puriyala, marubadi sollunga.",
        action="retry", confidence="NONE", needs_followup=False,
        has_insights=False,
    )


@app.post("/api/process", response_model=AnalyzeResponse, tags=["core"])
async def process(
    audio: UploadFile = File(...),
    user_id: str = Query(default="default", max_length=64),
):
    """
    **Audio input route.**

    Accepts WAV (preferred), WebM, OGG, MP3.
    Max file size: 5 MB. Requires GROQ_API_KEY for Whisper transcription.
    """
    check_rate_limit(user_id)
    data = await audio.read()
    wav_data = validate_audio(data)
    text = transcribe(wav_data)

    try:
        return _run_analysis(text, user_id)
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=_ERR_SYSTEM) from exc


# ─── NEW: POST /api/goal ──────────────────────────────────────────────────────

@app.post("/api/goal", response_model=SetGoalResponse, tags=["financial"])
async def set_goal(request: SetGoalRequest):
    """
    **Create or replace a savings goal.**

    The system stores one active goal per user (UNIQUE on user_id).
    Submitting a new goal overwrites the previous one.

    Returns:
    - daily_required: ₹ per day the user must save to hit the target
    - Tamil confirmation message

    Example:
    ```json
    { "user_id": "user_42", "target_amount": 10000, "duration_days": 100 }
    ```
    """
    try:
        upsert_goal(
            user_id       = request.user_id,
            target_amount = request.target_amount,
            duration_days = request.duration_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        log.error("Goal upsert failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=_ERR_SYSTEM) from exc

    daily_req = math.ceil(request.target_amount / request.duration_days)
    message = (
        f"Goal set aachhu! 🎯 ₹{request.target_amount} target, "
        f"{request.duration_days} naal la. "
        f"Daily ₹{daily_req} save pannunga."
    )
    log.info("GOAL SET | user=%s target=%d days=%d daily=%d",
             request.user_id, request.target_amount, request.duration_days, daily_req)

    return SetGoalResponse(
        status        = "ok",
        user_id       = request.user_id,
        target_amount = request.target_amount,
        duration_days = request.duration_days,
        daily_required = daily_req,
        message       = message,
    )


# ─── NEW: GET /api/insights ───────────────────────────────────────────────────

@app.get("/api/insights", response_model=InsightsResponse, tags=["financial"])
async def insights(user_id: str = Query(default="default", max_length=64)):
    """
    **Full financial snapshot for a user.**

    Returns savings calculation, expense prediction, goal progress,
    and enhanced scheme suggestions — all in a single call.

    No text input required. Useful for dashboard polling / PWA home screen.
    """
    try:
        sav_data  = calculate_savings(user_id)
        pred_data = predict_expense(user_id)
        gp_data   = goal_plan(user_id)

        summary = monthly_summary(user_id)
        ctx = {
            "text":       "",
            "income":     int(summary.get("income_total",  0)),
            "expense":    int(summary.get("expense_total", 0)),
            "savings":    sav_data.get("savings", 0),
            "debt_total": int(summary.get("debt_total", 0)),
        }
        schemes = enhanced_scheme(user_id, ctx)

        return InsightsResponse(
            user_id    = user_id,
            savings    = sav_data,
            prediction = pred_data,
            goal       = gp_data,
            schemes    = schemes,
        )
    except Exception as exc:
        log.error("Insights fetch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=_ERR_SYSTEM) from exc


# ─── Existing Routes (unchanged) ──────────────────────────────────────────────

@app.get("/api/history", response_model=List[TransactionRecord], tags=["data"])
async def history(
    user_id: str = Query(default="default", max_length=64),
    limit:   int = Query(default=30, ge=1, le=100),
):
    """Return the most recent transactions for a user (default last 30)."""
    rows = get_recent_transactions(user_id=user_id, limit=limit)
    return [TransactionRecord(**r) for r in rows]


@app.get("/api/summary", response_model=MonthlySummary, tags=["data"])
async def summary(user_id: str = Query(default="default", max_length=64)):
    """Current-month income / expense / debt totals for a user."""
    data = monthly_summary(user_id)
    return MonthlySummary(
        user_id           = user_id,
        income_total      = int(data.get("income_total",      0)),
        expense_total     = int(data.get("expense_total",     0)),
        debt_total        = int(data.get("debt_total",        0)),
        transaction_count = int(data.get("transaction_count", 0)),
    )


@app.get("/api/health", tags=["ops"])
async def health():
    """Health probe — DB status + active sessions + version."""
    return {
        "status":           "ok",
        "db":               "connected" if is_db_connected() else "error",
        "version":          "7.0.0",
        "mode":             "rule-based financial advisor (no LLM)",
        "pending_sessions": pending_count(),
    }


# ─── Global Exception Handler ─────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: return Tamil-friendly 500 instead of raw Python traceback."""
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": _ERR_SYSTEM},
    )
