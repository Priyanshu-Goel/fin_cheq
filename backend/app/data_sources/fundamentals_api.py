"""
Client for the indianapi.in "Indian Stock Market API" (also listed on RapidAPI).
Cheap paid tiers, free tier for prototyping. Returns Screener-style fundamentals
(ratios, financials, key metrics) as clean JSON - no scraping needed.

If INDIAN_API_KEY isn't set in .env, callers should fall back to
screener_scraper.py instead (see pipeline.py for the fallback logic).
"""
import requests
from app.config import settings


class FundamentalsAPIError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.indian_api_key)


def fetch_company_fundamentals(company_name: str) -> dict:
    """
    Returns a dict with company profile, current price, technical data,
    and multi-year financials - shape mirrors what Screener.in shows.
    Raises FundamentalsAPIError on failure so the caller can fall back
    to the scraper.
    """
    if not is_configured():
        raise FundamentalsAPIError("INDIAN_API_KEY not configured")

    url = f"{settings.indian_api_base_url}/stock"
    headers = {"X-Api-Key": settings.indian_api_key}
    params = {"name": company_name}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise FundamentalsAPIError(str(exc)) from exc


def normalize_fundamentals(raw: dict) -> dict:
    """
    Maps the API's field names into the flat structure the rest of the
    pipeline (analysis/ratios.py etc.) expects. Adjust the key paths here
    if the API response shape drifts - this is the single place to fix it.
    """
    financials = raw.get("financials", {})
    return {
        "company_name": raw.get("companyName"),
        "industry": raw.get("industry"),
        "current_price": raw.get("currentPrice", {}),
        "year_high": raw.get("yearHigh"),
        "year_low": raw.get("yearLow"),
        # Expect financials to be a dict keyed by year -> metrics
        "yearly_financials": financials,
    }
