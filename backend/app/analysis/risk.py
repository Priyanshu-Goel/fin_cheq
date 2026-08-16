"""
Standard quantitative risk metrics computed off daily returns:
- Annualized volatility (std dev * sqrt(252))
- Sharpe ratio (using the same risk-free rate as CAPM)
- Maximum drawdown over the lookback window
- Value at Risk (95% historical, 1-day)
Then rolls these into a simple risk grade for a quick read at the top of
the research note.
"""
import numpy as np
import pandas as pd

from app.analysis.capm import DEFAULT_RISK_FREE_RATE
from app.models import RiskAssessment

TRADING_DAYS_PER_YEAR = 252


def annualized_volatility(returns: pd.Series) -> float:
    return float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = DEFAULT_RISK_FREE_RATE) -> float:
    excess_daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess_returns = returns - excess_daily_rf
    if returns.std() == 0:
        return 0.0
    return float((excess_returns.mean() / returns.std()) * np.sqrt(TRADING_DAYS_PER_YEAR))


def max_drawdown(price_series: pd.Series) -> float:
    cumulative_max = price_series.cummax()
    drawdown = (price_series - cumulative_max) / cumulative_max
    return float(drawdown.min())  # negative number, e.g. -0.35 = -35%


def value_at_risk_95(returns: pd.Series) -> float:
    """Historical 1-day 95% VaR, expressed as a negative return (loss)."""
    return float(np.percentile(returns, 5))


def _grade_risk(vol: float, dd: float) -> str:
    if vol < 0.20 and dd > -0.25:
        return "Low"
    if vol < 0.35 and dd > -0.45:
        return "Moderate"
    if vol < 0.55:
        return "High"
    return "Very High"


def assess_risk(price_df: pd.DataFrame, returns: pd.Series) -> RiskAssessment:
    vol = annualized_volatility(returns)
    sharpe = sharpe_ratio(returns)
    dd = max_drawdown(price_df["Adj Close"])
    var95 = value_at_risk_95(returns)

    return RiskAssessment(
        annualized_volatility=round(vol, 4),
        sharpe_ratio=round(sharpe, 3),
        max_drawdown=round(dd, 4),
        value_at_risk_95=round(var95, 4),
        risk_grade=_grade_risk(vol, dd),
    )
