"""
Capital Asset Pricing Model:
    Cost of Equity = Rf + Beta * (Rm - Rf)

Beta is estimated via OLS regression of the stock's daily returns against
the Nifty 50's daily returns over the trailing window (default 5 years,
but the backtest module re-runs this with a shorter window ending in the
past — see analysis/backtest.py).

Risk-free rate default: 10-year Indian G-Sec yield. This is hardcoded as a
sensible fallback (update periodically) — for production accuracy, wire in
a live G-Sec yield source instead of the constant below.
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm

from app.models import CapmResult

DEFAULT_RISK_FREE_RATE = 0.069        # ~6.9% - update to current 10yr G-Sec yield
DEFAULT_MARKET_RETURN_ASSUMPTION = 0.12  # long-run Indian equity market return assumption


def estimate_beta(stock_returns: pd.Series, market_returns: pd.Series) -> tuple[float, float]:
    """
    Returns (beta, r_squared) from OLS regression: stock_return = alpha + beta * market_return
    """
    aligned = pd.concat([stock_returns, market_returns], axis=1, join="inner").dropna()
    aligned.columns = ["stock", "market"]
    if len(aligned) < 30:
        raise ValueError("Not enough overlapping data points to estimate beta reliably.")

    X = sm.add_constant(aligned["market"])
    model = sm.OLS(aligned["stock"], X).fit()
    beta = model.params["market"]
    r_squared = model.rsquared
    return float(beta), float(r_squared)


def compute_capm(
    stock_returns: pd.Series,
    market_returns: pd.Series,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    market_return_assumption: float = DEFAULT_MARKET_RETURN_ASSUMPTION,
    window_years: int = 5,
) -> CapmResult:
    beta, r_squared = estimate_beta(stock_returns, market_returns)
    cost_of_equity = risk_free_rate + beta * (market_return_assumption - risk_free_rate)

    return CapmResult(
        beta=round(beta, 3),
        risk_free_rate=risk_free_rate,
        market_return_assumption=market_return_assumption,
        cost_of_equity=round(cost_of_equity, 4),
        r_squared=round(r_squared, 3),
        regression_window_years=window_years,
    )
