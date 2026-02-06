"""Embedding service — wraps LiteLLM for provider-agnostic vector generation."""

from __future__ import annotations

from litellm import aembedding

from app.core.config import get_settings

# Default embedding model — small, fast, cheap
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSIONS = 1536
MAX_BATCH_SIZE = 128


async def embed_texts(
    texts: list[str],
    model: str | None = None,
    api_key: str | None = None,
) -> list[list[float]]:
    """Generate embeddings for a batch of texts via LiteLLM.

    Args:
        texts: List of text strings to embed.
        model: Embedding model name (LiteLLM format). Defaults to text-embedding-3-small.
        api_key: Optional provider API key. If None, uses env vars (OPENAI_API_KEY, etc.).

    Returns:
        List of embedding vectors (same order as input texts).
    """
    if not texts:
        return []

    model = model or DEFAULT_EMBEDDING_MODEL
    all_embeddings: list[list[float]] = []

    # Process in batches
    for i in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[i : i + MAX_BATCH_SIZE]
        kwargs: dict = {"model": model, "input": batch}
        if api_key:
            kwargs["api_key"] = api_key

        response = await aembedding(**kwargs)
        batch_embeddings = [item["embedding"] for item in response.data]
        all_embeddings.extend(batch_embeddings)

    return all_embeddings


def get_embedding_dimensions(model: str | None = None) -> int:
    """Return the expected vector dimension for a given embedding model."""
    model = model or DEFAULT_EMBEDDING_MODEL
    # Known dimensions for common models
    dims = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    return dims.get(model, DEFAULT_EMBEDDING_DIMENSIONS)
