"""
Embeddings computed locally with sentence-transformers (all-MiniLM-L6-v2,
384-dim, free, no API cost) rather than a paid embeddings API - this is the
single biggest cost saver in the whole pipeline since embedding calls scale
with document volume, unlike the one-shot LLM call for note generation.

Model downloads (~80MB) on first use and is cached by the library - on
Render's free tier this adds to cold-start time; if that's a problem later,
swap in a hosted embeddings endpoint instead (Render supports persistent
disks on paid tiers).
"""
from functools import lru_cache
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME)


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return vectors.tolist()


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
