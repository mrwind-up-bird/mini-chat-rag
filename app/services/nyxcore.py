"""NyxCore Axiom API client — query-time RAG retrieval from external knowledge base."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://nyxcore.cloud"
DEFAULT_LIMIT = 10
DEFAULT_TIMEOUT = 15


@dataclass
class AxiomChunk:
    """A chunk returned from NyxCore Axiom search."""

    content: str
    heading: str | None
    filename: str
    authority: str  # "mandatory" | "guideline" | "informational"
    score: float
    chunk_id: str
    document_id: str


async def search_axiom(
    api_token: str,
    query: str,
    base_url: str = DEFAULT_BASE_URL,
    limit: int = DEFAULT_LIMIT,
    authority: list[str] | None = None,
) -> list[AxiomChunk]:
    """Search the NyxCore Axiom knowledge base.

    Args:
        api_token: Bearer token (nyx_ax_... format).
        query: Search query string.
        base_url: NyxCore base URL.
        limit: Max results (1-50).
        authority: Filter by authority level(s).

    Returns:
        List of AxiomChunk results sorted by score.
    """
    body: dict = {"query": query, "limit": limit}
    if authority:
        body["authority"] = authority

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/api/v1/rag/search",
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    if not data.get("ok"):
        error = data.get("error", {})
        raise RuntimeError(
            f"Axiom search failed: {error.get('code', 'UNKNOWN')} — {error.get('message', '')}"
        )

    return [
        AxiomChunk(
            content=r["content"],
            heading=r.get("heading"),
            filename=r.get("filename", ""),
            authority=r.get("authority", "informational"),
            score=r.get("score", 0.0),
            chunk_id=r.get("chunkId", ""),
            document_id=r.get("documentId", ""),
        )
        for r in data.get("results", [])
    ]


async def list_axiom_documents(
    api_token: str,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict]:
    """List documents in the NyxCore Axiom knowledge base.

    Used during ingestion to validate the connection.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{base_url.rstrip('/')}/api/v1/rag/documents",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    if not data.get("ok"):
        error = data.get("error", {})
        raise RuntimeError(
            f"Axiom list failed: {error.get('code', 'UNKNOWN')} — {error.get('message', '')}"
        )

    return data.get("documents", [])
