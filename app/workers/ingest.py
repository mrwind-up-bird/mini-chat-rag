"""Ingestion worker task — processes a Source into chunks and vectors."""

from __future__ import annotations

import json
import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import async_session_factory
from app.models.base import utcnow
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.source import Source, SourceStatus
from app.services.chunking import chunk_text
from app.services.embedding import embed_texts
from app.services.html_extract import html_to_text
from app.services.vector_store import delete_by_source, ensure_collection, upsert_chunks

logger = logging.getLogger(__name__)

# Chunking defaults
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 64


async def ingest_source(ctx: dict, source_id: str, tenant_id: str) -> dict:
    """ARQ task: ingest a source — chunk, embed, upsert into Qdrant.

    Args:
        ctx: ARQ worker context.
        source_id: UUID of the Source to process.
        tenant_id: UUID of the owning tenant.

    Returns:
        dict with document_count and chunk_count.
    """
    async with async_session_factory() as session:
        try:
            source = await _get_source(session, source_id, tenant_id)
            if source is None:
                logger.error("Source %s not found for tenant %s", source_id, tenant_id)
                return {"error": "source_not_found"}

            # Mark as processing
            source.status = SourceStatus.PROCESSING
            source.error_message = None
            session.add(source)
            await session.commit()

            # 1. Extract raw content
            raw_content = await _extract_content(source)
            if not raw_content:
                await _mark_error(session, source, "No content to ingest")
                return {"error": "no_content"}

            # 2. Create document record
            doc = Document(
                tenant_id=uuid.UUID(tenant_id),
                source_id=uuid.UUID(source_id),
                title=source.name,
                raw_content=raw_content,
                char_count=len(raw_content),
            )
            session.add(doc)
            await session.flush()

            # 3. Chunk the content
            chunks = chunk_text(
                raw_content,
                chunk_size=DEFAULT_CHUNK_SIZE,
                chunk_overlap=DEFAULT_CHUNK_OVERLAP,
            )
            if not chunks:
                await _mark_error(session, source, "Chunking produced no output")
                return {"error": "no_chunks"}

            # 4. Embed all chunk texts
            texts = [c.content for c in chunks]
            embeddings = await embed_texts(texts)

            # 5. Ensure Qdrant collection exists
            await ensure_collection()

            # 6. Delete old vectors for this source (re-ingestion)
            await delete_by_source(tenant_id, source_id)

            # 7. Save chunks to SQL + prepare Qdrant points
            qdrant_points: list[dict] = []
            chunk_records: list[Chunk] = []

            for tc, vector in zip(chunks, embeddings):
                chunk_id = uuid.uuid4()
                chunk_rec = Chunk(
                    id=chunk_id,
                    tenant_id=uuid.UUID(tenant_id),
                    document_id=doc.id,
                    source_id=uuid.UUID(source_id),
                    chunk_index=tc.index,
                    content=tc.content,
                    char_count=tc.char_count,
                    qdrant_point_id=str(chunk_id),
                )
                chunk_records.append(chunk_rec)

                qdrant_points.append(
                    {
                        "id": str(chunk_id),
                        "vector": vector,
                        "payload": {
                            "tenant_id": tenant_id,
                            "source_id": source_id,
                            "bot_profile_id": str(source.bot_profile_id),
                            "document_id": str(doc.id),
                            "chunk_index": tc.index,
                            "content": tc.content,
                        },
                    }
                )

            session.add_all(chunk_records)

            # 8. Upsert vectors into Qdrant
            await upsert_chunks(qdrant_points)

            # 9. Update counters and mark ready
            doc.chunk_count = len(chunks)
            session.add(doc)

            source.status = SourceStatus.READY
            source.document_count = 1
            source.chunk_count = len(chunks)
            source.error_message = None
            source.last_refreshed_at = utcnow()
            session.add(source)
            await session.commit()

            logger.info("Ingested source %s: 1 document, %d chunks", source_id, len(chunks))

            # Dispatch webhook (fire-and-forget)
            try:
                from app.services.webhook_dispatch import dispatch_webhook_event

                await dispatch_webhook_event(session, tenant_id, "source.ingested", {
                    "source_id": source_id,
                    "source_name": source.name,
                    "document_count": 1,
                    "chunk_count": len(chunks),
                })
            except Exception:
                logger.warning("Webhook dispatch failed after ingest for source %s", source_id)

            return {"document_count": 1, "chunk_count": len(chunks)}

        except Exception as exc:
            logger.exception("Ingestion failed for source %s", source_id)
            await session.rollback()
            # Try to mark the source as errored
            try:
                source = await _get_source(session, source_id, tenant_id)
                if source:
                    await _mark_error(session, source, str(exc)[:2000])
            except Exception:
                logger.exception("Failed to mark source %s as errored", source_id)

            # Dispatch webhook for failure (fire-and-forget)
            try:
                from app.services.webhook_dispatch import dispatch_webhook_event

                await dispatch_webhook_event(session, tenant_id, "source.failed", {
                    "source_id": source_id,
                    "error": str(exc)[:500],
                })
            except Exception:
                logger.warning("Webhook dispatch failed after error for source %s", source_id)

            return {"error": str(exc)}


async def _extract_content(source: Source) -> str:
    """Extract raw text from a source based on its type."""
    if source.source_type == "text":
        return source.content or ""
    if source.source_type == "url":
        config = json.loads(source.config) if isinstance(source.config, str) else source.config
        url = config.get("url", "")
        if not url:
            return ""
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "MiniRAG/1.0"})
            resp.raise_for_status()
            return html_to_text(resp.text)
    # Upload sources: content already extracted at upload time
    return source.content or ""


async def _get_source(session: AsyncSession, source_id: str, tenant_id: str) -> Source | None:
    stmt = select(Source).where(
        Source.id == uuid.UUID(source_id),
        Source.tenant_id == uuid.UUID(tenant_id),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _mark_error(session: AsyncSession, source: Source, message: str) -> None:
    source.status = SourceStatus.ERROR
    source.error_message = message
    session.add(source)
    await session.commit()
