"""
Pulls the text of annual reports / earnings call transcripts / investor
presentations so the RAG pipeline has real source documents to ground the
research note in (rather than the LLM inventing qualitative commentary).

Two free sources, both scraped:
  1. Screener.in's "Documents" tab on the company page - links to annual
     reports, credit ratings, and (for many companies) concall transcripts.
  2. BSE announcements page - fallback for concall transcripts if not on
     Screener.

Returns a list of {source, title, url, text} dicts ready for chunking.
Downloading and parsing the actual PDFs is done with a lightweight text
extractor to keep this dependency-light; swap in the `pdf` skill's approach
if you need OCR for scanned reports.
"""
import io
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader

from app.data_sources.screener_scraper import build_screener_url, HEADERS


def list_document_links(nse_symbol: str) -> list[dict]:
    url = build_screener_url(nse_symbol) + "#documents"
    resp = requests.get(build_screener_url(nse_symbol), headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    links = []
    docs_section = soup.select_one("#documents")
    if not docs_section:
        return links

    for a in docs_section.select("a[href]"):
        href = a.get("href")
        title = a.get_text(strip=True)
        if href and href.startswith("http"):
            links.append({"title": title or "Document", "url": href})
    return links


def extract_pdf_text(pdf_url: str, max_pages: int = 60) -> str:
    resp = requests.get(pdf_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    reader = PdfReader(io.BytesIO(resp.content))
    pages = reader.pages[:max_pages]
    return "\n".join(page.extract_text() or "" for page in pages)


def fetch_source_documents(nse_symbol: str, max_docs: int = 5) -> list[dict]:
    """
    Returns up to `max_docs` documents with extracted text, skipping any
    that fail to download/parse (scanned/corrupt PDFs, dead links, etc.)
    rather than failing the whole run.
    """
    try:
        links = list_document_links(nse_symbol)[:max_docs]
    except Exception:
        # screener.in itself unreachable (observed: connections actively
        # refused from some hosts) - no source documents rather than
        # failing the whole analysis. note_generator.py already handles
        # an empty chunk list by grounding only in the quantitative
        # outputs instead of retrieved excerpts.
        return []

    # Each of these is an independent download + PDF parse (up to 30s
    # timeout apiece) - fetching them one at a time in a for-loop was a
    # real serial bottleneck whenever screener.in actually responds.
    def _fetch_one(link: dict) -> dict | None:
        try:
            text = extract_pdf_text(link["url"])
            if text.strip():
                return {"source": "screener_documents", **link, "text": text}
        except Exception:
            pass  # skip unreadable documents, don't fail the whole ingestion
        return None

    with ThreadPoolExecutor(max_workers=min(5, len(links)) or 1) as pool:
        results = pool.map(_fetch_one, links)
        docs = [doc for doc in results if doc is not None]
    return docs
