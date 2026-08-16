"""
Backtests the pipeline's own signal: re-runs the CAPM expected-return
calculation using only data available as of `years_ago` years before today,
then compares that expected return to what the stock actually returned over
the following year. This tells you how well the model's own methodology
would have predicted outcomes historically - it is NOT a trading strategy
backtest (no position sizing, entries/exits, or transaction costs).

Metrics reported:
- MAE / RMSE of expected vs. actual return (magnitude of error)
- Directional hit-rate (did it correctly call outperformance vs underperformance
  of the risk-free rate)

For a single company this is a sample size of 1 - the aggregate accuracy
becomes meaningful once you've run the pipeline across many companies and
these results accumulate in the `backtest_results` Supabase table (see
README section 3 and db.py).
"""
from datetime import datetime

import numpy as np
import pandas as pd

from app.analysis.capm import compute_capm, DEFAULT_RISK_FREE_RATE
from app.models import BacktestResult


def run_backtest(
    price_df: pd.DataFrame,
    index_df: pd.DataFrame,
    years_ago: int = 3,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> BacktestResult:
    price_df = price_df.sort_index()
    index_df = index_df.sort_index()

    as_of_date = price_df.index.max() - pd.DateOffset(years=years_ago)
    train_prices = price_df.loc[:as_of_date]
    train_index = index_df.loc[:as_of_date]

    if len(train_prices) < 260 or len(train_index) < 260:
        raise ValueError(
            f"Not enough historical data before {as_of_date.date()} to backtest "
            f"{years_ago} years back - need at least ~1 year of prior returns."
        )

    stock_returns_train = train_prices["Adj Close"].pct_change().dropna()
    market_returns_train = train_index["Adj Close"].pct_change().dropna()

    capm_train = compute_capm(
        stock_returns_train, market_returns_train,
        risk_free_rate=risk_free_rate, window_years=years_ago,
    )
    predicted_expected_return = capm_train.cost_of_equity

    # actual realized return over the following ~1 year (252 trading days)
    forward_window = price_df.loc[as_of_date:]
    if len(forward_window) < 200:
        raise ValueError("Not enough forward data to measure a full-year realized return.")

    price_start = forward_window["Adj Close"].iloc[0]
    price_end_idx = min(252, len(forward_window) - 1)
    price_end = forward_window["Adj Close"].iloc[price_end_idx]
    actual_realized_return = float((price_end - price_start) / price_start)

    error = predicted_expected_return - actual_realized_return
    directional_hit = (predicted_expected_return > risk_free_rate) == (actual_realized_return > risk_free_rate)

    mae = abs(error)
    rmse = float(np.sqrt(error ** 2))  # single-sample RMSE == |error|; kept for
    # shape-consistency once this is aggregated across many companies in Supabase

    return BacktestResult(
        as_of_date=str(as_of_date.date()),
        predicted_expected_return=round(predicted_expected_return, 4),
        actual_realized_return=round(actual_realized_return, 4),
        error=round(error, 4),
        directional_hit=directional_hit,
        mae=round(mae, 4),
        rmse=round(rmse, 4),
    )


def run_backtest_adaptive(
    price_df: pd.DataFrame,
    index_df: pd.DataFrame,
    years_ago: int = 3,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> BacktestResult:
    """
    Same as run_backtest, but shrinks `years_ago` down to 1 before giving up
    if there isn't enough history for the requested lookback. This matters
    now that fetch_daily_history/fetch_index_history can themselves return
    a shorter window than requested when a provider is throttling large
    date ranges (see price_data._year_ladder) - a 2-year price history
    shouldn't crash the whole analysis just because the default 3-year
    backtest lookback no longer fits.
    """
    last_error: ValueError | None = None
    for candidate_years_ago in range(years_ago, 0, -1):
        try:
            return run_backtest(price_df, index_df, years_ago=candidate_years_ago, risk_free_rate=risk_free_rate)
        except ValueError as exc:
            last_error = exc
    raise ValueError(
        f"Not enough historical price data to backtest the model's own forecast, even after "
        f"shrinking the lookback window down to 1 year ({len(price_df)} trading days available). "
        f"Underlying error: {last_error}"
    )
