"use client";

import { useState } from "react";
import MicButton from "@/components/MicButton";

import { Scale } from "lucide-react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

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

export default function Home() {
  const [result, setResult] = useState<FinancialResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [inputMode, setInputMode] = useState<"text" | "mic">("text");
  const [textInput, setTextInput] = useState("");
  const [textLoading, setTextLoading] = useState(false);

  const handleResult = (data: FinancialResult) => {
    setErrorMsg(null);
    setResult(data);
  };

  const handleError = (msg: string) => setErrorMsg(msg);

  const handleReset = () => {
    setResult(null);
    setErrorMsg(null);
    setTextInput("");
  };

  const handleTextSubmit = async () => {
    if (!textInput.trim()) return;
    setTextLoading(true);
    setErrorMsg(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: textInput.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail ?? `Server error ${res.status}`);
      }
      const data = await res.json();
      handleResult({ ...data, transcribed_text: textInput.trim() });
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setTextLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-gradient-to-b from-slate-50 via-blue-50 to-indigo-100 py-8 px-4">
      <div className="max-w-md mx-auto space-y-6">

        <header className="text-center space-y-2">
          
          <h1 className="text-3xl font-extrabold tracking-tight text-slate-900">
            Rural Finance <span className="text-emerald-600">Advisor</span>
          </h1>
          <p className="text-sm text-slate-500 max-w-xs mx-auto leading-relaxed">
            Log financial transactions via text or voice, track debt, and find government schemes.
          </p>
        </header>

        {!result && (
          <section className="bg-white rounded-3xl shadow-xl border border-slate-100 py-8 px-6 space-y-5">

            <div className="flex rounded-xl overflow-hidden border border-slate-200 text-sm font-semibold">
              <button
                onClick={() => setInputMode("text")}
                className={`flex-1 py-2.5 transition-colors ${
                  inputMode === "text"
                    ? "bg-indigo-600 text-white"
                    : "bg-white text-slate-500 hover:bg-slate-50"
                }`}
              >
                Type Issue
              </button>
              <button
                onClick={() => setInputMode("mic")}
                className={`flex-1 py-2.5 transition-colors ${
                  inputMode === "mic"
                    ? "bg-indigo-600 text-white"
                    : "bg-white text-slate-500 hover:bg-slate-50"
                }`}
              >
                Speak Issue
              </button>
            </div>

            {inputMode === "text" && (
              <div className="space-y-3">
                <label className="block text-sm font-semibold text-slate-700">
                  Log Your Daily Expense
                </label>
                <textarea
                  id="legal-issue-input"
                  rows={5}
                  className="w-full border border-slate-200 rounded-xl px-4 py-3 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
                  placeholder={
                    "Examples:\n" +
                    "* Iniku velaiku Poi 500 rupa Sambarichen \n" +
                    "* Maligai Saman Selavu 200 rupa\n" +
                    "* Ponnu School Selavuku 500 rupa Kaasu kuduthen"
                  }
                  value={textInput}
                  onChange={(e) => setTextInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleTextSubmit();
                  }}
                />
                <button
                  id="analyze-text-btn"
                  onClick={handleTextSubmit}
                  disabled={textLoading || !textInput.trim()}
                  className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold py-3 rounded-xl text-sm transition-colors flex items-center justify-center gap-2"
                >
                  {textLoading ? (
                    <>
                      <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Analysing…
                    </>
                  ) : (
                    "Analyse My Situation →"
                  )}
                </button>
                {/* <p className="text-xs text-slate-400 text-center">Ctrl + Enter to submit</p> */}
              </div>
            )}

            {inputMode === "mic" && (
              <div className="flex flex-col items-center space-y-4">
                <MicButton onResult={handleResult} onError={handleError} />
                <p className="text-xs text-slate-400 text-center">
                  Speak in Tanglish, Tamil, or English.
                  <br />we will translate automatically.
                </p>
              </div>
            )}

            {errorMsg && (
              <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3">
                <p className="text-sm text-red-600 font-medium">{errorMsg}</p>
              </div>
            )}

            <div className="flex flex-wrap gap-2 justify-center pt-1">
              {["Income Parsing", "Debt Risk Alerts", "Scheme Suggestions"].map((tag) => (
                <span key={tag} className="text-xs bg-slate-100 text-slate-500 rounded-full px-3 py-1 font-medium">
                  {tag}
                </span>
              ))}
            </div>
          </section>
        )}

        {result && (
          <>
            <div className="bg-white rounded-3xl shadow-xl border border-slate-100 py-6 px-6 space-y-6">
              
              {result.transcribed_text && (
                <div className="space-y-2">
                  <h3 className="text-sm font-semibold text-slate-500">Statement</h3>
                  <p className="text-sm bg-slate-50 p-3 rounded-lg border border-slate-100 text-slate-700 italic">
                    &quot;{result.transcribed_text}&quot;
                  </p>
                </div>
              )}

              {result.insights.debt_risk_flag && result.insights.alert_message && (
                <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded-r-lg">
                  <h3 className="text-red-800 font-bold mb-1">Debt Risk Alert</h3>
                  <p className="text-red-700 text-sm">{result.insights.alert_message}</p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <div className="bg-emerald-50 border border-emerald-100 p-4 rounded-xl text-center">
                  <p className="text-xs font-semibold text-emerald-600 uppercase tracking-wide">Total Income</p>
                  <p className="text-2xl font-bold text-emerald-700">₹{result.insights.total_income_logged}</p>
                </div>
                <div className="bg-rose-50 border border-rose-100 p-4 rounded-xl text-center">
                  <p className="text-xs font-semibold text-rose-600 uppercase tracking-wide">Total Expense</p>
                  <p className="text-2xl font-bold text-rose-700">₹{result.insights.total_expense_logged}</p>
                </div>
              </div>

              {result.transactions.length > 0 && (
                <div className="space-y-3">
                  <h3 className="text-sm font-bold text-slate-800">Detected Transactions</h3>
                  <ul className="space-y-2">
                    {result.transactions.map((t, idx) => (
                      <li key={idx} className="flex items-center justify-between text-sm bg-slate-50 border border-slate-100 p-3 rounded-xl">
                        <span className="font-medium text-slate-700 capitalize">{t.category}</span>
                        <div className="flex items-center gap-3">
                          <span className={`text-xs px-2 py-1 rounded-full font-semibold ${
                            t.transaction_type === 'income' ? 'bg-emerald-100 text-emerald-700' :
                            t.transaction_type === 'expense' ? 'bg-rose-100 text-rose-700' :
                            'bg-violet-100 text-violet-700'
                          }`}>
                            {t.transaction_type.replace('_', ' ')}
                          </span>
                          <span className="font-bold">₹{t.amount}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {result.insights.suggested_schemes.length > 0 && (
                <div className="space-y-3 pt-2">
                  <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                    🌟 Suggested Schemes
                  </h3>
                  <div className="space-y-3">
                    {result.insights.suggested_schemes.map((scheme, idx) => (
                      <div key={idx} className="bg-gradient-to-r from-indigo-50 to-blue-50 border border-indigo-100 p-4 rounded-xl">
                        <h4 className="font-bold text-indigo-900 text-sm mb-1">{scheme.scheme_name}</h4>
                        <p className="text-xs text-indigo-700">{scheme.reason}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

            </div>

            <button
              onClick={handleReset}
              className="w-full py-3 rounded-2xl text-sm font-semibold text-slate-500 border border-slate-200 bg-white hover:bg-slate-50 transition-colors"
            >
              ← Log Another Entry
            </button>
          </>
        )}

        {/* <footer className="text-center text-xs text-slate-400 pb-4 space-y-1">
          <p>Financial Logic Engine powered by Llama 3.3</p>
        </footer> */}
      </div>
    </main>
  );
}