"""
Thin wrapper around Supabase's Postgres + pgvector for storing document
chunks and retrieving the most relevant ones per query. Uses Supabase's
REST client so no direct DB driver/connection pooling headaches on Render's
free tier.

Requires the `documents` table + `vector` extension set up per README
section 5, Step 1.
"""
from app.config import settings
from app.rag.embeddings import embed_texts, embed_query
from supabase import create_client, Client


def _client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


def store_chunks(company_ticker: str, chunks: list[dict]) -> int:
    """
    chunks: [{"source", "title", "url", "chunk"}, ...] from chunker.py
    Returns the number of rows inserted.
    """
    if not chunks:
        return 0

    texts = [c["chunk"] for c in chunks]
    embeddings = embed_texts(texts)

    rows = [
        {
            "company_ticker": company_ticker,
            "source": c.get("source"),
            "content": c["chunk"],
            "embedding": embeddings[i],
        }
        for i, c in enumerate(chunks)
    ]

    client = _client()
    client.table("documents").insert(rows).execute()
    return len(rows)


def retrieve_relevant_chunks(company_ticker: str, query: str, top_k: int = 6) -> list[str]:
    """
    Cosine-similarity search scoped to one company's documents.
    Requires a Postgres function `match_documents` - see the SQL snippet
    in README section 5 (add it alongside the table creation) or below:

        create or replace function match_documents(
          query_embedding vector(384), match_ticker text, match_count int
        ) returns table(content text, similarity float)
        language sql stable as $$
          select content, 1 - (embedding <=> query_embedding) as similarity
          from documents
          where company_ticker = match_ticker
          order by embedding <=> query_embedding
          limit match_count;
        $$;
    """
    query_embedding = embed_query(query)
    client = _client()
    response = client.rpc("match_documents", {
        "query_embedding": query_embedding,
        "match_ticker": company_ticker,
        "match_count": top_k,
    }).execute()
    return [row["content"] for row in (response.data or [])]
