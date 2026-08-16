"use client";

import { useEffect, useState } from "react";

const STAGES = [
  { at: 0, label: "Waking up the backend and resolving the ticker…" },
  { at: 6, label: "Pulling 5-year daily price history…" },
  { at: 14, label: "Fetching fundamentals and financial statements…" },
  { at: 24, label: "Computing ratios, CAPM beta, and risk metrics…" },
  { at: 34, label: "Retrieving relevant excerpts from source documents…" },
  { at: 42, label: "Drafting the research note…" },
  { at: 58, label: "Building the Excel workbook and PDF note…" },
  { at: 75, label: "Almost there — finalizing the report…" },
];

export default function ProgressIndicator() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  const current = [...STAGES].reverse().find((s) => elapsed >= s.at) || STAGES[0];

  return (
    <div className="status-box">
      <div className="progress-bar-track">
        <div
          className="progress-bar-fill"
          style={{ width: `${Math.min(96, (elapsed / 90) * 100)}%` }}
        />
      </div>
      <div style={{ marginTop: 10 }}>{current.label}</div>
      <div style={{ marginTop: 4, fontSize: 11, opacity: 0.7 }}>
        {elapsed}s elapsed — a cold backend start plus a full 5-year pull can
        take up to a minute or two.
      </div>
    </div>
  );
}
