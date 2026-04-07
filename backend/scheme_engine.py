"""
scheme_engine.py — Hardened Scheme Matching (v6.1)

Changes from v6.0:
  - Scheme rules moved to a data config structure (SCHEME_CONFIG list of dicts)
  - Logic separated from data: SchemeRule objects are built from config at import
  - Condition lambdas remain, but their input keywords live in the config dict
  - Easier to add/edit schemes without touching engine logic
"""

from __future__ import annotations

from typing import Callable, List, NamedTuple


# ─── Config (Data Layer) ──────────────────────────────────────────────────────
# Edit this list to add / change / remove schemes.
# The engine logic below is NOT touched when scheme data changes.

SCHEME_CONFIG: List[dict] = [
    {
        "id": "KMUT",
        "name": "Kalaignar Magalir Urimai Thittam",
        "description": "₹1000/month for women in Tamil Nadu with household income < ₹2.5 lakh/yr",
        "income_threshold": 8_000,    # monthly income below this qualifies
        "trigger_keywords": [
            "ration", "ration card", "bpl", "bpl card",
            "magalir", "urimai",
        ],
    },
    {
        "id": "SSY",
        "name": "Sukanya Samriddhi Yojana (SSY)",
        "description": "High-interest Post Office savings for girl child's education/marriage",
        "income_threshold": None,      # no income threshold
        "trigger_keywords": [
            "ponnu", "daughter", "girl child", "girl",
            "school fee", "school fees", "ponnukaaga", "penn",
            "அம்மா", "பெண்",
        ],
    },
    {
        "id": "RD",
        "name": "Post Office Recurring Deposit (RD)",
        "description": "Start saving ₹100/month at the Post Office — safe, guaranteed returns",
        "income_threshold": None,
        "trigger_keywords": [],        # triggered by debt==0 logic, not keywords
        "requires_zero_debt": True,
    },
    {
        "id": "MGNREGA",
        "name": "MGNREGA 100-Day Work Scheme",
        "description": "Guaranteed 100 days of wage employment per year for rural households",
        "income_threshold": None,
        "trigger_keywords": [
            "mgnrega", "100 day", "noorunaala", "government work",
            "village panchayat work",
        ],
    },
]


# ─── Rule Model ───────────────────────────────────────────────────────────────

class SchemeRule(NamedTuple):
    id:          str
    name:        str
    description: str
    condition:   Callable[..., bool]


# ─── Build Rules from Config ──────────────────────────────────────────────────

def _build_rules(config: List[dict]) -> List[SchemeRule]:
    """Transform SCHEME_CONFIG dicts into callable SchemeRule objects."""
    rules: List[SchemeRule] = []
    for cfg in config:
        kws         = [k.lower() for k in cfg.get("trigger_keywords", [])]
        threshold   = cfg.get("income_threshold")
        needs_zero  = cfg.get("requires_zero_debt", False)

        def make_condition(
            kws=kws,
            threshold=threshold,
            needs_zero=needs_zero,
        ) -> Callable[..., bool]:
            def condition(income: int, text: str, debt: int, **_) -> bool:
                text_l = text.lower()
                kw_hit = any(k in text_l for k in kws) if kws else False
                income_hit = (
                    (0 < income < threshold) if threshold is not None else False
                )
                debt_ok = (debt == 0) if needs_zero else True
                return (kw_hit or income_hit) and debt_ok
            return condition

        rules.append(SchemeRule(
            id=cfg["id"],
            name=cfg["name"],
            description=cfg["description"],
            condition=make_condition(),
        ))
    return rules


# Build once at import time
SCHEME_RULES: List[SchemeRule] = _build_rules(SCHEME_CONFIG)


# ─── Matcher ─────────────────────────────────────────────────────────────────

def match_schemes(text: str, income: int, debt: int) -> List[dict]:
    """
    Evaluate all rules. Return list of matched scheme dicts.

    Args:
        text   : raw user utterance
        income : current-month income total from DB
        debt   : current-month debt total from DB
    """
    matched = []
    for rule in SCHEME_RULES:
        try:
            if rule.condition(income=income, text=text, debt=debt):
                matched.append({
                    "id":          rule.id,
                    "name":        rule.name,
                    "description": rule.description,
                })
        except Exception:
            # Defensive: a malformed rule must never crash a request
            pass
    return matched
