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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import pandas as pd
import requests

BASE_URL = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
}


def _bootstrap_session() -> requests.Session:
    """
    Deliberately NOT cached/shared across calls: requests.Session wraps a
    cookiejar that is not safe for concurrent use from multiple threads,
    and now that Yahoo/NSE race concurrently and the pipeline fetches
    price/index history in parallel (see price_data.py / pipeline.py), a
    shared session here could be hit by two threads at once and corrupt
    its cookie state - the exact same class of bug fixed for the Yahoo
    session in price_data.py. A fresh handshake per call costs 2 extra
    requests but guarantees correctness.

    Deliberately no per-call retry decorator here (there used to be one):
    this already runs inside price_data._year_ladder's 4 window sizes,
    each racing against Yahoo - a real block doesn't clear up between two
    retries a few seconds apart, so an inner retry here only compounds
    with the outer ladder (observed: 4 windows * 2 attempts * 10s hit
    ~110s+ and tripped Render's own gateway timeout - a 502 from Render's
    infra, not a clean error from this app). One attempt per window,
    with the ladder+race providing the actual redundancy, keeps worst-
    case latency bounded enough to always return a real response.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    # Hitting the homepage first is required to get valid cookies - NSE's
    # API rejects requests that arrive without them.
    session.get(BASE_URL, timeout=10)
    session.get(f"{BASE_URL}/get-quotes/equity", timeout=10)
    return session


def _chunk_date_ranges(start: datetime, end: datetime, chunk_days: int = 364):
    """NSE's endpoint is unreliable for ranges spanning much more than a
    year in one call, so we chunk into ~1yr windows and stitch results."""
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def _fetch_equity_chunk(cookies, headers, symbol: str, start: datetime, end: datetime) -> list[dict]:
    """
    Takes cookies/headers rather than a live requests.Session so callers
    can run many of these concurrently (see fetch_daily_history_nse) -
    fetching a 5-year pull's ~5-6 one-year chunks one at a time in a
    for-loop was a real, uncaught serial bottleneck (each chunk can take
    several seconds, so a full sequential pull added 20-40s+ on top of
    everything else). Sharing the live Session object itself across
    threads would reintroduce the exact cookiejar-mutation bug already
    fixed once in this module; passing its already-established cookies
    to independent plain `requests.get` calls avoids that entirely.
    """
    url = f"{BASE_URL}/api/historical/cm/equity"
    params = {
        "symbol": symbol,
        "series": '["EQ"]',
        "from": start.strftime("%d-%m-%Y"),
        "to": end.strftime("%d-%m-%Y"),
    }
    resp = requests.get(url, params=params, cookies=cookies, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _fetch_index_chunk(cookies, headers, index_symbol: str, start: datetime, end: datetime) -> list[dict]:
    url = f"{BASE_URL}/api/historical/indicesHistory"
    params = {
        "indexType": index_symbol,
        "from": start.strftime("%d-%m-%Y"),
        "to": end.strftime("%d-%m-%Y"),
    }
    resp = requests.get(url, params=params, cookies=cookies, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("indexCloseOnlineRecords", [])


def fetch_index_history_nse(index_symbol: str = "NIFTY 50", years: int = 5) -> pd.DataFrame:
    """
    NSE's own index-history endpoint - fallback for fetch_index_history in
    price_data.py when Yahoo Finance's ^NSEI series is blocked. Same session
    bootstrap approach as the equity endpoint above, but chunks are fetched
    concurrently once the session's cookies are established (see
    _fetch_equity_chunk's docstring for why that's safe).
    """
    session = _bootstrap_session()
    cookies, headers = session.cookies, dict(session.headers)
    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)
    chunks = list(_chunk_date_ranges(start, end))

    with ThreadPoolExecutor(max_workers=min(8, len(chunks))) as pool:
        chunk_results = pool.map(
            lambda ce: _fetch_index_chunk(cookies, headers, index_symbol, ce[0], ce[1]), chunks
        )
        all_rows = [row for rows in chunk_results for row in rows]

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
    cookies, headers = session.cookies, dict(session.headers)

    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)
    chunks = list(_chunk_date_ranges(start, end))

    with ThreadPoolExecutor(max_workers=min(8, len(chunks))) as pool:
        chunk_results = pool.map(
            lambda ce: _fetch_equity_chunk(cookies, headers, symbol, ce[0], ce[1]), chunks
        )
        all_rows = [row for rows in chunk_results for row in rows]

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
