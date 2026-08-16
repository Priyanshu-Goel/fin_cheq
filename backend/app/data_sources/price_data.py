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

# NOTE: deliberately NOT a shared module-level session. curl_cffi's Session
# wraps a libcurl handle that is not safe for concurrent use across threads -
# now that Yahoo/NSE are raced concurrently and the pipeline fetches price
# and index history in parallel (see pipeline.py), two threads can end up
# hitting the same session at once. That was silently corrupting requests -
# manifesting as Yahoo returning an empty result instead of an explicit
# error, at every window size, which is what tipped this off (a real
# throttle would still fail on a large window and succeed on a small one;
# this failed identically regardless of size). A fresh session per call
# costs a little setup time but guarantees no cross-thread sharing.
def _new_yahoo_session() -> curl_requests.Session:
    return curl_requests.Session(impersonate="chrome")

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


def _year_ladder(years: int) -> list[int]:
    """
    Descending list of window sizes to try, e.g. 5 -> [5, 3, 2, 1].
    Yahoo has been observed to return a genuinely empty result (not a
    rate-limit exception) for a large date-range request while the same
    ticker succeeds on a smaller one - consistent with a soft throttle on
    request size/cost rather than a hard IP block. Rather than fail
    outright, each window is tried in turn; partial history is far more
    useful to the user than none. Downstream analysis doesn't require
    exactly `years` of data - CAPM/risk work off whatever length comes
    back, and the backtest independently shrinks its own lookback to fit.
    """
    candidates = [years, 3, 2, 1]
    ladder = []
    for y in candidates:
        if 1 <= y <= years and y not in ladder:
            ladder.append(y)
    return ladder


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
        auto_adjust=False, session=_new_yahoo_session(),
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
    and returns whichever succeeds first. If both fail for the requested
    window, automatically retries with a shorter window (see _year_ladder)
    before giving up - a large request is more likely to be silently
    throttled than a small one. Only if every window fails on both
    providers does this raise, with a message explaining that's likely a
    temporary dual outage rather than a bug.
    """
    errors_by_window: dict[int, dict[str, Exception]] = {}
    for window_years in _year_ladder(years):
        df, errors = _race_providers({
            "Yahoo Finance": lambda w=window_years: _fetch_daily_history_yahoo(symbol, exchange, w),
            "NSE-direct": lambda w=window_years: fetch_daily_history_nse(symbol, w),
        })
        if df is not None:
            return df
        errors_by_window[window_years] = errors

    smallest_window = min(errors_by_window)
    last_errors = errors_by_window[smallest_window]
    raise ValueError(
        f"Could not fetch price history for {symbol} from either source, even after "
        f"shrinking the requested window down to {smallest_window} year(s).\n"
        f"Yahoo Finance error: {last_errors.get('Yahoo Finance')}\n"
        f"NSE-direct error: {last_errors.get('NSE-direct')}\n"
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
        auto_adjust=False, session=_new_yahoo_session(),
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

    errors_by_window: dict[int, dict[str, Exception]] = {}
    for window_years in _year_ladder(years):
        df, errors = _race_providers({
            "Yahoo Finance": lambda w=window_years: _fetch_index_uncached(index_symbol, w),
            "NSE-direct": lambda w=window_years: fetch_index_history_nse("NIFTY 50", w),
        })
        if df is not None:
            _INDEX_CACHE[cache_key] = (time.time(), df)
            return df
        errors_by_window[window_years] = errors

    smallest_window = min(errors_by_window)
    last_errors = errors_by_window[smallest_window]
    raise ValueError(
        f"Could not fetch index history from either source, even after shrinking the "
        f"requested window down to {smallest_window} year(s).\n"
        f"Yahoo Finance error: {last_errors.get('Yahoo Finance')}\n"
        f"NSE-direct error: {last_errors.get('NSE-direct')}\n"
        f"Wait 10-15 minutes before retrying."
    )


def daily_returns(price_df: pd.DataFrame, price_col: str = "Adj Close") -> pd.Series:
    prices = price_df[price_col]
    if isinstance(prices, pd.DataFrame):
        # Defensive: guarantee a Series even if an upstream source ever
        # hands back duplicate/MultiIndex columns for this field again.
        prices = prices.iloc[:, 0]
    return prices.pct_change().dropna()
