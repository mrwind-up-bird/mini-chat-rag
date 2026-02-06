"""Qdrant vector store service — collection management, upsert, search, delete."""

from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import get_settings
from app.services.embedding import get_embedding_dimensions

# One collection per environment (multi-tenancy via payload filtering)
COLLECTION_NAME = "minirag_chunks"

_client: AsyncQdrantClient | None = None


async def get_qdrant_client() -> AsyncQdrantClient:
    """Lazy-init a shared async Qdrant client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    return _client


async def ensure_collection(
    embedding_model: str | None = None,
) -> None:
    """Create the chunk collection if it doesn't exist."""
    client = await get_qdrant_client()
    collections = await client.get_collections()
    existing = {c.name for c in collections.collections}

    if COLLECTION_NAME not in existing:
        dims = get_embedding_dimensions(embedding_model)
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
        )


async def upsert_chunks(
    points: list[dict],
) -> None:
    """Upsert chunk vectors into Qdrant.

    Each point dict must have:
        id: str (UUID)
        vector: list[float]
        payload: dict with at least tenant_id, source_id, bot_profile_id,
                 chunk_index, content (for retrieval display)
    """
    client = await get_qdrant_client()
    qdrant_points = [
        PointStruct(
            id=p["id"],
            vector=p["vector"],
            payload=p["payload"],
        )
        for p in points
    ]
    # Qdrant batch limit is ~100 points; upsert handles larger batches internally
    await client.upsert(collection_name=COLLECTION_NAME, points=qdrant_points)


async def search_chunks(
    query_vector: list[float],
    tenant_id: str,
    bot_profile_id: str,
    limit: int = 5,
    score_threshold: float | None = None,
) -> list[dict]:
    """Search for similar chunks within a tenant + bot profile scope.

    Returns list of dicts with id, score, and payload.
    """
    client = await get_qdrant_client()
    query_filter = Filter(
        must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
            FieldCondition(key="bot_profile_id", match=MatchValue(value=bot_profile_id)),
        ]
    )
    response = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=query_filter,
        limit=limit,
        score_threshold=score_threshold,
    )
    return [
        {
            "id": str(hit.id),
            "score": hit.score,
            "payload": hit.payload,
        }
        for hit in response.points
    ]


async def delete_by_source(
    tenant_id: str,
    source_id: str,
) -> None:
    """Delete all vectors belonging to a specific source."""
    client = await get_qdrant_client()
    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="source_id", match=MatchValue(value=source_id)),
            ]
        ),
    )
