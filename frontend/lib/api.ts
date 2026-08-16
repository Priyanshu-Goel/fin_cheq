const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface RatioTrend {
  metric: string;
  values_by_year: Record<string, number>;
  trend: "improving" | "deteriorating" | "stable";
}

export interface CapmResult {
  beta: number;
  risk_free_rate: number;
  market_return_assumption: number;
  cost_of_equity: number;
  r_squared: number;
  regression_window_years: number;
}

export interface RiskAssessment {
  annualized_volatility: number;
  sharpe_ratio: number;
  max_drawdown: number;
  value_at_risk_95: number;
  risk_grade: string;
}

export interface RedFlag {
  severity: string;
  title: string;
  detail: string;
}

export interface BacktestResult {
  as_of_date: string;
  predicted_expected_return: number;
  actual_realized_return: number;
  error: number;
  directional_hit: boolean;
  mae: number;
  rmse: number;
}

export interface PricePoint {
  date: string;
  close: number;
}

export interface AnalyzeResponse {
  company_name: string;
  symbol: string;
  exchange: string;
  summary: string;
  ratios: RatioTrend[];
  capm: CapmResult;
  risk: RiskAssessment;
  red_flags: RedFlag[];
  backtest: BacktestResult;
  price_history: PricePoint[];
  excel_url: string;
  pdf_url: string;
}

export async function pingHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

export async function analyzeCompany(
  companyName: string,
  nseSymbol?: string,
  exchange: string = "NSE"
): Promise<AnalyzeResponse> {
  const controller = new AbortController();
  // Generous timeout: a full run does 5yr price history (yfinance/NSE) +
  // fundamentals scraping + document embeddings + an LLM call + PDF/Excel
  // generation, which can genuinely take over a minute even on a warm
  // backend - on top of a cold Render instance waking up (~30-50s).
  const timeoutId = setTimeout(() => controller.abort(), 150_000);

  let res: Response;
  try {
    res = await fetch(`${API_URL}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company_name: companyName,
        nse_symbol: nseSymbol || null,
        exchange,
      }),
      signal: controller.signal,
    });
  } catch (e: any) {
    if (e.name === "AbortError") {
      throw new Error(
        "The request is taking longer than expected (over 2.5 minutes) and was cancelled. This can happen on a cold backend instance, or when the underlying market data providers are temporarily rate-limiting requests. Please try again in a moment, or wait 10-15 minutes if it keeps happening."
      );
    }
    throw new Error(
      "Couldn't reach the backend. Check your connection, or the backend may be temporarily unavailable."
    );
  } finally {
    clearTimeout(timeoutId);
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Analysis failed. Please try again.");
  }
  return res.json();
}

export function downloadUrl(path: string): string {
  return `${API_URL}${path}`;
}
