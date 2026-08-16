"""
Price history via yfinance (primary) with an automatic fallback to NSE's
own direct historical data endpoint (nse_direct.py) if Yahoo fails.
NSE tickers use a `.NS` suffix, BSE tickers use `.BO`.

IMPORTANT — cloud hosting note:
Yahoo Finance aggressively blocks/rate-limits requests coming from shared
datacenter IP ranges (Render, Railway, AWS, etc.) - an ongoing, widely
reported issue throughout 2025-2026 (see
https://github.com/ranaroussi/yfinance/issues/2422). This module:
  1. Impersonates a real browser via curl_cffi (bypasses outright blocking -
     without this you'd see "Expecting value: line 1 column 1" errors).
  2. Retries with real exponential backoff (tens of seconds), since
     YFRateLimitError needs genuine cool-down time, not a quick retry.
  3. Falls back automatically to NSE's own direct endpoint if Yahoo still
     fails after retries - a different provider with an independent block
     list, so one being down doesn't necessarily mean the other is too.
  4. Caches the Nifty 50 index history in memory, since it's identical for
     every company analyzed and re-fetching it on every request was
     needlessly doubling our request volume against both providers.

If BOTH Yahoo and NSE-direct fail back to back, that is a genuine dual
temporary outage/block - wait 10-15 minutes between test runs rather than
retrying immediately, which only extends the cool-down.
"""
import time
from datetime import datetime, timedelta
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
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=5, min=5, max=60),  # 5s, 10s, 20s, 40s...
    retry=retry_if_exception_type(Exception),
    reraise=True,
)


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

    Tries Yahoo Finance first; if that fails (blocked/rate-limited - a known,
    ongoing issue on shared cloud IPs), automatically falls back to NSE's own
    direct historical data endpoint (nse_direct.py), which is a different
    provider with an independent block list. Only if BOTH fail does this
    raise, with a message explaining that's likely a temporary dual outage
    rather than a bug.
    """
    try:
        return _fetch_daily_history_yahoo(symbol, exchange, years)
    except Exception as yahoo_error:
        try:
            return fetch_daily_history_nse(symbol, years)
        except Exception as nse_error:
            raise ValueError(
                f"Could not fetch price history for {symbol} from either "
                f"source.\nYahoo Finance error: {yahoo_error}\n"
                f"NSE-direct error: {nse_error}\n"
                f"Both providers independently blocking/failing at once "
                f"usually means a temporary IP-level rate limit - wait "
                f"10-15 minutes before retrying."
            ) from nse_error


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
    (this alone roughly halves our request volume under load). Falls back
    to NSE's own index-history endpoint if Yahoo fails, same as
    fetch_daily_history above.
    """
    cache_key = f"{index_symbol}:{years}"
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        cached_at, df = cached
        if time.time() - cached_at < _INDEX_CACHE_TTL_SECONDS:
            return df

    try:
        df = _fetch_index_uncached(index_symbol, years)
    except Exception as yahoo_error:
        try:
            df = fetch_index_history_nse("NIFTY 50", years)
        except Exception as nse_error:
            raise ValueError(
                f"Could not fetch index history from either source.\n"
                f"Yahoo Finance error: {yahoo_error}\nNSE-direct error: {nse_error}\n"
                f"Wait 10-15 minutes before retrying."
            ) from nse_error

    _INDEX_CACHE[cache_key] = (time.time(), df)
    return df


def daily_returns(price_df: pd.DataFrame, price_col: str = "Adj Close") -> pd.Series:
    prices = price_df[price_col]
    if isinstance(prices, pd.DataFrame):
        # Defensive: guarantee a Series even if an upstream source ever
        # hands back duplicate/MultiIndex columns for this field again.
        prices = prices.iloc[:, 0]
    return prices.pct_change().dropna()
