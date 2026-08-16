"""
Simple sliding-window chunker. Good enough for annual report / transcript
text; swap for a semantic/section-aware chunker later if retrieval quality
needs improving (e.g. split on MD&A section headers).
"""


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    text = " ".join(text.split())  # normalize whitespace
    if len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def chunk_documents(documents: list[dict]) -> list[dict]:
    """
    documents: [{"source": ..., "title": ..., "url": ..., "text": ...}, ...]
    returns a flat list of {"source", "title", "url", "chunk"} ready to embed.
    """
    out = []
    for doc in documents:
        for chunk in chunk_text(doc.get("text", "")):
            out.append({
                "source": doc.get("source"),
                "title": doc.get("title"),
                "url": doc.get("url"),
                "chunk": chunk,
            })
    return out
