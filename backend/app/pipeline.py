"""
Orchestrates a full run for one company:
  1. Resolve symbol -> concurrently fetch 5yr price history (yfinance/NSE) +
     Nifty50 index history + fundamentals (indianapi.in/Screener) + source
     documents (annual report/transcripts) - these are all independent I/O
     calls, so they run in parallel rather than one after another
  2. Compute ratios, CAPM, risk, red flags, backtest
  3. Chunk + embed source documents, store in Supabase
  4. Retrieve relevant chunks, generate the RAG research note via Claude
  5. Build Excel + PDF reports, save to /tmp (or wherever OUTPUT_DIR points)

This is the single function main.py's /analyze route calls.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.data_sources import price_data
from app.data_sources.fundamentals_api import (
    fetch_company_fundamentals, normalize_fundamentals,
    is_configured as fundamentals_api_configured, FundamentalsAPIError,
)
from app.data_sources.screener_scraper import fetch_all_fundamentals
from app.data_sources.reports_fetcher import fetch_source_documents

from app.analysis.ratios import compute_ratio_trends
from app.analysis.capm import compute_capm
from app.analysis.risk import assess_risk
from app.analysis.red_flags import detect_red_flags
from app.analysis.backtest import run_backtest_adaptive

from app.rag.chunker import chunk_documents
from app.rag.vector_store import store_chunks, retrieve_relevant_chunks
from app.rag.note_generator import generate_research_note

from app.reports.excel_report import build_excel_report
from app.reports.pdf_report import build_pdf_report

from app.models import AnalyzeRequest, AnalyzeResponse, PricePoint
from app.config import settings
from app.db import save_backtest_result

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp/equity_reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _resolve_symbol(req: AnalyzeRequest) -> str:
    if req.nse_symbol:
        return req.nse_symbol.upper()
    # Best-effort: many company names ARE close to their NSE symbol conventions,
    # but for reliability we strongly recommend passing nse_symbol explicitly
    # from the frontend's company search/autocomplete (see frontend/lib/api.ts).
    guess = req.company_name.upper().replace(" ", "").replace(".", "")[:10]
    return guess


def _get_fundamentals(nse_symbol: str, company_name: str) -> dict:
    """Try the cheap paid API first; fall back to the free scraper."""
    if fundamentals_api_configured():
        try:
            raw = fetch_company_fundamentals(company_name)
            return normalize_fundamentals(raw)
        except FundamentalsAPIError:
            pass  # fall through to scraper
    scraped = fetch_all_fundamentals(nse_symbol)
    return {
        "top_ratios": scraped["top_ratios"],
        "profit_loss": scraped["profit_loss"],
        "balance_sheet": scraped["balance_sheet"],
        "cash_flow": scraped["cash_flow"],
        "ratios_5yr": scraped["ratios_5yr"],
    }


def _build_quant_summary_text(ratios, capm, risk, red_flags, backtest) -> str:
    lines = ["Ratio trends:"]
    for r in ratios:
        lines.append(f"  - {r.metric}: {r.trend} ({r.values_by_year})")
    lines.append(f"\nCAPM: beta={capm.beta}, cost_of_equity={capm.cost_of_equity:.2%}, "
                  f"R^2={capm.r_squared}")
    lines.append(f"Risk: annualized_vol={risk.annualized_volatility:.2%}, "
                 f"sharpe={risk.sharpe_ratio}, max_drawdown={risk.max_drawdown:.2%}, "
                 f"risk_grade={risk.risk_grade}")
    lines.append("Red flags: " + "; ".join(f"[{f.severity}] {f.title}" for f in red_flags))
    lines.append(f"Backtest: predicted={backtest.predicted_expected_return:.2%}, "
                 f"actual={backtest.actual_realized_return:.2%}, hit={backtest.directional_hit}")
    return "\n".join(lines)


def _build_chart_price_history(price_df) -> list[PricePoint]:
    """Weekly-downsampled series for the frontend chart - the full daily
    5-year series (~1250 rows) would be wasteful to send over the wire for
    a line chart and is already available in the Excel download."""
    weekly = price_df["Adj Close"].resample("W").last().dropna()
    return [PricePoint(date=str(idx.date()), close=round(float(val), 2)) for idx, val in weekly.items()]


def run_full_analysis(req: AnalyzeRequest) -> AnalyzeResponse:
    nse_symbol = _resolve_symbol(req)

    # 1. Price history, fundamentals, and source documents are all
    # independent I/O calls - fetching them one after another was pure
    # wasted wall-clock (each can take tens of seconds against
    # slow/rate-limited providers). Run them concurrently instead.
    #
    # Deliberately not a `with ThreadPoolExecutor(...) as pool:` block:
    # its __exit__ calls shutdown(wait=True), so if e.g. price_future
    # raises first, exiting the `with` still blocks until every other
    # future finishes too - tacking their full duration onto a request
    # that's already failed. shutdown(wait=False) in `finally` lets the
    # error surface as soon as the first future raises.
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        price_future = pool.submit(price_data.fetch_daily_history, nse_symbol, req.exchange, 5)
        index_future = pool.submit(price_data.fetch_index_history, years=5)
        fundamentals_future = pool.submit(_get_fundamentals, nse_symbol, req.company_name)
        documents_future = pool.submit(fetch_source_documents, nse_symbol)

        price_df = price_future.result()
        index_df = index_future.result()
        fundamentals = fundamentals_future.result()
        documents = documents_future.result()
    finally:
        pool.shutdown(wait=False)

    stock_returns = price_data.daily_returns(price_df)
    market_returns = price_data.daily_returns(index_df)

    # 2. Quantitative analysis
    ratio_trends = compute_ratio_trends(
        fundamentals.get("ratios_5yr", {}), fundamentals.get("profit_loss", {})
    )
    capm_result = compute_capm(stock_returns, market_returns, window_years=5)
    risk_result = assess_risk(price_df, stock_returns)
    red_flags = detect_red_flags(
        ratio_trends,
        fundamentals.get("balance_sheet", {}),
        fundamentals.get("cash_flow", {}),
        fundamentals.get("profit_loss", {}),
    )
    backtest_result = run_backtest_adaptive(
        price_df, index_df, years_ago=settings.backtest_years_ago
    )
    save_backtest_result(nse_symbol, backtest_result)

    # 3. RAG: chunk + embed the source documents fetched above, store
    chunks = chunk_documents(documents)
    store_chunks(nse_symbol, chunks)
    retrieved = retrieve_relevant_chunks(
        nse_symbol, query=f"{req.company_name} financial performance and outlook", top_k=6
    )

    # 4. Generate the qualitative note
    quant_summary_text = _build_quant_summary_text(
        ratio_trends, capm_result, risk_result, red_flags, backtest_result
    )
    note_text = generate_research_note(req.company_name, retrieved, quant_summary_text)

    analysis = AnalyzeResponse(
        company_name=req.company_name,
        symbol=nse_symbol,
        exchange=req.exchange,
        summary=note_text,
        ratios=ratio_trends,
        capm=capm_result,
        risk=risk_result,
        red_flags=red_flags,
        backtest=backtest_result,
        price_history=_build_chart_price_history(price_df),
        excel_url="",  # filled in below
        pdf_url="",
    )

    # 5. Build downloadable reports
    run_id = uuid.uuid4().hex[:8]
    excel_path = os.path.join(OUTPUT_DIR, f"{nse_symbol}_{run_id}.xlsx")
    pdf_path = os.path.join(OUTPUT_DIR, f"{nse_symbol}_{run_id}.pdf")
    build_excel_report(analysis, price_df, excel_path)
    build_pdf_report(analysis, note_text, pdf_path)

    analysis.excel_url = f"/downloads/{os.path.basename(excel_path)}"
    analysis.pdf_url = f"/downloads/{os.path.basename(pdf_path)}"
    return analysis
