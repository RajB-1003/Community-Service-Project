"""
nlu.py — Hardened Rule-Based NLU (v6.2 — Debug & Correctness Fix)

Root-cause fixes:
  1. KEYWORD GAPS: Added maligai, saapadu, marunthu, veetu selavu, paasam,
     kirana, kade, kadai and 50+ more Tamil/Tanglish synonyms.
  2. FUZZY MATCHING: Common variant spellings handled via normalise_text()
     (double letters, trailing vowel noise, common substitutions).
  3. CATEGORY PRIORITY: Debt always first; Education before Food to prevent
     "ponnu school fee" hitting Food via "food" substring.
  4. AMOUNT EXTRACTION: Now returns the FIRST numeric match that follows a
     currency/amount signal, not the largest — prevents grabbing phone numbers.
  5. LOGGING: Structured DEBUG log line after every parse() call.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import List

log = logging.getLogger("nlu")

# ─── Text Normalisation ───────────────────────────────────────────────────────

# Maps common Tanglish spelling variants to a canonical form before matching.
_NORMALISE = [
    # double → single consonant
    (re.compile(r"ll"), "l"),
    (re.compile(r"tt"), "t"),
    (re.compile(r"nn"), "n"),
    (re.compile(r"pp"), "p"),
    (re.compile(r"rr"), "r"),
    # trailing vowel noise
    (re.compile(r"u$"), ""),       # "selavu" → "selv"  (handled by startswith match)
    (re.compile(r"aa"), "a"),      # "saapadu" → "sapadu"
    (re.compile(r"oo"), "o"),      # "kooli" → "koli"
    (re.compile(r"ee"), "e"),      # "veetu" → "vetu"
    # common substitutions
    (re.compile(r"th"), "t"),      # "vaithiyam" → "vaityam"
    (re.compile(r"dh"), "d"),
    (re.compile(r"zh"), "l"),      # Tamil ழ romanised
    (re.compile(r"ph"), "f"),
]


def normalise(text: str) -> str:
    """
    Normalise Tanglish text for fuzzy keyword matching.
    Applied AFTER exact matching fails, as a second pass.
    """
    s = text.lower()
    for pattern, repl in _NORMALISE:
        s = pattern.sub(repl, s)
    return s


# ─── Amount Extraction ────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?\s*|rupees?\s*|rp\.?\s*)(\d[\d,]*)"
    r"|(?<![\d\-])\b(\d[\d,]*)\b(?![\d])",
    re.IGNORECASE,
)
# Year filter: only suppress numbers that LOOK like years AND have no currency prefix.
# We track which group matched: group(1) = currency-prefixed (always accept),
# group(2) = bare number (suppress if year-shaped AND plausible year range).
_YEAR_RE = re.compile(r"^(19\d{2}|202[5-9]|20[3-9]\d|2[1-9]\d{2})$")


def extract_amount(text: str) -> int:
    """
    Extract rupee amount from text.

    Two-group regex:
      Group 1: number preceded by currency prefix (₹/rs/rupees) — always accept
      Group 2: bare number — accept unless it looks exactly like a calendar year

    Returns the first valid candidate (leftmost), max 1,000,000.
    """
    candidates: List[int] = []
    for m in _AMOUNT_RE.finditer(text):
        currency_prefixed = m.group(1)    # has ₹ / rs prefix
        bare              = m.group(2)    # no prefix

        if currency_prefixed is not None:
            raw = currency_prefixed.replace(",", "")
            try:
                val = int(raw)
                if 0 < val <= 1_000_000:
                    candidates.append(val)
            except ValueError:
                pass
        elif bare is not None:
            raw = bare.replace(",", "")
            # Suppress calendar years only for bare numbers
            if _YEAR_RE.match(raw):
                continue
            try:
                val = int(raw)
                if 0 < val <= 1_000_000:
                    candidates.append(val)
            except ValueError:
                pass

    return candidates[0] if candidates else 0


# ─── Time Context Parsing ─────────────────────────────────────────────────────

_TODAY_KW     = {"innaiku", "today", "ippo", "இன்னைக்கு", "இன்று", "ine", "inaikku"}
_YESTERDAY_KW = {"nettu", "nettiku", "nettru", "yesterday", "நேத்து", "nethu", "nathu"}
_LASTWEEK_KW  = {"last week", "kadan vaaram", "kilaya vaaram", "previous week"}


def extract_date(text: str) -> date:
    lower = text.lower()
    if any(kw in lower for kw in _YESTERDAY_KW):
        return date.today() - timedelta(days=1)
    if any(kw in lower for kw in _LASTWEEK_KW):
        return date.today() - timedelta(days=7)
    return date.today()


# ─── Intent Keyword Tables ────────────────────────────────────────────────────
# IMPORTANT: Debt is checked first — many debt-related words overlap with expense verbs.

_DEBT_KEYWORDS = {
    # Core loan/interest terms
    "vaddi", "kandu vaddi", "meter vaddi", "kandu vadi", "vadi",
    "kadan", "kadhan", "loan", "chit", "chit fund", "blade company",
    "debt", "borrow", "formal loan", "50 paise", "50p", "paisa vaddi",
    # Repayment verbs
    "kattinaen", "kattinen", "katti", "kattuvom",
    "adaichu", "adaichaen", "adaikiren",
    "kaduththen", "kaduththinaen",
    "repay", "repaid",
    # Tamil script
    "கடன்", "வட்டி", "கடன் கட்டினேன்",
}

_INCOME_KEYWORDS = {
    # Labour / wage
    "kooli", "koolie", "coolie", "kuli", "kulee", "koly",
    "daily wage", "daily wages", "thinai kooli",
    "mgnrega", "100 day", "100day", "noorunaala",
    # Occupation
    "tailoring", "thozhil", "silai", "silayadi", "tailar",
    "salary", "salaeram", "maatha kadai", "monthly salary",
    "job", "work payment",
    # Receipt verbs
    "income", "earned", "earn",    # Receipt verbs — income-specific (receiving money, NOT buying)
    "vandhuchu", "vanduchu", "wantuchu", "wanthuchu",
    "kedachuchu", "kedasuchu", "kittuchu", "kittachu",
    "vaanthuchu", "vandhu serthaen",
    "received",
    # Business / sale
    "business", "sell", "sold", "vitrean", "vitren", "vikkirom",
    "commission",
    # Government transfers
    "pm kisan", "subsidy", "pension", "amma pension",
    # Tamil script
    "விற்றேன்", "கிடைச்சது", "சம்பாரிச்சேன்", "வேலை", "வந்தது",
}

_EXPENSE_KEYWORDS = {
    # ── FOOD & GROCERIES ──────────────────────────────────────────────────
    "groceries", "grocery", "ration", "ration shop",
    "milk", "paal", "paaal",
    "rice", "arisi",
    "dal", "paruppu", "moong", "urad",
    "vegetables", "kaai kari", "keerai", "kaaikari",
    "food", "saapadu", "sapadu", "saappaadu",
    "maligai", "maligai kadai", "malligai",        # ← KEY FIX: grocery shop
    "kirana", "kirana kadai", "kirna",              # ← common store name
    "kadai", "kade",                                # ← "shop" in Tanglish
    "provisions",
    "oil", "ennai", "gingelly", "groundnut oil",
    "maavu", "flour", "atta", "bread",
    "egg", "mutta", "chicken", "mutton", "fish", "meen",
    "sugar", "sakkarai", "tea", "coffee", "kappi",
    "snacks", "biscuit", "biscuits",
    # ── EDUCATION ─────────────────────────────────────────────────────────
    "school fee", "school fees", "fees",
    "book", "books", "notebook", "notebooks",
    "ponnu school", "daughter school", "penn school",
    "tuition", "coaching",
    # ── HEALTH ────────────────────────────────────────────────────────────
    "hospital", "medicine", "medicines",
    "marunthu", "marundu", "marunthaa",             # ← KEY FIX: Tamil for medicine
    "doctor", "health", "vaithiyam", "vaithiyan",
    "clinic", "tablet", "tablets", "injection", "neetle",
    "operation", "surgery", "nursing home",
    # ── HOUSEHOLD UTILITIES ───────────────────────────────────────────────
    "rent", "house rent", "veetu vaadakam", "vaadakam",
    "electricity", "current bill", "current",
    "water bill", "water",
    "gas", "cylinder", "gas cylinder",
    "phone bill", "mobile bill", "recharge",
    "internet", "wifi",
    "household", "veetu selavu",                    # ← KEY FIX: household expense
    # ── TRANSPORT ─────────────────────────────────────────────────────────
    "auto", "autoriksha", "auto charge",
    "bus", "bus fare", "bus ticket",
    "train", "train ticket",
    "petrol", "diesel", "fuel",
    "ticket",
    # ── SPEND VERBS (check after category hits) ───────────────────────────
    "selavu", "selvu", "sela", "sella",
    "selavaachu", "selavitten", "selavitaen",
    "selavu panniten", "selavu panni", "selavu aayiduchu",
    "selavachu", "selavachitten", "selavaitaen",
    "spent", "spend", "spentu",
    "paid", "pay", "payment",
    "kattinaen",                                    # also in debt — debt checked first
    "கட்டினேன்", "வாங்கினேன்",
    "bought", "vaanginaen", "vaangi",
    "purchase", "purchased",
    # ── MONEY-GONE PHRASES ────────────────────────────────────────────────
    "kaasu poiduchu", "kaasu pochu", "kaasu pona",
    "panam poiduchu", "panam pochu", "panam pona",
    "kuduthen", "koduththen", "kuduthaen", "koduthaen",
    "kodutten",
}

_GOAL_KEYWORDS = {
    # Explicit goal / target words
    "goal", "target", "lakshya", "saving goal", "savings goal",
    "save panna venum", "save panna poringa", "save pannanum",
    "save panna", "semikirein",
    # Tamil script
    "சேமிக்க வேண்டும்", "இலக்கு",
}

_INTENT_RULES: list[tuple[set[str], str]] = [
    (_DEBT_KEYWORDS,    "debt_repayment"),
    (_INCOME_KEYWORDS,  "income"),
    (_EXPENSE_KEYWORDS, "expense"),
    (_GOAL_KEYWORDS,    "set_goal"),      # ← lowest priority; safe mode only
]


def classify_intent(text: str) -> tuple[str, int]:
    """
    Return (intent_label, keyword_hit_count).
    Evaluated in priority order: Debt → Income → Expense → Goal.

    IMPORTANT: Only exact substring matching is used here.
    The fuzzy/normalised pass is intentionally excluded from intent classification
    because short stems (e.g. normalise('sell') = 'sel') cause false positives
    against Tamil words like 'selavu'. Categories use normalised matching safely
    because those keywords are longer and more specific.

    set_goal is SAFE MODE: detecting it never writes to DB; only triggers
    a goal_hint response asking the user to redirect to POST /api/goal.
    """
    lower = text.lower()
    for keywords, intent in _INTENT_RULES:
        hits = sum(1 for kw in keywords if kw in lower)
        if hits > 0:
            return intent, hits
    return "unknown", 0


# ─── Category Keyword Table ───────────────────────────────────────────────────
# Priority: Debt → Education → Health → Food → Household → Travel → SHG → Income
# (Education before Food to avoid "ponnu" in a food context)

_CATEGORY_RULES: list[tuple[set[str], str]] = [
    # Debt
    ({
        "vaddi", "kandu vaddi", "meter vaddi", "kadan", "kadhan",
        "loan", "chit", "chit fund", "debt", "vadi", "kandu vadi",
        "blade company", "கடன்", "வட்டி",
    }, "Debt"),
    # Education
    ({
        "school fee", "school fees", "education", "fees",
        "book", "books", "notebook",
        "ponnu school", "daughter school", "penn school",
        "tuition", "coaching",
    }, "Education"),
    # Health
    ({
        "hospital", "medicine", "medicines",
        "marunthu", "marundu", "marunthaa",
        "doctor", "health", "vaithiyam", "vaithiyan",
        "clinic", "tablet", "tablets", "injection",
        "operation", "surgery", "nursing home",
    }, "Health"),
    # Food (incl. grocery shops)
    ({
        "ration", "groceries", "grocery",
        "milk", "paal", "rice", "arisi",
        "dal", "paruppu", "vegetables", "kaai kari", "keerai",
        "food", "saapadu", "sapadu", "saappaadu",
        "maligai", "maligai kadai", "malligai",
        "kirana", "kirana kadai",
        "provisions",
        "oil", "ennai", "maavu", "flour", "bread",
        "egg", "mutta", "chicken", "mutton", "fish", "meen",
        "sugar", "sakkarai", "tea", "coffee", "kappi",
        "snacks", "biscuit",
    }, "Food"),
    # Household
    ({
        "rent", "house rent", "veetu vaadakam", "vaadakam",
        "electricity", "current bill", "current",
        "water bill", "gas", "cylinder", "gas cylinder",
        "phone bill", "mobile bill", "recharge",
        "internet", "wifi",
        "household", "veetu selavu",
    }, "Household"),
    # Travel
    ({
        "auto", "bus", "train", "petrol", "diesel", "fuel",
        "transport", "travel", "ticket",
        "auto charge", "bus fare", "train ticket",
    }, "Travel"),
    # SHG
    ({
        "shg", "mahalir", "kudumbam", "self help",
        "group savings", "sangam", "kudumbam sangam",
    }, "SHG"),
    # Income
    ({
        "kooli", "koolie", "coolie", "kuli",
        "mgnrega", "100 day",
        "tailoring", "thozhil", "salary", "business",
        "commission", "pension",
    }, "Income"),
]


def classify_category(text: str, intent: str) -> str:
    """
    Map text → standard category.

    Pass 1: exact keyword match (priority-ordered rules).
    Pass 2: normalised match on same rules.
    Fallback: infer from intent.
    """
    lower = text.lower()
    norm  = normalise(lower)

    # Pass 1: exact
    for keywords, category in _CATEGORY_RULES:
        for kw in keywords:
            if kw in lower:
                return category

    # Pass 2: normalised (catches "marunthaa" → Health, "saappaadu" → Food, etc.)
    for keywords, category in _CATEGORY_RULES:
        for kw in keywords:
            if normalise(kw) in norm:
                return category

    # Fallback
    if intent == "income":
        return "Income"
    if intent == "debt_repayment":
        return "Debt"
    return "Other"


# ─── Confidence Scoring ───────────────────────────────────────────────────────

def _compute_confidence(intent: str, match_count: int, amount: int) -> str:
    """
    HIGH   : intent known + amount found
    MEDIUM : intent known OR amount found (not both), OR multi-keyword
    LOW    : single weak keyword only, no amount
    NONE   : nothing recognised
    """
    if intent == "unknown" and amount == 0:
        return "NONE"
    if intent != "unknown" and amount > 0:
        return "HIGH" if match_count >= 2 else "MEDIUM"
    # Only intent OR only amount
    return "MEDIUM" if (match_count >= 2 or amount > 0) else "LOW"


# ─── ParsedInput ─────────────────────────────────────────────────────────────

class ParsedInput:
    __slots__ = (
        "text", "intent", "category", "amount",
        "confidence", "missing_amount", "missing_category", "tx_date",
    )

    def __init__(
        self,
        text: str,
        intent: str,
        category: str,
        amount: int,
        confidence: str,
        missing_amount: bool,
        missing_category: bool,
        tx_date: date,
    ) -> None:
        self.text             = text
        self.intent           = intent
        self.category         = category
        self.amount           = amount
        self.confidence       = confidence
        self.missing_amount   = missing_amount
        self.missing_category = missing_category
        self.tx_date          = tx_date

    def __repr__(self) -> str:
        return (
            f"<ParsedInput intent={self.intent} category={self.category} "
            f"amount={self.amount} confidence={self.confidence}>"
        )


# ─── Top-Level Parse ──────────────────────────────────────────────────────────

def parse(text: str) -> ParsedInput:
    """
    Full NLU parse pipeline with structured debug logging.

    Returns ParsedInput with all fields populated.
    """
    text_clean = text.strip()

    intent, hits = classify_intent(text_clean)
    category     = classify_category(text_clean, intent)
    amount       = extract_amount(text_clean)
    confidence   = _compute_confidence(intent, hits, amount)
    tx_date      = extract_date(text_clean)

    missing_amount   = (intent != "unknown") and (amount == 0)
    missing_category = (category == "Other")

    result = ParsedInput(
        text             = text_clean,
        intent           = intent,
        category         = category,
        amount           = amount,
        confidence       = confidence,
        missing_amount   = missing_amount,
        missing_category = missing_category,
        tx_date          = tx_date,
    )

    log.debug(
        "NLU | input=%r intent=%s cat=%s amt=%d conf=%s miss_amt=%s tx_date=%s",
        text_clean, intent, category, amount, confidence, missing_amount, tx_date,
    )

    return result
