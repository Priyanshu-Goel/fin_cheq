"""
Free, no-key price history via yfinance. NSE tickers use a `.NS` suffix,
BSE tickers use `.BO`. This is the most reliable free source of day-by-day
historical data for Indian equities.
"""
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_fixed


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
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"No price data returned for {ticker}. Check the symbol/exchange.")
    df.index.name = "Date"
    return df


def fetch_index_history(index_symbol: str = "^NSEI", years: int = 5) -> pd.DataFrame:
    """
    Default is the Nifty 50 (^NSEI) - used as the market proxy for CAPM beta.
    """
    end = datetime.today()
    start = end - timedelta(days=365 * years + 30)
    df = yf.download(index_symbol, start=start, end=end, progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"No index data returned for {index_symbol}.")
    return df


def daily_returns(price_df: pd.DataFrame, price_col: str = "Adj Close") -> pd.Series:
    return price_df[price_col].pct_change().dropna()
