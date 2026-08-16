import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.models import AnalyzeRequest, AnalyzeResponse
from app.pipeline import run_full_analysis, OUTPUT_DIR
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
def analyze(req: AnalyzeRequest):
    try:
        return run_full_analysis(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a 500 with detail
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")


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
