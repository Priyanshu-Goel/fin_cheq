from supabase import create_client, Client
from app.config import settings
from app.models import BacktestResult


def get_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


def save_backtest_result(company_ticker: str, result: BacktestResult) -> None:
    """Persists each run's backtest so accuracy can be tracked in aggregate
    across every company you've analyzed over time (see README section 3)."""
    client = get_client()
    client.table("backtest_results").insert({
        "company_ticker": company_ticker,
        "run_date": result.as_of_date,
        "predicted_return": result.predicted_expected_return,
        "actual_return": result.actual_realized_return,
        "hit": result.directional_hit,
    }).execute()


def get_aggregate_backtest_accuracy() -> dict:
    """Returns overall hit-rate and average error across all stored backtests."""
    client = get_client()
    response = client.table("backtest_results").select("*").execute()
    rows = response.data or []
    if not rows:
        return {"num_runs": 0, "hit_rate": None, "avg_abs_error": None}

    hits = sum(1 for r in rows if r["hit"])
    errors = [abs(r["predicted_return"] - r["actual_return"]) for r in rows]
    return {
        "num_runs": len(rows),
        "hit_rate": round(hits / len(rows), 3),
        "avg_abs_error": round(sum(errors) / len(errors), 4),
    }
