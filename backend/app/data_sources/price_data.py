"""
Free, no-key price history via yfinance. NSE tickers use a `.NS` suffix,
BSE tickers use `.BO`. This is the most reliable free source of day-by-day
historical data for Indian equities.

IMPORTANT — cloud hosting note:
Yahoo Finance aggressively blocks/rate-limits requests coming from shared
datacenter IP ranges (Render, Railway, AWS, etc.). This module:
  1. Impersonates a real browser via curl_cffi (bypasses outright blocking -
     without this you'd see "Expecting value: line 1 column 1" errors).
  2. Caches the Nifty 50 index history in memory, since it's identical for
     every company analyzed and re-fetching it on every single request was
     needlessly doubling our Yahoo request volume.
  3. Uses a longer exponential backoff on retry, since YFRateLimitError
     needs real cool-down time (tens of seconds), not a quick 2-second retry.

If you still see YFRateLimitError bursts during heavy testing, that's Yahoo
throttling the whole shared IP (other Render tenants' traffic counts too) -
wait a few minutes between test runs rather than clicking repeatedly.
"""
import time
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
from curl_cffi import requests as curl_requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

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


@_retry_yahoo
def fetch_daily_history(symbol: str, exchange: str = "NSE", years: int = 5) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by date with columns:
    Open, High, Low, Close, Adj Close, Volume
    covering the trailing `years` years, day by day.
    """
    ticker = to_yahoo_ticker(symbol, exchange)
    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)  # small buffer
    df = yf.download(
        ticker, start=start, end=end, progress=False,
        auto_adjust=False, session=_SESSION,
    )
    if df.empty:
        raise ValueError(
            f"No price data returned for {ticker}. This is usually Yahoo "
            f"Finance blocking/rate-limiting the request rather than an "
            f"invalid symbol - check the symbol is correct, and if it keeps "
            f"failing, wait a few minutes before retrying (see the cloud "
            f"hosting note at the top of this file)."
        )
    df.index.name = "Date"
    return df


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
    return df


def fetch_index_history(index_symbol: str = "^NSEI", years: int = 5) -> pd.DataFrame:
    """
    Default is the Nifty 50 (^NSEI) - used as the market proxy for CAPM beta.
    Cached in memory across requests since it's identical for every company
    (this alone roughly halves our Yahoo request volume under load).
    """
    cache_key = f"{index_symbol}:{years}"
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        cached_at, df = cached
        if time.time() - cached_at < _INDEX_CACHE_TTL_SECONDS:
            return df

    df = _fetch_index_uncached(index_symbol, years)
    _INDEX_CACHE[cache_key] = (time.time(), df)
    return df


def daily_returns(price_df: pd.DataFrame, price_col: str = "Adj Close") -> pd.Series:
    return price_df[price_col].pct_change().dropna()
