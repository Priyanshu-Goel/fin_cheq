import os
import traceback
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.models import AnalyzeRequest, AnalyzeResponse
from app.pipeline import run_full_analysis, build_reports, OUTPUT_DIR
from app.db import get_aggregate_backtest_accuracy

app = FastAPI(title="AI Equity Research Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_allow_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    try:
        analysis, report_job = run_full_analysis(req)
        # Runs after this response is sent, not before - the user gets
        # their analysis immediately; the Excel/PDF files appear at their
        # (already-returned) URLs a few seconds later once this finishes.
        background_tasks.add_task(build_reports, *report_job)
        return analysis
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a 500 with detail
        # Exception type + the deepest frame (file:line:function) it was
        # raised from, not just str(exc) - a bare message alone has proven
        # genuinely ambiguous to debug (multiple external APIs in this app
        # can independently produce near-identical wording).
        last_frame = traceback.extract_tb(exc.__traceback__)[-1]
        origin = f"{os.path.basename(last_frame.filename)}:{last_frame.lineno} in {last_frame.name}()"
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {type(exc).__name__}: {exc} (raised from {origin})",
        )


@app.get("/downloads/{filename}")
def download(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found or expired.")
    return FileResponse(path, filename=filename)


@app.get("/backtest/aggregate-accuracy")
def aggregate_accuracy():
    """Accuracy of the CAPM signal across every company analyzed so far."""
    return get_aggregate_backtest_accuracy()
