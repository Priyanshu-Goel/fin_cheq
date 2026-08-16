"""
Fallback price history source: NSE's own website exposes an undocumented
but public JSON API (the same one nsepython/jugaad-data use) that serves
day-by-day historical data directly - no Yahoo Finance involved at all.

Why this exists: Yahoo Finance (price_data.py's primary source) has been
increasingly aggressive about blocking/rate-limiting shared cloud IPs
throughout 2025-2026 (see https://github.com/ranaroussi/yfinance/issues/2422
and related threads) - Render's free tier shares IPs across many tenants,
so YFRateLimitError can happen even with retries and browser impersonation.
NSE's endpoint is a *different* provider with a *different* IP blocklist,
so when Yahoo is down, this often still works, and vice versa.

Honest caveat: NSE is also known to rate-limit/bot-block aggressively, and
this endpoint is undocumented (could change without notice). This is a
resilience improvement, not a guarantee - if BOTH sources fail back to
back, that's a genuine "wait a bit" situation, not a bug to keep chasing.

NSE requires a valid session (cookies) obtained by first hitting the
homepage with browser-like headers, then reusing that session for the
actual data call - this mimics what a real browser does and is why a bare
`requests.get` on the API URL alone returns 401/403.
"""
import time
from datetime import datetime, timedelta
import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

BASE_URL = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
}

# Cookies from the homepage handshake are valid for a while - reusing one
# session across calls (instead of re-bootstrapping on every single price
# fetch) roughly halves our request volume against NSE, which matters
# because NSE rate-limits/blocks aggressively and re-bootstrapping on every
# call was itself making that more likely.
_SESSION_CACHE: dict[str, tuple[float, requests.Session]] = {}
_SESSION_TTL_SECONDS = 10 * 60


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
def _bootstrap_session() -> requests.Session:
    """
    Kept to 2 attempts / a short backoff, not more: this now runs racing
    against Yahoo Finance (see price_data._race_providers), so a stubborn
    retry budget here just delays surfacing an error the concurrent Yahoo
    attempt may already be past. A read-timeout on a fully-blocked NSE
    endpoint previously cost ~3 attempts * 15s + backoff (~65s+) - with an
    independent provider racing alongside, failing fast here matters more
    than being individually resilient.
    """
    cached = _SESSION_CACHE.get("session")
    if cached is not None:
        cached_at, session = cached
        if time.time() - cached_at < _SESSION_TTL_SECONDS:
            return session

    session = requests.Session()
    session.headers.update(HEADERS)
    # Hitting the homepage first is required to get valid cookies - NSE's
    # API rejects requests that arrive without them.
    session.get(BASE_URL, timeout=10)
    session.get(f"{BASE_URL}/get-quotes/equity", timeout=10)
    _SESSION_CACHE["session"] = (time.time(), session)
    return session


def _chunk_date_ranges(start: datetime, end: datetime, chunk_days: int = 364):
    """NSE's endpoint is unreliable for ranges spanning much more than a
    year in one call, so we chunk into ~1yr windows and stitch results."""
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=8), reraise=True)
def _fetch_chunk(session: requests.Session, symbol: str, start: datetime, end: datetime) -> list[dict]:
    url = f"{BASE_URL}/api/historical/cm/equity"
    params = {
        "symbol": symbol,
        "series": '["EQ"]',
        "from": start.strftime("%d-%m-%Y"),
        "to": end.strftime("%d-%m-%Y"),
    }
    resp = session.get(url, params=params, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("data", [])


def fetch_index_history_nse(index_symbol: str = "NIFTY 50", years: int = 5) -> pd.DataFrame:
    """
    NSE's own index-history endpoint - fallback for fetch_index_history in
    price_data.py when Yahoo Finance's ^NSEI series is blocked. Same session
    bootstrap approach as the equity endpoint above.
    """
    session = _bootstrap_session()
    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)

    all_rows = []
    for chunk_start, chunk_end in _chunk_date_ranges(start, end):
        url = f"{BASE_URL}/api/historical/indicesHistory"
        params = {
            "indexType": index_symbol,
            "from": chunk_start.strftime("%d-%m-%Y"),
            "to": chunk_end.strftime("%d-%m-%Y"),
        }
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", {}).get("indexCloseOnlineRecords", [])
        all_rows.extend(rows)

    if not all_rows:
        raise ValueError(f"NSE returned no index history for {index_symbol}.")

    df = pd.DataFrame(all_rows)
    df["Date"] = pd.to_datetime(df["EOD_TIMESTAMP"])
    df["Adj Close"] = pd.to_numeric(df["EOD_CLOSE_INDEX_VAL"], errors="coerce")
    df = df[["Date", "Adj Close"]].set_index("Date").sort_index()
    return df


def fetch_daily_history_nse(symbol: str, years: int = 5) -> pd.DataFrame:
    """
    Returns the same shape as price_data.fetch_daily_history:
    a DataFrame indexed by date with Open, High, Low, Close, Adj Close, Volume.
    """
    symbol = symbol.upper().replace(".NS", "").replace(".BO", "").strip()
    session = _bootstrap_session()

    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)

    all_rows = []
    for chunk_start, chunk_end in _chunk_date_ranges(start, end):
        rows = _fetch_chunk(session, symbol, chunk_start, chunk_end)
        all_rows.extend(rows)

    if not all_rows:
        raise ValueError(
            f"NSE returned no historical data for {symbol} either. Both the "
            f"Yahoo Finance and NSE-direct sources failed - this is likely a "
            f"genuine temporary block on both providers from this server's "
            f"IP. Wait 10-15 minutes and try again rather than retrying "
            f"immediately."
        )

    df = pd.DataFrame(all_rows)
    df["Date"] = pd.to_datetime(df["CH_TIMESTAMP"])
    df = df.rename(columns={
        "CH_OPENING_PRICE": "Open",
        "CH_TRADE_HIGH_PRICE": "High",
        "CH_TRADE_LOW_PRICE": "Low",
        "CH_CLOSING_PRICE": "Close",
        "CH_LAST_TRADED_PRICE": "Adj Close",
        "CH_TOT_TRADED_QTY": "Volume",
    })
    df = df[["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    df = df.set_index("Date").sort_index()
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
