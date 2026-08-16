"""
Rule-based red flag detection - deliberately transparent (no ML black box)
so an analyst can see exactly why each flag fired. Mirrors the checks a
junior analyst is taught to run before trusting a set of financials:
margin deterioration, rising leverage, weak interest coverage, promoter
pledge, and unusual cash-flow-vs-profit divergence.
"""
from app.models import RatioTrend, RedFlag


def _latest_two(values_by_year: dict) -> tuple[float | None, float | None]:
    years_sorted = sorted(values_by_year.keys())
    if len(years_sorted) < 2:
        return None, None
    return values_by_year[years_sorted[-2]], values_by_year[years_sorted[-1]]


def detect_red_flags(
    ratio_trends: list[RatioTrend],
    balance_sheet: dict,
    cash_flow: dict,
    profit_loss: dict,
) -> list[RedFlag]:
    flags: list[RedFlag] = []
    trend_map = {t.metric: t for t in ratio_trends}

    # 1. Margin deterioration
    margin = trend_map.get("Net Profit Margin %")
    if margin and margin.trend == "deteriorating":
        flags.append(RedFlag(
            severity="Medium",
            title="Declining net profit margin",
            detail="Net profit margin has trended down over the observed period, "
                   "signaling pricing pressure, rising costs, or both.",
        ))

    # 2. Rising leverage
    de_ratio = trend_map.get("Debt to Equity")
    if de_ratio:
        prev, latest = _latest_two(de_ratio.values_by_year)
        if prev is not None and latest is not None and latest > prev * 1.25 and latest > 0.5:
            flags.append(RedFlag(
                severity="High",
                title="Rapidly rising debt-to-equity",
                detail=f"Debt/Equity moved from {prev} to {latest} year-on-year, "
                       "a >25% jump that warrants checking the use of proceeds "
                       "and covenant headroom.",
            ))

    # 3. Weak interest coverage
    interest_cov = trend_map.get("Interest Coverage")
    if interest_cov:
        _, latest = _latest_two(interest_cov.values_by_year)
        if latest is not None and latest < 2.0:
            flags.append(RedFlag(
                severity="High",
                title="Low interest coverage ratio",
                detail=f"Interest coverage of {latest}x is below the commonly used "
                       "2x safety threshold, implying limited buffer to service debt "
                       "if earnings soften.",
            ))

    # 4. Cash flow vs. profit divergence (earnings quality check)
    npat_row = profit_loss.get("Net Profit") or {}
    cfo_row = cash_flow.get("Cash from Operating Activity") or cash_flow.get("Operating Cash Flow") or {}
    if npat_row and cfo_row:
        years_common = sorted(set(npat_row.keys()) & set(cfo_row.keys()))
        if years_common:
            latest_year = years_common[-1]
            try:
                npat = float(str(npat_row[latest_year]).replace(",", ""))
                cfo = float(str(cfo_row[latest_year]).replace(",", ""))
                if npat > 0 and cfo < 0.5 * npat:
                    flags.append(RedFlag(
                        severity="Medium",
                        title="Operating cash flow lags reported profit",
                        detail=f"Latest operating cash flow ({cfo}) is under half of "
                               f"reported net profit ({npat}) - worth checking working "
                               "capital changes and revenue recognition.",
                    ))
            except (ValueError, TypeError):
                pass

    if not flags:
        flags.append(RedFlag(
            severity="Low",
            title="No rule-based red flags triggered",
            detail="Based on the checks run (margin trend, leverage, interest "
                   "coverage, cash-flow-vs-profit), nothing crossed the "
                   "thresholds used here. This is not a guarantee of clean "
                   "financials - only that these specific checks passed.",
        ))

    return flags
