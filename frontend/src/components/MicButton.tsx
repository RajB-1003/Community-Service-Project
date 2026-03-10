"use client";

import { useRef, useState, useEffect } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

type Status = "idle" | "recording" | "loading";

interface Scheme {
  scheme_name: string;
  reason: string;
}

interface Transaction {
  transaction_type: string;
  amount: number;
  category: string;
}

interface Insights {
  total_income_logged: number;
  total_expense_logged: number;
  debt_risk_flag: boolean;
  alert_message: string | null;
  suggested_schemes: Scheme[];
}

interface FinancialResult {
  transactions: Transaction[];
  insights: Insights;
  transcribed_text?: string;
}

interface MicButtonProps {
  onResult: (data: FinancialResult) => void;
  onError: (msg: string) => void;
}

// ── Language options ───────────────────────────────────────────────────────────

const LANGUAGES = [
  { code: "en-IN",  label: "English (India)" },
  { code: "hi-IN",  label: "हिन्दी (Hindi)" },
  { code: "ta-IN",  label: "தமிழ் (Tamil)" },
  { code: "te-IN",  label: "తెలుగు (Telugu)" },
  { code: "kn-IN",  label: "ಕನ್ನಡ (Kannada)" },
  { code: "ml-IN",  label: "മലയാളം (Malayalam)" },
  { code: "mr-IN",  label: "मराठी (Marathi)" },
  { code: "bn-IN",  label: "বাংলা (Bengali)" },
  { code: "gu-IN",  label: "ગુજરાતી (Gujarati)" },
  { code: "pa-IN",  label: "ਪੰਜਾਬੀ (Punjabi)" },
  { code: "ur-IN",  label: "اردو (Urdu)" },
];

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

// ── TypeScript type declarations for Web Speech API ─────────────────────────
// (not included in standard TS dom lib for Next.js)

interface SpeechRecognitionEvent extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEvent extends Event {
  readonly error: string;
  readonly message: string;
}

