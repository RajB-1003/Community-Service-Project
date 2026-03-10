
"""
Project Nyaya — FastAPI Backend v5.0
Voice-first legal triage for marginalized communities.

Context retrieval pipeline (priority order):
  1. Live web fetch from official Indian government portals (httpx, async, 6s timeout)
  2. ChromaDB semantic search (ONNXMiniLM-L6-v2) — fallback if web fetch returns < 300 chars
  3. Both sources are fused when web fetch succeeds, so the LLM gets the richest possible context
"""

import asyncio
import os
import uuid
import json
import tempfile
from pathlib import Path
from typing import List

import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from groq import Groq
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

                                                                             
           
                                                                             

load_dotenv()
DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DEMO_MODE and not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set. Add it to .env or set DEMO_MODE=true.")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Rural Finance Advisor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

                                                                             
                                      
                                                                             
                                                                       
                                                                             
                                                                             

_chroma_client = chromadb.Client()
_embed_fn = ONNXMiniLM_L6_V2()

SCHEME_CHUNKS = [
    {
        "id": "scheme_km_urimai",
        "topic": "Income & Ration",
        "section": "Kalaignar Magalir Urimai Thittam",
        "text": (
            "Kalaignar Magalir Urimai Thittam: "
            "A Tamil Nadu state scheme providing ₹1000/month to eligible women. "
            "Eligibility Criteria: "
            "1. Family annual income must be below ₹2.5 lakhs (approx. ₹20,833/month). "
            "So if a user's monthly income is less than ₹8000/month, they easily qualify. "
            "2. Users who mention possessing a 'Ration Card' or 'BPL card' are also generally eligible. "
            "3. Intended to support women head of families to improve livelihood."
        ),
    },
    {
        "id": "scheme_ssy",
        "topic": "Education & Girl Child",
        "section": "Sukanya Samriddhi Yojana (SSY)",
        "text": (
            "Sukanya Samriddhi Yojana (SSY): "
            "A Government of India backed saving scheme targeted at the parents of girl children. "
            "Eligibility Criteria: "
            "1. The user must have a daughter / girl child. "
            "2. Whenever a user mentions a 'daughter', 'ponnu', 'girl child', or paying for 'school fees' for a girl. "
            "Benefits: "
            "High interest rate scheme at the Post Office. It builds a fund for the girl's education and marriage."
        ),
    },
    {
        "id": "scheme_po_rd",
        "topic": "Savings",
        "section": "Post Office Recurring Deposit (RD)",
        "text": (
            "Post Office Recurring Deposit (RD): "
            "A safe investment for rural and low-income individuals to build a saving habit. "
            "Eligibility Criteria: "
            "1. Any individual with zero or manageable debt. "
            "2. If a user logs Income but has ZERO Debt (no loans, no vaddi). "
            "Benefits: "
            "You can start an RD with as little as ₹100 per month at the nearest Post Office."
        ),
    },
    {
        "id": "risk_informal_debt",
        "topic": "Debt & Risk",
        "section": "Informal Debt Risk",
        "text": (
            "Informal Debt and 'Kandu Vaddi': "
            "Rural economies often suffer from predatory lending (kandu vaddi, meter vaddi, informal loans). "
            "Risk Triggers: "
            "1. If the user mentions paying 'vaddi', 'kandu vaddi', 'chit fund', 'loan', 'kadan', 'meter vaddi', etc. "
            "Action Required: "
            "Set the 'debt_risk_flag' to true and issue a critical warning in simple Tanglish/English about the dangers of informal high-interest debt."
        ),
    },
]

                                                                             
                                                        
                                                                             


def _build_vector_store() -> chromadb.Collection:
    """Embed all scheme chunks and store in an in-memory ChromaDB collection."""
    try:
        _chroma_client.delete_collection("nyaya_financial")
    except Exception:
        pass

    collection = _chroma_client.create_collection(
        name="nyaya_financial",
        embedding_function=_embed_fn,
        metadata={"hnsw:space": "cosine"},
    )
    collection.add(
        ids=[c["id"] for c in SCHEME_CHUNKS],
        documents=[c["text"] for c in SCHEME_CHUNKS],
        metadatas=[{"topic": c["topic"], "section": c["section"]} for c in SCHEME_CHUNKS],
    )
    return collection


                                                                         
_collection: chromadb.Collection = _build_vector_store()


