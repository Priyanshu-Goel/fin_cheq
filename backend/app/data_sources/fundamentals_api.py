"""
Client for the indianapi.in "Indian Stock Market API" (also listed on RapidAPI).
Cheap paid tiers, free tier for prototyping.

Uses the /historical_stats endpoint (stats=ratios|yoy_results|balancesheet|
cashflow) rather than /stock: it returns each table pre-shaped as
{row_label: {period: value}}, byte-for-byte the same shape
screener_scraper.parse_financial_table produces - so pipeline.py can treat
both fundamentals sources identically with no separate normalization step.

If INDIAN_API_KEY isn't set in .env, callers should fall back to
screener_scraper.py instead (see pipeline.py for the fallback logic).
"""
from concurrent.futures import ThreadPoolExecutor
import requests
from app.config import settings


class FundamentalsAPIError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.indian_api_key)


def fetch_historical_stat(company_name: str, stat: str) -> dict:
    """
    stat: one of quarter_results, yoy_results, balancesheet, cashflow,
    ratios, shareholding_pattern_quarterly, shareholding_pattern_yearly.
    Returns {row_label: {period: value}}.
    """
    if not is_configured():
        raise FundamentalsAPIError("INDIAN_API_KEY not configured")

    url = f"{settings.indian_api_base_url}/historical_stats"
    headers = {"X-Api-Key": settings.indian_api_key}
    params = {"stock_name": company_name, "stats": stat}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise FundamentalsAPIError(str(exc)) from exc


def fetch_company_fundamentals(company_name: str) -> dict:
    """
    Fetches the 4 historical-stats tables this app's analysis actually
    needs, concurrently. Returns the same shape
    screener_scraper.fetch_all_fundamentals does:
    {top_ratios, profit_loss, balance_sheet, cash_flow, ratios_5yr}.
    Raises FundamentalsAPIError on failure so the caller can fall back to
    the scraper.
    """
    stat_by_key = {
        "ratios_5yr": "ratios",
        "profit_loss": "yoy_results",
        "balance_sheet": "balancesheet",
        "cash_flow": "cashflow",
    }
    pool = ThreadPoolExecutor(max_workers=len(stat_by_key))
    try:
        futures = {
            key: pool.submit(fetch_historical_stat, company_name, stat)
            for key, stat in stat_by_key.items()
        }
        result = {key: future.result() for key, future in futures.items()}
    finally:
        pool.shutdown(wait=False)

    result["top_ratios"] = {}  # not populated via historical_stats; unused downstream
    return result