interface SpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult:  ((event: SpeechRecognitionEvent) => void) | null;
  onerror:   ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend:     (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

declare global {
  interface Window {
    webkitSpeechRecognition: new () => SpeechRecognition;
    SpeechRecognition:       new () => SpeechRecognition;
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function MicButton({ onResult, onError }: MicButtonProps) {
  const [status, setStatus]           = useState<Status>("idle");
  const [lang, setLang]               = useState("en-IN");
  const [transcript, setTranscript]   = useState("");   // live display
  const [interimText, setInterimText] = useState("");   // grey interim words
  const [supported, setSupported]     = useState(true);

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const finalRef       = useRef("");   // accumulates final text across utterances
  const stoppedRef     = useRef(false); // true = user manually stopped, false = Chrome auto-stopped

  // ── Check browser support ──────────────────────────────────────────────────

  useEffect(() => {
    const SpeechAPI =
      typeof window !== "undefined"
        ? window.SpeechRecognition ?? window.webkitSpeechRecognition
        : null;
    if (!SpeechAPI) setSupported(false);
  }, []);

  // ── Submit transcript to /api/analyze ──────────────────────────────────────

  const submitTranscript = async (text: string) => {
    if (!text.trim()) {
      onError("No speech detected. Please try again.");
      setStatus("idle");
      return;
    }
    setStatus("loading");
    try {
      const res = await fetch(`${BACKEND_URL}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail ?? `Server error ${res.status}`);
      }
      const data = await res.json();
      onResult({ ...data, transcribed_text: text.trim() });
    } catch (e) {
      onError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setStatus("idle");
    }
  };

  // ── Start recognition ──────────────────────────────────────────────────────

  const startRecording = () => {
    const SpeechAPI =
      window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!SpeechAPI) {
      onError("Speech recognition is not supported in this browser. Please use Chrome or Edge.");
      return;
    }

    const recognition = new SpeechAPI();
    recognitionRef.current = recognition;
    finalRef.current = "";
    stoppedRef.current = false;
    setTranscript("");
    setInterimText("");

    recognition.lang = lang;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let interimAccum = "";
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const result = event.results[i];
        if (result.isFinal) {
          finalRef.current += " " + result[0].transcript;
        } else {
          interimAccum += result[0].transcript;
        }
      }
      setTranscript(finalRef.current.trim());
      setInterimText(interimAccum);
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      if (event.error === "no-speech") return; // ignore — user just paused
      if (event.error === "aborted") return;   // we stopped it manually
      onError(`Speech error: ${event.error}`);
      setStatus("idle");
    };

    recognition.onend = () => {
      // Chrome auto-stops after silence — restart if user hasn't clicked stop
      if (!stoppedRef.current) {
        try { recognition.start(); } catch { /* already starting */ }
      }
    };

    recognition.start();
    setStatus("recording");
  };

  // ── Stop and submit ────────────────────────────────────────────────────────

  const stopAndSubmit = () => {
    stoppedRef.current = true;          // signal onend NOT to restart
    recognitionRef.current?.stop();
    const captured = finalRef.current.trim();
    setInterimText("");
    submitTranscript(captured);
  };

  // ── Click handler ──────────────────────────────────────────────────────────

  const handleClick = () => {
    if (status === "idle")      startRecording();
    else if (status === "recording") stopAndSubmit();
  };

  // ── Unsupported browser fallback ───────────────────────────────────────────

  if (!supported) {
    return (
      <div className="text-center text-sm text-amber-600 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
        ⚠ Speech recognition requires <strong>Chrome or Edge</strong>.
        <br />Use the <strong>Type Issue</strong> tab instead.
      </div>
    );
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col items-center gap-4 w-full">

      {/* Language selector */}
      <div className="w-full">
        <label className="block text-xs font-semibold text-slate-500 mb-1">
          🌐 Speak in:
        </label>
        <select
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          disabled={status === "recording" || status === "loading"}
          className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-400 disabled:opacity-50"
        >
          {LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>{l.label}</option>
          ))}
        </select>
      </div>

      {/* Mic button */}
      <button
        onClick={handleClick}
        disabled={status === "loading"}
        aria-label={
          status === "idle"      ? "Start Recording" :
          status === "recording" ? "Stop and Analyse" : "Processing…"
        }
        className={`
          relative w-28 h-28 rounded-full flex items-center justify-center
          shadow-2xl transition-all duration-300 focus:outline-none focus:ring-4 focus:ring-offset-2
          ${status === "idle"
            ? "bg-gradient-to-br from-blue-600 to-indigo-700 hover:from-blue-500 hover:to-indigo-600 focus:ring-blue-400 hover:scale-105"
            : status === "recording"
            ? "bg-gradient-to-br from-red-500 to-rose-700 focus:ring-red-400 scale-110"
            : "bg-gradient-to-br from-slate-400 to-slate-500 cursor-not-allowed"
          }
        `}
      >
        {/* Pulse rings when recording */}
        {status === "recording" && (
          <>
            <span className="absolute inset-0 rounded-full bg-red-400 opacity-40 animate-ping" />
            <span className="absolute inset-2 rounded-full bg-red-400 opacity-20 animate-ping [animation-delay:0.3s]" />
          </>
        )}

        {/* Mic icon */}
        {status === "idle" && (
          <svg xmlns="http://www.w3.org/2000/svg" className="w-12 h-12 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 0 1 3 3v8a3 3 0 0 1-6 0V4a3 3 0 0 1 3-3z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4m-4 0h8" />
          </svg>
        )}

        {/* Stop icon */}
        {status === "recording" && (
          <svg xmlns="http://www.w3.org/2000/svg" className="w-12 h-12 text-white" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        )}

        {/* Spinner */}
        {status === "loading" && (
          <svg className="w-12 h-12 text-white animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        )}
      </button>

      {/* Status label */}
      <p className="text-sm font-medium tracking-wide text-slate-500 uppercase">
        {status === "idle"      && "Tap to speak"}
        {status === "recording" && "Listening… tap to stop & analyse"}
        {status === "loading"   && "Analysing your issue…"}
      </p>

      {/* Live transcript box */}
      {(transcript || interimText) && (
        <div className="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm text-slate-700 min-h-[60px]">
          <span>{transcript}</span>
          {interimText && (
            <span className="text-slate-400 italic"> {interimText}</span>
          )}
        </div>
      )}

      {/* Submit button (if user stopped but wants to manually trigger) */}
      {status === "idle" && transcript && (
        <button
          onClick={() => submitTranscript(transcript)}
          className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2.5 rounded-xl text-sm transition-colors"
        >
          Analyse This →
        </button>
      )}
    </div>
  );
}