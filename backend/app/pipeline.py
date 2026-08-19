"""
Orchestrates a full run for one company:
  1. Resolve symbol -> concurrently fetch 5yr price history (yfinance/NSE) +
     Nifty50 index history + fundamentals (indianapi.in/Screener) + source
     documents (annual report/transcripts) - these are all independent I/O
     calls, so they run in parallel rather than one after another. Price
     history failing is fatal (there's no analysis without it); fundamentals
     and source documents degrade gracefully to "unavailable" instead of
     failing the whole request, since both have been observed hard-blocked
     from some hosts' IPs entirely (see data_sources/*.py docstrings) -
     price/CAPM/risk/backtest still have real data to work with either way.
  2. Compute ratios, CAPM, risk, red flags, backtest
  3. Chunk + embed source documents, store in Supabase
  4. Retrieve relevant chunks, generate the RAG research note via Claude
  5. Return the analysis immediately, with the Excel/PDF report paths
     already assigned but the files not yet written - main.py schedules
     build_reports() as a background task so the user gets everything
     else without waiting on openpyxl/reportlab on top of an already-long
     request. /downloads/{filename} 404s until the file actually exists.

run_full_analysis() is the single function main.py's /analyze route calls.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.data_sources import price_data
from app.data_sources.fundamentals_api import (
    fetch_company_fundamentals,
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

from app.models import AnalyzeRequest, AnalyzeResponse, PricePoint, RedFlag
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


_EMPTY_FUNDAMENTALS = {
    "top_ratios": {}, "profit_loss": {}, "balance_sheet": {}, "cash_flow": {}, "ratios_5yr": {},
}


def _get_fundamentals(nse_symbol: str, company_name: str) -> tuple[dict, bool]:
    """
    Try the cheap paid API first; fall back to the free scraper - both
    return the identical {top_ratios, profit_loss, balance_sheet,
    cash_flow, ratios_5yr} shape, so no separate normalization step is
    needed either way. If both fail, return an empty shape instead of
    failing the whole analysis: ratios/red-flags downstream already treat
    a missing row as "no data", so this degrades to quant-only output
    (price history, CAPM, risk, backtest) rather than an all-or-nothing
    failure. Returns (fundamentals_dict, succeeded).
    """
    if fundamentals_api_configured():
        try:
            return fetch_company_fundamentals(company_name), True
        except FundamentalsAPIError:
            pass  # fall through to scraper
    try:
        scraped = fetch_all_fundamentals(nse_symbol)
        return {
            "top_ratios": scraped["top_ratios"],
            "profit_loss": scraped["profit_loss"],
            "balance_sheet": scraped["balance_sheet"],
            "cash_flow": scraped["cash_flow"],
            "ratios_5yr": scraped["ratios_5yr"],
        }, True
    except Exception:
        return _EMPTY_FUNDAMENTALS, False


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


def run_full_analysis(req: AnalyzeRequest) -> tuple[AnalyzeResponse, tuple]:
    """
    Returns (analysis, report_job). `report_job` is whatever build_reports()
    needs - main.py passes it straight through to a BackgroundTask so the
    Excel/PDF files get written after the response is already on its way
    to the client.
    """
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
        fundamentals, fundamentals_available = fundamentals_future.result()
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
    if not fundamentals_available:
        # Otherwise detect_red_flags's own "nothing crossed the thresholds"
        # fallback message reads as a clean bill of health, when really no
        # data was checked at all - say so plainly instead.
        red_flags.insert(0, RedFlag(
            severity="Medium",
            title="Fundamentals data unavailable",
            detail="Both the fundamentals API and the Screener.in scraper "
                   "failed for this company, so the ratio trends and "
                   "red-flag checks above reflect an absence of data, not "
                   "a clean bill of health. Price history, CAPM, risk, "
                   "and backtest results are unaffected.",
        ))
    backtest_result = run_backtest_adaptive(
        price_df, index_df, years_ago=settings.backtest_years_ago
    )
    try:
        # Persisting for the aggregate accuracy endpoint is a nice-to-have,
        # not core to returning this company's analysis - a Supabase outage
        # shouldn't fail an otherwise-successful request.
        save_backtest_result(nse_symbol, backtest_result)
    except Exception:
        pass

    # 3. RAG: chunk + embed the source documents fetched above, store
    chunks = chunk_documents(documents)
    retrieved: list[str] = []
    if chunks:
        # Skip the embedding/retrieval round-trip entirely when there's
        # nothing to retrieve (documents fetch is currently blocked - see
        # reports_fetcher.py) - no point calling Hugging Face + Supabase
        # just to get nothing back. Also wrapped defensively: if HF_API_TOKEN
        # isn't configured or either service has a bad day, note_generator.py
        # already handles an empty retrieved list by grounding only in the
        # quantitative outputs, so this shouldn't fail the whole analysis.
        try:
            store_chunks(nse_symbol, chunks)
            retrieved = retrieve_relevant_chunks(
                nse_symbol, query=f"{req.company_name} financial performance and outlook", top_k=6
            )
        except Exception:
            pass

    # 4. Generate the qualitative note
    quant_summary_text = _build_quant_summary_text(
        ratio_trends, capm_result, risk_result, red_flags, backtest_result
    )
    try:
        note_text = generate_research_note(req.company_name, retrieved, quant_summary_text)
    except Exception:
        # The LLM call is the last thing that could fail before we have a
        # complete, useful response - a CometAPI outage/misconfiguration
        # shouldn't discard real price/CAPM/risk/backtest results the user
        # already has. Fall back to the raw quant summary as the note.
        note_text = (
            "AI-generated commentary is temporarily unavailable. Below is "
            "the raw quantitative summary this note would normally be "
            f"written from:\n\n{quant_summary_text}"
        )

    # 5. The download paths are predictable, so the response can carry
    # their final URLs immediately - the files themselves are written by
    # build_reports() below, which main.py runs as a background task
    # *after* sending this response. openpyxl/reportlab add real seconds
    # on top of an already-long request; there's no reason to make the
    # user wait on them when they already have everything else. The
    # /downloads/{filename} route already 404s until a file exists, so
    # the frontend can just poll it - no new endpoint needed.
    run_id = uuid.uuid4().hex[:8]
    excel_path = os.path.join(OUTPUT_DIR, f"{nse_symbol}_{run_id}.xlsx")
    pdf_path = os.path.join(OUTPUT_DIR, f"{nse_symbol}_{run_id}.pdf")

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
        excel_url=f"/downloads/{os.path.basename(excel_path)}",
        pdf_url=f"/downloads/{os.path.basename(pdf_path)}",
    )

    report_job = (analysis, price_df, fundamentals, note_text, excel_path, pdf_path)
    return analysis, report_job


def build_reports(
    analysis: AnalyzeResponse, price_df, fundamentals: dict, note_text: str, excel_path: str, pdf_path: str
) -> None:
    """Writes the Excel workbook and PDF note to disk. Run as a FastAPI
    BackgroundTask (see main.py) so it happens after the response - the
    files simply don't exist at the predicted paths until this finishes,
    and /downloads/{filename} 404s until then."""
    build_excel_report(analysis, price_df, fundamentals, excel_path)
    build_pdf_report(analysis, note_text, pdf_path)
