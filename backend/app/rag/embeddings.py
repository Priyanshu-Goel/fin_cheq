"""
Embeddings via Hugging Face's hosted Inference API (free) instead of running
the model locally. This replaces an earlier local sentence-transformers
approach that pulled in torch + transformers - great for embedding quality,
but a 1GB+ install that OOMs on Render's free 512MB tier.

Calling the same model (all-MiniLM-L6-v2, 384-dim) over HTTPS keeps this
server lightweight: just `requests`, no local model weights, minimal RAM.

Setup (free): huggingface.co -> Sign up -> Settings -> Access Tokens ->
create a token with "read" access -> put it in HF_API_TOKEN in your .env
(and in Render's environment variables).
"""
import time
import requests
from tenacity import retry, stop_after_attempt, wait_fixed

from app.config import settings

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
HF_API_URL = f"https://api-inference.huggingface.co/pipeline/feature-extraction/{MODEL_NAME}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.hf_api_token}"}


@retry(stop=stop_after_attempt(4), wait=wait_fixed(3))
def _call_hf_api(texts: list[str]) -> list[list[float]]:
    resp = requests.post(
        HF_API_URL,
        headers=_headers(),
        json={"inputs": texts, "options": {"wait_for_model": True}},
        timeout=60,
    )
    if resp.status_code == 503:
        # Model is cold-starting on HF's side - wait_for_model usually handles
        # this, but retry once more defensively.
        time.sleep(5)
        raise RuntimeError("HF model still loading, retrying...")
    resp.raise_for_status()
    result = resp.json()

    # The feature-extraction pipeline returns one vector per input text
    # (already mean-pooled) when inputs is a list of strings.
    return result


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    # HF free tier rate-limits fairly aggressively on batch size; chunk
    # requests to stay well within that.
    batch_size = 20
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        all_embeddings.extend(_call_hf_api(batch))
    return all_embeddings


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
