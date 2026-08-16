"""
Price history via yfinance and NSE's own direct historical data endpoint
(nse_direct.py), raced concurrently so whichever provider isn't currently
blocked wins - trying one to exhaustion before even attempting the other
means a genuine outage on either side costs the full sum of both retry
budgets (observed: ~90s) instead of the max of the two.
NSE tickers use a `.NS` suffix, BSE tickers use `.BO`.

IMPORTANT — cloud hosting note:
Yahoo Finance aggressively blocks/rate-limits requests coming from shared
datacenter IP ranges (Render, Railway, AWS, etc.) - an ongoing, widely
reported issue throughout 2025-2026 (see
https://github.com/ranaroussi/yfinance/issues/2422). This module:
  1. Impersonates a real browser via curl_cffi (bypasses outright blocking -
     without this you'd see "Expecting value: line 1 column 1" errors).
  2. Retries with a short exponential backoff for transient blips, but
     deliberately not a long one - if the block is real (common per the
     issue above), a large retry budget just delays the inevitable failure
     rather than recovering.
  3. Races Yahoo against NSE-direct concurrently (a different provider with
     an independent block list) instead of trying them sequentially, and
     returns whichever succeeds first.
  4. Caches the Nifty 50 index history in memory, since it's identical for
     every company analyzed and re-fetching it on every request was
     needlessly doubling our request volume against both providers.

If BOTH Yahoo and NSE-direct fail back to back, that is a genuine dual
temporary outage/block - wait 10-15 minutes between test runs rather than
retrying immediately, which only extends the cool-down.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Callable
import pandas as pd
import yfinance as yf
from curl_cffi import requests as curl_requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.data_sources.nse_direct import fetch_daily_history_nse, fetch_index_history_nse

# A single shared impersonating session, reused across all calls in this
# process. "chrome" mimics a current desktop Chrome's fingerprint.
_SESSION = curl_requests.Session(impersonate="chrome")

# In-memory cache for the market index (same for every company request).
# TTL of 6 hours is plenty since this is only used for a daily-return
# regression, not intraday trading.
_INDEX_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_INDEX_CACHE_TTL_SECONDS = 6 * 60 * 60


def to_yahoo_ticker(symbol: str, exchange: str = "NSE") -> str:
    symbol = symbol.upper().strip()
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    return f"{symbol}{suffix}"


_retry_yahoo = retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=2, min=2, max=8),  # 2s, 8s...
    retry=retry_if_exception_type(Exception),
    reraise=True,
)


def _race_providers(fetchers: dict[str, Callable[[], pd.DataFrame]]) -> tuple[pd.DataFrame | None, dict[str, Exception]]:
    """
    Runs each named fetch function concurrently and returns the first
    successful result immediately, without waiting for slower/failing
    providers to finish. If all fail, returns (None, {name: error, ...})
    so the caller can build a provider-specific error message.
    """
    pool = ThreadPoolExecutor(max_workers=len(fetchers))
    futures = {pool.submit(fn): name for name, fn in fetchers.items()}
    errors: dict[str, Exception] = {}
    try:
        for future in as_completed(futures):
            name = futures[future]
            try:
                return future.result(), {}
            except Exception as exc:
                errors[name] = exc
    finally:
        # wait=False: don't block the caller on a losing/hung provider -
        # its thread finishes on its own and the result is discarded.
        pool.shutdown(wait=False)
    return None, errors


def _flatten_yahoo_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Newer yfinance versions return MultiIndex columns (field, ticker) even
    for a single-ticker download. Left as-is, `df["Adj Close"]` silently
    returns a 1-column DataFrame instead of a Series, which later breaks
    any code doing boolean checks on it (e.g. `if returns.std() == 0:` in
    analysis/risk.py raises "truth value of a Series is ambiguous"). Flatten
    to plain single-level columns regardless of yfinance version.
    """
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


@_retry_yahoo
def _fetch_daily_history_yahoo(symbol: str, exchange: str, years: int) -> pd.DataFrame:
    ticker = to_yahoo_ticker(symbol, exchange)
    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)  # small buffer
    df = yf.download(
        ticker, start=start, end=end, progress=False,
        auto_adjust=False, session=_SESSION,
    )
    if df.empty:
        raise ValueError(f"No price data returned for {ticker} from Yahoo Finance.")
    df = _flatten_yahoo_columns(df)
    df.index.name = "Date"
    return df


def fetch_daily_history(symbol: str, exchange: str = "NSE", years: int = 5) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by date with columns:
    Open, High, Low, Close, Adj Close, Volume
    covering the trailing `years` years, day by day.

    Races Yahoo Finance against NSE's own direct historical data endpoint
    (nse_direct.py) - a different provider with an independent block list -
    and returns whichever succeeds first. Only if BOTH fail does this raise,
    with a message explaining that's likely a temporary dual outage rather
    than a bug.
    """
    df, errors = _race_providers({
        "Yahoo Finance": lambda: _fetch_daily_history_yahoo(symbol, exchange, years),
        "NSE-direct": lambda: fetch_daily_history_nse(symbol, years),
    })
    if df is not None:
        return df
    raise ValueError(
        f"Could not fetch price history for {symbol} from either source.\n"
        f"Yahoo Finance error: {errors.get('Yahoo Finance')}\n"
        f"NSE-direct error: {errors.get('NSE-direct')}\n"
        f"Both providers independently blocking/failing at once "
        f"usually means a temporary IP-level rate limit - wait "
        f"10-15 minutes before retrying."
    )


@_retry_yahoo
def _fetch_index_uncached(index_symbol: str, years: int) -> pd.DataFrame:
    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)
    df = yf.download(
        index_symbol, start=start, end=end, progress=False,
        auto_adjust=False, session=_SESSION,
    )
    if df.empty:
        raise ValueError(f"No index data returned for {index_symbol}.")
    return _flatten_yahoo_columns(df)


def fetch_index_history(index_symbol: str = "^NSEI", years: int = 5) -> pd.DataFrame:
    """
    Default is the Nifty 50 (^NSEI) - used as the market proxy for CAPM beta.
    Cached in memory across requests since it's identical for every company
    (this alone roughly halves our request volume under load). Races
    against NSE's own index-history endpoint if Yahoo is slow/blocked, same
    as fetch_daily_history above.
    """
    cache_key = f"{index_symbol}:{years}"
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        cached_at, df = cached
        if time.time() - cached_at < _INDEX_CACHE_TTL_SECONDS:
            return df

    df, errors = _race_providers({
        "Yahoo Finance": lambda: _fetch_index_uncached(index_symbol, years),
        "NSE-direct": lambda: fetch_index_history_nse("NIFTY 50", years),
    })
    if df is None:
        raise ValueError(
            f"Could not fetch index history from either source.\n"
            f"Yahoo Finance error: {errors.get('Yahoo Finance')}\n"
            f"NSE-direct error: {errors.get('NSE-direct')}\n"
            f"Wait 10-15 minutes before retrying."
        )

    _INDEX_CACHE[cache_key] = (time.time(), df)
    return df


def daily_returns(price_df: pd.DataFrame, price_col: str = "Adj Close") -> pd.Series:
    prices = price_df[price_col]
    if isinstance(prices, pd.DataFrame):
        # Defensive: guarantee a Series even if an upstream source ever
        # hands back duplicate/MultiIndex columns for this field again.
        prices = prices.iloc[:, 0]
    return prices.pct_change().dropna()
