from typing import Optional
from pydantic import BaseModel


class AnalyzeRequest(BaseModel):
    company_name: str          # e.g. "Infosys" or "Tata Motors"
    nse_symbol: Optional[str] = None   # e.g. "INFY" - if known, skip lookup
    exchange: str = "NSE"      # "NSE" or "BSE"


class RatioTrend(BaseModel):
    metric: str
    values_by_year: dict        # {"2022": 1.2, "2023": 1.4, ...}
    trend: str                  # "improving" | "deteriorating" | "stable"


class CapmResult(BaseModel):
    beta: float
    risk_free_rate: float
    market_return_assumption: float
    cost_of_equity: float
    r_squared: float
    regression_window_years: int


class RiskAssessment(BaseModel):
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    value_at_risk_95: float
    risk_grade: str             # "Low" | "Moderate" | "High" | "Very High"


class RedFlag(BaseModel):
    severity: str                # "Low" | "Medium" | "High"
    title: str
    detail: str


class BacktestResult(BaseModel):
    as_of_date: str
    predicted_expected_return: float
    actual_realized_return: float
    error: float
    directional_hit: bool
    mae: float
    rmse: float


class PricePoint(BaseModel):
    date: str
    close: float


class AnalyzeResponse(BaseModel):
    company_name: str
    symbol: str
    exchange: str
    summary: str
    ratios: list[RatioTrend]
    capm: CapmResult
    risk: RiskAssessment
    red_flags: list[RedFlag]
    backtest: BacktestResult
    price_history: list[PricePoint] = []
    excel_url: str
    pdf_url: str