def semantic_retrieve(query: str, n_results: int = 4) -> str:
    """
    Embed the user's query and return the top-n most semantically relevant
    legal chunk texts, joined together as a single context string.
    """
    results = _collection.query(
        query_texts=[query],
        n_results=n_results,
        include=["documents", "metadatas"],
    )
    docs = results["documents"][0]                             
    metas = results["metadatas"][0]                               
    context_parts = []
    for doc, meta in zip(docs, metas):
        context_parts.append(f"[{meta['topic']} — {meta['section']}]\n{doc}")
    return "\n\n".join(context_parts)


                                                                             
                  
                                                                             

AUDIO_MIME_MAP = {
    ".webm": "audio/webm",
    ".mp3":  "audio/mpeg",
    ".mp4":  "audio/mp4",
    ".wav":  "audio/wav",
    ".ogg":  "audio/ogg",
    ".flac": "audio/flac",
    ".m4a":  "audio/mp4",
}


def get_mime_type(suffix: str) -> str:
    return AUDIO_MIME_MAP.get(suffix.lower(), "audio/webm")


                                                                             
                                           
                                                                             

DEMO_RESPONSES = {
    "Financial": {
        "transactions": [
            {
                "transaction_type": "income",
                "amount": 600,
                "category": "tailoring"
            },
            {
                "transaction_type": "debt_repayment",
                "amount": 300,
                "category": "vaddi"
            },
            {
                "transaction_type": "expense",
                "amount": 0,
                "category": "school fee"
            }
        ],
        "insights": {
            "total_income_logged": 600,
            "total_expense_logged": 0,
            "debt_risk_flag": True,
            "alert_message": "Vaddi katradhu ungaluku romba risk. Meter vaddi kaaranga kitta irundhu vilagi irunga, idhu unga valkaiya paadhikum.",
            "suggested_schemes": [
                {
                    "scheme_name": "Kalaignar Magalir Urimai Thittam",
                    "reason": "Since your logged income is likely below ₹8000/month, you might qualify for ₹1000 monthly."
                },
                {
                    "scheme_name": "Sukanya Samriddhi Yojana (SSY)",
                    "reason": "You mentioned paying for your daughter's school fees. Open an SSY account at the Post Office to save for her future."
                }
            ]
        }
    }
}

def _demo_process(intent_key: str = "Financial") -> dict:
    return DEMO_RESPONSES["Financial"]


                                                                             
                  
                                                                             


class AnalyzeRequest(BaseModel):
    text: str

class Transaction(BaseModel):
    transaction_type: str = Field(description="One of: 'income', 'expense', or 'debt_repayment'")
    amount: int
    category: str = Field(description="Standardized english string for the category")

class Scheme(BaseModel):
    scheme_name: str
    reason: str

class Insights(BaseModel):
    total_income_logged: int
    total_expense_logged: int
    debt_risk_flag: bool
    alert_message: str | None
    suggested_schemes: List[Scheme]

class IntentResult(BaseModel):
    transactions: List[Transaction]
    insights: Insights


                                                                             
                     
                                                                             


SYSTEM_PROMPT = """You are the backend financial logic engine for a rural micro-economy application in Tamil Nadu (Ayyampalayam). 
Your objective is to analyze raw, typed text input (informal Tamil, Tanglish, or English) from a user, extract financial transactions, and output deterministic financial alerts and scheme matching.

RULES OF ENGAGEMENT:
1. NO CHAT: You do not converse. You output ONLY strictly valid JSON. No markdown wrappers outside the JSON structure.
2. TYPO RESILIENCE: Users will type in Tanglish with heavy spelling variations (e.g., "selavu", "selvu", "vadi", "vaddi", "kooli", "koolee"). Parse the intent regardless of spelling.
3. LOCAL ECONOMY MAPPING:
   - "vaddi", "kandu vaddi", "chit fund", "loan", "kadan" -> type: "debt_repayment"
   - "coolie", "100-day work", "mgnrega", "tailoring", "business" -> type: "income"
   - "groceries", "school fee", "ration", "hospital" -> type: "expense"
4. DETERMINISTIC LOGIC ENGINE:
   - If Income < ₹8000/month or user mentions "ration card": Suggest "Kalaignar Magalir Urimai Thittam" (₹1000/month).
   - If user mentions daughter/girl child/school fees: Suggest "Sukanya Samriddhi Yojana (SSY)" at the Post Office.
   - If user logs Income but ZERO Debt: Suggest "Post Office Recurring Deposit (RD) - start with ₹100".
   - If user logs "vaddi", "kandu vaddi" or informal debt: Set "debt_risk_flag" to true and issue a critical warning in simple Tanglish/English in alert_message.

JSON SCHEMA REQUIREMENT:
{
  "transactions": [
    {
      "transaction_type": "income" | "expense" | "debt_repayment",
      "amount": <integer>,
      "category": "<standardized english string>"
    }
  ],
  "insights": {
    "total_income_logged": <integer>,
    "total_expense_logged": <integer>,
    "debt_risk_flag": <boolean>,
    "alert_message": "<String in simple Tanglish/English if risk is true, else null>",
    "suggested_schemes": [
      {
        "scheme_name": "<Name of Scheme>",
        "reason": "<Why they qualify>"
      }
    ]
  }
}
"""


