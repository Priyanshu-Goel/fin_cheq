"use client";

import { useState } from "react";

interface Props {
  onSubmit: (companyName: string, nseSymbol: string, exchange: string) => void;
  loading: boolean;
}

const EXAMPLES = [
  { name: "Infosys", symbol: "INFY" },
  { name: "Tata Motors", symbol: "TATAMOTORS" },
  { name: "HDFC Bank", symbol: "HDFCBANK" },
  { name: "Reliance Industries", symbol: "RELIANCE" },
];

export default function CompanySearch({ onSubmit, loading }: Props) {
  const [companyName, setCompanyName] = useState("");
  const [nseSymbol, setNseSymbol] = useState("");
  const [exchange, setExchange] = useState("NSE");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!companyName.trim()) return;
    onSubmit(companyName.trim(), nseSymbol.trim(), exchange);
  }

  function useExample(name: string, symbol: string) {
    if (loading) return;
    setCompanyName(name);
    setNseSymbol(symbol);
    onSubmit(name, symbol, "NSE");
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="search-row">
        <input
          type="text"
          placeholder="Company name, e.g. Infosys, Tata Motors, HDFC Bank"
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          disabled={loading}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Analyzing…" : "Generate research note"}
        </button>
      </div>
      <p className="hint">
        Optional: enter the exact NSE/BSE ticker below for a reliable match
        (e.g. INFY, TATAMOTORS) — company-name lookup is best-effort.
      </p>
      <div className="search-row" style={{ marginBottom: 24 }}>
        <input
          type="text"
          placeholder="Ticker symbol (optional)"
          value={nseSymbol}
          onChange={(e) => setNseSymbol(e.target.value)}
          disabled={loading}
          style={{ flex: "0 0 220px" }}
        />
        <select
          value={exchange}
          onChange={(e) => setExchange(e.target.value)}
          disabled={loading}
          style={{ padding: "0 14px", border: "2px solid var(--ink)", borderRadius: 2 }}
        >
          <option value="NSE">NSE</option>
          <option value="BSE">BSE</option>
        </select>
      </div>

      <div className="examples-row">
        <span className="examples-label">Try:</span>
        {EXAMPLES.map((ex) => (
          <button
            key={ex.symbol}
            type="button"
            className="example-chip"
            disabled={loading}
            onClick={() => useExample(ex.name, ex.symbol)}
          >
            {ex.name}
          </button>
        ))}
      </div>
    </form>
  );
}
