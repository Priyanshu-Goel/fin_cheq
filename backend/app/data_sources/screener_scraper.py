"""
Free fallback source: scrapes the public company page on screener.in for
ratios and multi-year financials when INDIAN_API_KEY isn't configured or
the API call fails.

IMPORTANT — operational notes:
- This scrapes PUBLIC pages only (no login), for personal research use.
- Respect Screener's robots.txt and rate limits: add delays if you scrape
  many companies in a loop, and cache aggressively (data only changes when
  a company reports results, i.e. quarterly at most).
- Screener's HTML structure can change without notice. If this stops
  working, that's the known trade-off of a free scraping source (see
  README section 2) - the fix is to update the CSS selectors below, not to
  assume the whole pipeline is broken.
"""
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (research-assistant; personal, non-commercial use)"}
BASE_URL = "https://www.screener.in/company"


def build_screener_url(nse_symbol: str) -> str:
    return f"{BASE_URL}/{nse_symbol.upper()}/"


def fetch_company_page(nse_symbol: str, polite_delay_sec: float = 1.0) -> BeautifulSoup:
    time.sleep(polite_delay_sec)  # be a polite scraper
    resp = requests.get(build_screener_url(nse_symbol), headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def parse_top_ratios(soup: BeautifulSoup) -> dict:
    """
    Parses the top "ratios" grid (Market Cap, P/E, ROCE, Dividend Yield, etc.)
    shown at the top of a Screener company page.
    """
    ratios = {}
    for li in soup.select("#top-ratios li"):
        name_el = li.select_one(".name")
        value_el = li.select_one(".number")
        if name_el and value_el:
            ratios[name_el.get_text(strip=True)] = value_el.get_text(strip=True)
    return ratios


def parse_financial_table(soup: BeautifulSoup, section_id: str) -> dict:
    """
    Parses a yearly financial table (e.g. section_id='profit-loss',
    'balance-sheet', 'ratios') into {row_label: {year: value}}.
    """
    section = soup.select_one(f"#{section_id}")
    if not section:
        return {}
    table = section.select_one("table")
    if not table:
        return {}

    headers = [th.get_text(strip=True) for th in table.select("thead th")][1:]
    data = {}
    for row in table.select("tbody tr"):
        cells = row.select("td")
        if not cells:
            continue
        label = cells[0].get_text(strip=True)
        values = [c.get_text(strip=True) for c in cells[1:]]
        data[label] = dict(zip(headers, values))
    return data


def fetch_all_fundamentals(nse_symbol: str) -> dict:
    soup = fetch_company_page(nse_symbol)
    return {
        "top_ratios": parse_top_ratios(soup),
        "profit_loss": parse_financial_table(soup, "profit-loss"),
        "balance_sheet": parse_financial_table(soup, "balance-sheet"),
        "cash_flow": parse_financial_table(soup, "cash-flow"),
        "ratios_5yr": parse_financial_table(soup, "ratios"),
    }
