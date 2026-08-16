"""
Computes the standard set of ratios/multiples a sell-side note covers:
profitability, leverage, liquidity, efficiency, and valuation. Works off
the normalized fundamentals dict produced by either fundamentals_api.py
or screener_scraper.py (pipeline.py handles picking whichever succeeded).
"""
from app.models import RatioTrend


def _to_float(value: str | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("%", "").replace("₹", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# Metrics where a RISING value is actually a bad sign (lower is generally
# better). Everything not in this set is treated as higher-is-better.
LOWER_IS_BETTER_METRICS = {"Debt to Equity", "P/E", "EV/EBITDA", "Price to Book"}


def _trend_direction(values: list[float], metric: str | None = None) -> str:
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return "stable"
    delta = values[-1] - values[0]
    pct_change = delta / abs(values[0]) if values[0] else 0

    rising_is_good = metric not in LOWER_IS_BETTER_METRICS

    if abs(pct_change) <= 0.05:
        return "stable"
    rose = pct_change > 0
    if rose == rising_is_good:
        return "improving"
    return "deteriorating"


# Maps our internal metric name -> the row label used in Screener's tables
METRIC_ROW_LABELS = {
    "Net Profit Margin %": "Net Profit",  # derived vs Sales, see compute below
    "ROE %": "ROE %",
    "ROCE %": "ROCE %",
    "Debt to Equity": "Debt to equity",
    "Current Ratio": "Current ratio",
    "Interest Coverage": "Interest coverage",
    "P/E": "PE",
    "EV/EBITDA": "EV/EBITDA",
    "Price to Book": "PBV",
}


def compute_ratio_trends(ratios_5yr_table: dict, profit_loss_table: dict) -> list[RatioTrend]:
    """
    ratios_5yr_table / profit_loss_table: {row_label: {year: value_str}}
    as returned by screener_scraper.parse_financial_table (or the API
    equivalent, normalized to the same shape upstream).
    """
    trends: list[RatioTrend] = []

    # Direct pass-through ratios that already exist as rows
    for metric, row_label in METRIC_ROW_LABELS.items():
        row = ratios_5yr_table.get(row_label)
        if not row:
            continue
        values_by_year = {yr: _to_float(v) for yr, v in row.items()}
        clean_values = [v for v in values_by_year.values() if v is not None]
        trends.append(RatioTrend(
            metric=metric,
            values_by_year=values_by_year,
            trend=_trend_direction(clean_values, metric),
        ))

    # Derived ratio: Net Profit Margin % = Net Profit / Sales
    sales_row = profit_loss_table.get("Sales") or profit_loss_table.get("Sales ")
    profit_row = profit_loss_table.get("Net Profit") or profit_loss_table.get("Net Profit ")
    if sales_row and profit_row:
        margin_by_year = {}
        for year in sales_row:
            sales = _to_float(sales_row.get(year))
            profit = _to_float(profit_row.get(year))
            if sales and profit is not None and sales != 0:
                margin_by_year[year] = round(100 * profit / sales, 2)
        if margin_by_year:
            trends.append(RatioTrend(
                metric="Net Profit Margin %",
                values_by_year=margin_by_year,
                trend=_trend_direction(list(margin_by_year.values()), "Net Profit Margin %"),
            ))

    return trends