async def _call_groq_analyze(text: str) -> IntentResult:
    """
    RAG-fallback context pipeline:
      1. Run ChromaDB semantic search to get top-3 relevant financial/scheme chunks.
      2. Call Groq Llama 3.3-70b with the context.
    """
    rag_context = semantic_retrieve(text, n_results=3)

    fused_context = (
        "=== CONTEXT FROM FINANCIAL SCHEME KNOWLEDGE BASE ===\n"
        f"{rag_context}"
    )

    prompt = (
        f'User\'s statement (translated to English if necessary): "{text}"\n\n'
        f"Context to use for scheme matching:\n"
        f"{fused_context}\n\n"
        f"Produce a specific, legally precise JSON response for this user's exact financial situation."
    )

    response = await asyncio.to_thread(
        groq_client.chat.completions.create,
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw = response.choices[0].message.content
    data = json.loads(raw)

    return IntentResult(**data)


                                                                             
             
                                                                             
@app.post("/api/analyze", response_model=IntentResult)
async def analyze(request: AnalyzeRequest):
    try:
        return await _call_groq_analyze(request.text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"JSON parse error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq analysis error: {exc}") from exc


@app.post("/api/process")
async def process(audio: UploadFile = File(...)):
    """
    Full pipeline: audio → Whisper → semantic retrieval → Llama analysis.
    Set DEMO_MODE=true in .env to bypass all API calls.
    """
    if DEMO_MODE:
        demo = _demo_process("Financial")
        return JSONResponse({**demo, "transcribed_text": "[DEMO] I earned 600 from tailoring, and paid 300 vaddi."})

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    tmp_path = Path(tempfile.mktemp(suffix=suffix))
    try:
        tmp_path.write_bytes(await audio.read())
        with open(tmp_path, "rb") as f:
            whisper_resp = groq_client.audio.translations.create(
                file=(tmp_path.name, f.read()),
                model="whisper-large-v3",
                response_format="text",
            )
        text = str(whisper_resp).strip()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq transcription error: {exc}") from exc
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    try:
        result = await _call_groq_analyze(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"JSON parse error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq analysis error: {exc}") from exc

    return JSONResponse({
        **result.model_dump(),
        "transcribed_text": text
    })


                                                                             
                                                             
                                                                             


@app.get("/api/debug/retrieve")
async def debug_retrieve(q: str, n: int = 4):
    """
    GET /api/debug/retrieve?q=my+husband+beats+me&n=4
    Shows which legal chunks were semantically matched for a query.
    Useful for testing and tuning retrieval quality.
    """
    results = _collection.query(
        query_texts=[q],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )
    return {
        "query": q,
        "retrieved": [
            {
                "rank": i + 1,
                "topic": meta["topic"],
                "section": meta["section"],
                "distance": round(dist, 4),
                "preview": doc[:200] + "...",
            }
            for i, (doc, meta, dist) in enumerate(zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ))
        ],
    }


@app.get("/api/debug/sources")
async def debug_sources(intent: str = None):
    """
    GET /api/debug/sources              → all intents
    GET /api/debug/sources?intent=RTI   → specific intent
    Shows all configured government portal URLs and which sources are tried per intent.
    """
    if intent:
        sources = GOVERNMENT_SOURCES.get(intent, [])
        return {
            "intent": intent,
            "configured_sources": [
                {"url": s["url"], "label": s["label"]} for s in sources
            ],
        }
    return {
        "all_sources": {
            topic: [{"url": s["url"], "label": s["label"]} for s in srcs]
            for topic, srcs in GOVERNMENT_SOURCES.items()
        }
    }



