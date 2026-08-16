"""
Free, no-key price history via yfinance. NSE tickers use a `.NS` suffix,
BSE tickers use `.BO`. This is the most reliable free source of day-by-day
historical data for Indian equities.

IMPORTANT — cloud hosting note:
Yahoo Finance aggressively blocks requests coming from shared datacenter IP
ranges (Render, Railway, AWS, etc.), returning an empty/non-JSON response
instead of data. This shows up as errors like:
    "Expecting value: line 1 column 1 (char 0)"
    "YFTzMissingError: possibly delisted; no timezone found"
even for perfectly valid, actively-traded tickers - it is not a code bug.

The fix (recommended by yfinance's own maintainers for exactly this
scenario) is to route requests through `curl_cffi`, which impersonates a
real browser's TLS/HTTP fingerprint rather than Python's default request
signature that Yahoo flags. See requirements.txt for the added dependency.
"""
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
from curl_cffi import requests as curl_requests
from tenacity import retry, stop_after_attempt, wait_fixed

# A single shared impersonating session, reused across all calls in this
# process. "chrome" mimics a current desktop Chrome's fingerprint.
_SESSION = curl_requests.Session(impersonate="chrome")


def to_yahoo_ticker(symbol: str, exchange: str = "NSE") -> str:
    symbol = symbol.upper().strip()
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    return f"{symbol}{suffix}"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
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
            f"Finance blocking the request rather than an invalid symbol - "
            f"check the symbol is correct, and if it still fails, see the "
            f"cloud hosting note at the top of this file."
        )
    df.index.name = "Date"
    return df


def fetch_index_history(index_symbol: str = "^NSEI", years: int = 5) -> pd.DataFrame:
    """
    Default is the Nifty 50 (^NSEI) - used as the market proxy for CAPM beta.
    """
    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)
    df = yf.download(
        index_symbol, start=start, end=end, progress=False,
        auto_adjust=False, session=_SESSION,
    )
    if df.empty:
        raise ValueError(f"No index data returned for {index_symbol}.")
    return df


def daily_returns(price_df: pd.DataFrame, price_col: str = "Adj Close") -> pd.Series:
    return price_df[price_col].pct_change().dropna()
