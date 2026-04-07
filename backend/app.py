"""
app.py — Project Nyaya  v7.0
Voice-First Financial Assistant — Streamlit UI

Single-screen, Tamil-friendly interface for rural users.
Connects to FastAPI backend at localhost:8000.

Sections:
  A. Config & session setup
  B. API helper functions
  C. Header
  D. Input block (voice / text toggle)
  E. Response display
  F. Financial insights (conditional — has_insights only)
  G. Goal form (conditional — goal_hint only)
  H. Secondary actions (history / monthly summary)
  I. Error handling utilities
"""

import uuid
import json
import requests
import streamlit as st

# ─── A. Config ────────────────────────────────────────────────────────────────

API_BASE = "http://localhost:8000"

st.set_page_config(
    page_title="Nyaya — உங்கள் நிதி உதவியாளர்",
    page_icon="💰",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Minimal custom CSS — keeps it clean without heavy styling
st.markdown("""
<style>
    /* Softer font, reduce max width for mobile feel */
    html, body, [class*="css"] { font-family: 'Segoe UI', sans-serif; }
    .block-container { max-width: 680px; padding-top: 1.5rem; }

    /* Response card */
    .response-card {
        background: #f0f7ff;
        border-left: 5px solid #2563eb;
        border-radius: 8px;
        padding: 14px 18px;
        font-size: 1.1rem;
        line-height: 1.6;
        margin-bottom: 12px;
    }
    .alert-card {
        background: #fff8e1;
        border-left: 5px solid #f59e0b;
    }
    .hint-card {
        background: #f0fdf4;
        border-left: 5px solid #22c55e;
    }

    /* Hide hamburger / footer */
    #MainMenu { visibility: hidden; }
    footer    { visibility: hidden; }
    header    { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ─── B. Session State ─────────────────────────────────────────────────────────

def _init_session():
    """Initialise persistent session keys on first load."""
    defaults = {
        "user_id":        str(uuid.uuid4())[:12],   # short deterministic ID
        "awaiting_followup": False,                  # True when action='followup'
        "awaiting_goal":     False,                  # True when action='goal_hint'
        "last_response":     None,                   # dict from last API call
        "show_history":      False,
        "show_summary":      False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()


# ─── C. API Helpers ───────────────────────────────────────────────────────────

def _api_post(path: str, payload: dict) -> dict | None:
    """POST to backend. Returns parsed JSON or None on failure."""
    try:
        r = requests.post(f"{API_BASE}{path}", json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error("🔌 Backend connect aagala. Server running-aa check pannunga.")
        return None
    except requests.exceptions.Timeout:
        st.error("⏱️ Server too slow. Marubadi try pannunga.")
        return None
    except Exception as exc:
        st.error(f"❌ Thappu: {exc}")
        return None


def _api_post_file(path: str, file_bytes: bytes, filename: str, user_id: str) -> dict | None:
    """Multipart POST for audio. Returns parsed JSON or None on failure."""
    try:
        r = requests.post(
            f"{API_BASE}{path}",
            files={"audio": (filename, file_bytes, "audio/wav")},
            params={"user_id": user_id},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error("🔌 Backend connect aagala. Server running-aa check pannunga.")
        return None
    except Exception as exc:
        st.error(f"❌ Audio thappu: {exc}")
        return None


def _api_get(path: str, params: dict | None = None) -> dict | None:
    """GET from backend. Returns parsed JSON or None on failure."""
    try:
        r = requests.get(f"{API_BASE}{path}", params=params or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error("🔌 Backend connect aagala.")
        return None
    except Exception as exc:
        st.error(f"❌ Thappu: {exc}")
        return None


def _handle_response(resp: dict):
    """Store response in session and update flags."""
    st.session_state["last_response"]     = resp
    st.session_state["awaiting_followup"] = resp.get("action") == "followup"
    st.session_state["awaiting_goal"]     = resp.get("action") == "goal_hint"


# ─── D. Display Helpers ───────────────────────────────────────────────────────

def _action_emoji(action: str) -> str:
    return {
        "log":       "✅",
        "alert":     "⚠️",
        "scheme":    "🏛️",
        "retry":     "🔁",
        "followup":  "💬",
        "goal_hint": "🎯",
    }.get(action, "📌")


def _card_class(action: str) -> str:
    if action == "alert":
        return "alert-card"
    if action in ("goal_hint", "scheme"):
        return "hint-card"
    return ""


def _render_response(resp: dict):
    """Render the main assistant response card."""
    action  = resp.get("action", "")
    text    = resp.get("response", "...")
    emoji   = _action_emoji(action)
    css_cls = _card_class(action)

    st.markdown(
        f'<div class="response-card {css_cls}">{emoji} {text}</div>',
        unsafe_allow_html=True,
    )

    # Transcribed text (only for audio path)
    raw = resp.get("text", "")
    if raw:
        st.caption(f"🎤 Heard: *{raw}*")

    # Small metadata row
    cols = st.columns(3)
    with cols[0]:
        st.caption(f"Intent: `{resp.get('intent','—')}`")
    with cols[1]:
        st.caption(f"Category: `{resp.get('category','—')}`")
    with cols[2]:
        amt = resp.get("amount", 0)
        if amt:
            st.caption(f"Amount: **₹{amt:,}**")


def _render_insights(resp: dict):
    """
    Financial insights block — shown ONLY when has_insights=True.
    Uses st.metric and st.progress (no charts).
    """
    if not resp.get("has_insights"):
        return

    ins = resp.get("financial_insights", {})
    if not ins:
        return

    st.divider()
    st.subheader("📊 Financial Snapshot")

    # ── Savings row ──────────────────────────────────────────────────────────
    savings = ins.get("savings", 0)
    income  = ins.get("income",  0)
    expense = ins.get("expense", 0)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Income 💚", f"₹{income:,}")
    with col2:
        st.metric("Expense 🔴", f"₹{expense:,}")
    with col3:
        delta_color = "normal" if savings >= 0 else "inverse"
        st.metric("Savings 💰", f"₹{savings:,}", delta=None)

    msg = ins.get("savings_message", "")
    if msg:
        if ins.get("savings_status") in ("warning", "no_income"):
            st.warning(msg)
        elif ins.get("savings_status") == "good":
            st.success(msg)
        else:
            st.info(msg)

    # ── Prediction ───────────────────────────────────────────────────────────
    pred = ins.get("prediction", {})
    if pred.get("status") == "ok":
        st.divider()
        st.markdown("**📈 Expense Prediction**")
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("This Month (Forecast)", f"₹{pred['predicted']:,}")
        with col_b:
            st.metric("Daily Average", f"₹{pred['daily_avg']:,}")
        st.caption(pred.get("message", ""))
    elif pred.get("status") == "insufficient_data":
        st.caption(f"📈 {pred.get('message','')}")

    # ── Goal Progress ─────────────────────────────────────────────────────────
    goal = ins.get("goal", {})
    if goal.get("status") not in ("no_goal", "error", ""):
        st.divider()
        st.markdown("**🎯 Goal Progress**")
        target   = goal.get("target", 0)
        progress = goal.get("progress", 0)
        pct      = min(goal.get("percent", 0), 100) / 100  # clamp to 1.0 for st.progress

        col_x, col_y = st.columns(2)
        with col_x:
            st.metric("Target", f"₹{target:,}")
        with col_y:
            st.metric("Saved So Far", f"₹{progress:,}")

        st.progress(pct, text=f"{goal.get('percent', 0)}% complete")

        daily_req = goal.get("daily_required", 0)
        if daily_req:
            st.caption(f"💡 Daily save panna: **₹{daily_req}/day**")

        g_msg = goal.get("message", "")
        if g_msg:
            if goal.get("status") == "achieved":
                st.success(g_msg)
            elif goal.get("status") in ("low", "start"):
                st.warning(g_msg)
            else:
                st.info(g_msg)

    # ── Scheme suggestions ────────────────────────────────────────────────────
    schemes = ins.get("schemes", [])
    if schemes:
        st.divider()
        st.markdown("**🏛️ Suggested Schemes**")
        for s in schemes:
            with st.expander(f"{s['name']}"):
                st.write(s.get("description", ""))
                reason_map = {
                    "savings_positive": "✅ You have positive savings",
                    "low_income":       "ℹ️ Eligible based on income level",
                    "child_expense":    "👧 Child-related expense detected",
                    "no_income":        "ℹ️ No income recorded yet",
                }
                st.caption(reason_map.get(s.get("reason",""), ""))


def _render_goal_form():
    """
    Goal creation form — shown when action == 'goal_hint'.
    Calls POST /api/goal on submit.
    """
    st.divider()
    st.subheader("🎯 Set Your Savings Goal")
    st.info("Ungal savings target set pannunga. Neenga evvalvu save panna virumbukireenga?")

    with st.form("goal_form", clear_on_submit=True):
        target = st.number_input(
            "Target Amount (₹)",
            min_value=100,
            max_value=1_000_000,
            value=10_000,
            step=500,
        )
        days = st.number_input(
            "Duration (days)",
            min_value=7,
            max_value=3650,
            value=100,
            step=7,
        )
        submitted = st.form_submit_button("✅ Set Goal", use_container_width=True)

    if submitted:
        with st.spinner("Goal set panrom..."):
            data = _api_post("/api/goal", {
                "user_id":       st.session_state["user_id"],
                "target_amount": int(target),
                "duration_days": int(days),
            })
        if data and data.get("status") == "ok":
            st.success(data.get("message", "Goal set aachhu! ✅"))
            st.metric("Daily Savings Required", f"₹{data['daily_required']}/day")
            st.session_state["awaiting_goal"] = False
        elif data:
            st.error("Goal set aagala. Marubadi try pannunga.")


# ─── E. History & Summary Renderers ──────────────────────────────────────────

def _render_history():
    st.divider()
    st.subheader("📜 Recent Transactions")
    data = _api_get("/api/history", {"user_id": st.session_state["user_id"], "limit": 15})
    if not data:
        st.info("Engum transaction illai.")
        return
    if isinstance(data, list) and len(data) == 0:
        st.info("Engum transaction illai.")
        return

    type_emoji = {"income": "💚 Income", "expense": "🔴 Expense", "debt_repayment": "🟡 Debt"}
    for tx in data:
        label = type_emoji.get(tx.get("type",""), "📌")
        date  = tx.get("tx_date", "")[:10] if tx.get("tx_date") else "—"
        cat   = tx.get("category", "Other")
        amt   = tx.get("amount", 0)
        st.markdown(f"&nbsp;&nbsp;{label} &nbsp;|&nbsp; **₹{amt:,}** &nbsp;|&nbsp; {cat} &nbsp;|&nbsp; `{date}`")


def _render_summary():
    st.divider()
    st.subheader("📅 This Month")
    data = _api_get("/api/summary", {"user_id": st.session_state["user_id"]})
    if not data:
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Income 💚", f"₹{data.get('income_total', 0):,}")
    with col2:
        st.metric("Expense 🔴", f"₹{data.get('expense_total', 0):,}")
    with col3:
        st.metric("Debt 🟡", f"₹{data.get('debt_total', 0):,}")
    st.caption(f"{data.get('transaction_count', 0)} transactions this month")


# ─── F. Main UI ───────────────────────────────────────────────────────────────

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("## 💰 Nyaya — நிதி உதவியாளர்")
st.markdown("*Ungal selavu, savings, goal — ellam oru idam.*")
st.caption(f"User: `{st.session_state['user_id']}`")

st.divider()

# ── Follow-up banner ──────────────────────────────────────────────────────────
if st.session_state["awaiting_followup"]:
    st.warning("💬 **Assistant waiting...** Thodarnthu sollunga (amount or category).")

# ── Input section ─────────────────────────────────────────────────────────────
input_mode = st.radio(
    "Input mode",
    options=["✏️ Text", "🎤 Voice"],
    horizontal=True,
    label_visibility="collapsed",
)

user_text  = None
audio_file = None

if input_mode == "✏️ Text":
    placeholder = (
        "Marubadi sollunga..." if st.session_state["awaiting_followup"]
        else "e.g. 'kooli 800 vandhuchu' or 'maligai selavu 500'"
    )
    user_text = st.text_input(
        "Enter your message",
        placeholder=placeholder,
        label_visibility="collapsed",
    )
    send_btn = st.button("📤 Send", use_container_width=True, type="primary")

else:
    audio_file = st.file_uploader(
        "Upload voice message",
        type=["wav", "webm", "ogg", "mp3"],
        label_visibility="collapsed",
    )
    send_btn = st.button(
        "📤 Send Voice",
        use_container_width=True,
        type="primary",
        disabled=(audio_file is None),
    )

# ── Send logic ────────────────────────────────────────────────────────────────
if send_btn:
    resp = None

    # ── Voice path ────────────────────────────────────────────────────────────
    if input_mode == "🎤 Voice" and audio_file is not None:
        with st.spinner("🎙️ Processing voice..."):
            resp = _api_post_file(
                "/api/process",
                file_bytes = audio_file.read(),
                filename   = audio_file.name,
                user_id    = st.session_state["user_id"],
            )

    # ── Text path ─────────────────────────────────────────────────────────────
    elif input_mode == "✏️ Text":
        if not user_text or not user_text.strip():
            st.warning("⚠️ Enna sollukireenga? Text enter pannunga.")
        else:
            with st.spinner("🤔 Thinking..."):
                # Route to /followup if system is waiting for more info
                endpoint = "/api/followup" if st.session_state["awaiting_followup"] else "/api/analyze"
                resp = _api_post(endpoint, {
                    "text":    user_text.strip(),
                    "user_id": st.session_state["user_id"],
                })

    if resp:
        _handle_response(resp)
        st.rerun()   # Refresh to render response section cleanly

# ─── G. Response Section ──────────────────────────────────────────────────────

last = st.session_state.get("last_response")

if last:
    _render_response(last)

    # ── Goal form (goal_hint only) ────────────────────────────────────────────
    if st.session_state["awaiting_goal"]:
        _render_goal_form()
    else:
        # ── Financial insights (has_insights=True only) ───────────────────────
        _render_insights(last)

# ─── H. Secondary Actions ─────────────────────────────────────────────────────

st.divider()
col_h, col_s, col_r = st.columns([2, 2, 1])

with col_h:
    if st.button("📜 Show History", use_container_width=True):
        st.session_state["show_history"] = not st.session_state["show_history"]
        st.session_state["show_summary"] = False

with col_s:
    if st.button("📅 Monthly Summary", use_container_width=True):
        st.session_state["show_summary"] = not st.session_state["show_summary"]
        st.session_state["show_history"] = False

with col_r:
    if st.button("🔄 Reset", use_container_width=True, help="Clear session & start again"):
        for key in ["last_response", "awaiting_followup", "awaiting_goal",
                    "show_history", "show_summary"]:
            st.session_state[key] = False if isinstance(st.session_state.get(key), bool) else None
        st.rerun()

if st.session_state["show_history"]:
    _render_history()

if st.session_state["show_summary"]:
    _render_summary()

# ─── I. Full Insights On Demand ───────────────────────────────────────────────

with st.expander("📊 Full Financial Snapshot", expanded=False):
    if st.button("Load Insights", key="load_insights"):
        with st.spinner("Loading..."):
            ins_data = _api_get("/api/insights", {"user_id": st.session_state["user_id"]})
        if ins_data:
            sav = ins_data.get("savings", {})
            pred = ins_data.get("prediction", {})
            gp   = ins_data.get("goal", {})

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Income",  f"₹{sav.get('income', 0):,}")
            with col2:
                st.metric("Expense", f"₹{sav.get('expense', 0):,}")
            with col3:
                st.metric("Savings", f"₹{sav.get('savings', 0):,}")

            msg = sav.get("message", "")
            if msg:
                st.info(msg)

            if pred.get("status") == "ok":
                st.metric("Predicted Monthly Expense", f"₹{pred.get('predicted', 0):,}")
                st.caption(pred.get("message", ""))
            else:
                st.caption(f"📈 Prediction: {pred.get('message', 'Insufficient data')}")

            if gp.get("status") not in ("no_goal", "error"):
                target   = gp.get("target", 0)
                progress = gp.get("progress", 0)
                pct      = min(gp.get("percent", 0), 100) / 100
                st.progress(pct, text=f"Goal: ₹{progress:,} / ₹{target:,}")
                st.caption(gp.get("message", ""))
            else:
                st.caption("🎯 Goal set aagala — POST /api/goal use pannunga.")

            schemes = ins_data.get("schemes", [])
            if schemes:
                st.markdown("**Suggested Schemes:**")
                for s in schemes:
                    st.markdown(f"- 🏛️ **{s['name']}** — {s.get('description','')}")

# ─── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.caption("Project Nyaya v7.0 · Rule-based · Offline-friendly · Tamil Nadu 🇮🇳")
