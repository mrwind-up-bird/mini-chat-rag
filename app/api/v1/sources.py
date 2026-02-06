"""Source CRUD — all queries scoped to tenant_id."""

import json
import uuid
from pathlib import Path

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import Auth, Session
from app.models.base import utcnow
from app.models.bot_profile import BotProfile
from app.models.source import Source, SourceCreate, SourceRead, SourceStatus, SourceType, SourceUpdate
from app.services.extract import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, extract_text
from app.workers.main import _redis_settings

router = APIRouter(prefix="/sources", tags=["sources"])


def _to_read(src: Source) -> SourceRead:
    # config is stored as JSON text, deserialize for the response
    config = json.loads(src.config) if isinstance(src.config, str) else src.config
    return SourceRead(
        id=src.id,
        tenant_id=src.tenant_id,
        bot_profile_id=src.bot_profile_id,
        name=src.name,
        description=src.description,
        source_type=src.source_type,
        status=src.status,
        config=config,
        document_count=src.document_count,
        chunk_count=src.chunk_count,
        error_message=src.error_message,
        is_active=src.is_active,
        created_at=src.created_at,
        updated_at=src.updated_at,
    )


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreate,
    auth: Auth,
    session: Session,
) -> SourceRead:
    # Verify bot_profile belongs to the same tenant
    await _verify_bot_profile(body.bot_profile_id, auth.tenant_id, session)

    src = Source(
        tenant_id=auth.tenant_id,
        bot_profile_id=body.bot_profile_id,
        name=body.name,
        description=body.description,
        source_type=body.source_type,
        config=json.dumps(body.config),
        content=body.content,
    )
    session.add(src)
    await session.commit()
    await session.refresh(src)
    return _to_read(src)


@router.get("", response_model=list[SourceRead])
async def list_sources(
    auth: Auth,
    session: Session,
    bot_profile_id: uuid.UUID | None = None,
) -> list[SourceRead]:
    stmt = select(Source).where(Source.tenant_id == auth.tenant_id)
    if bot_profile_id is not None:
        stmt = stmt.where(Source.bot_profile_id == bot_profile_id)
    stmt = stmt.order_by(Source.created_at.desc())  # type: ignore[union-attr]

    result = await session.execute(stmt)
    return [_to_read(s) for s in result.scalars().all()]


@router.post("/upload", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def upload_source(
    file: UploadFile,
    auth: Auth,
    session: Session,
    bot_profile_id: uuid.UUID = Form(...),
    name: str | None = Form(None),
    description: str = Form(""),
) -> SourceRead:
    """Upload a file, extract text, create Source, and auto-trigger ingestion."""
    # Validate extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    content = await file.read()

    # Validate size
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )

    # Verify bot profile ownership
    await _verify_bot_profile(bot_profile_id, auth.tenant_id, session)

    # Extract text
    extracted = extract_text(file.filename or "file.txt", content)

    src = Source(
        tenant_id=auth.tenant_id,
        bot_profile_id=bot_profile_id,
        name=name or file.filename or "Uploaded file",
        description=description,
        source_type=SourceType.UPLOAD,
        config=json.dumps({"original_filename": file.filename, "file_size": len(content)}),
        content=extracted,
    )
    session.add(src)
    await session.commit()
    await session.refresh(src)

    # Auto-trigger ingestion
    try:
        redis: ArqRedis = await create_pool(_redis_settings())
        try:
            await redis.enqueue_job(
                "ingest_source",
                source_id=str(src.id),
                tenant_id=str(auth.tenant_id),
            )
        finally:
            await redis.aclose()
    except Exception:
        pass  # Source is created; ingestion can be triggered manually

    return _to_read(src)


@router.get("/{source_id}", response_model=SourceRead)
async def get_source(
    source_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> SourceRead:
    src = await _get_or_404(source_id, auth.tenant_id, session)
    return _to_read(src)


@router.patch("/{source_id}", response_model=SourceRead)
async def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    auth: Auth,
    session: Session,
) -> SourceRead:
    src = await _get_or_404(source_id, auth.tenant_id, session)

    update_data = body.model_dump(exclude_unset=True)

    if "config" in update_data:
        update_data["config"] = json.dumps(update_data["config"])

    for field, value in update_data.items():
        setattr(src, field, value)

    src.updated_at = utcnow()
    session.add(src)
    await session.commit()
    await session.refresh(src)
    return _to_read(src)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> None:
    src = await _get_or_404(source_id, auth.tenant_id, session)
    src.is_active = False
    src.updated_at = utcnow()
    session.add(src)
    await session.commit()


# ── Ingestion trigger ─────────────────────────────────────────

class IngestResponse(BaseModel):
    status: str
    message: str


@router.post("/{source_id}/ingest", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_ingest(
    source_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> IngestResponse:
    """Enqueue an ingestion job for the given source."""
    src = await _get_or_404(source_id, auth.tenant_id, session)

    if src.status == SourceStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source is already being processed",
        )

    redis: ArqRedis = await create_pool(_redis_settings())
    try:
        await redis.enqueue_job(
            "ingest_source",
            source_id=str(source_id),
            tenant_id=str(auth.tenant_id),
        )
    finally:
        await redis.aclose()

    return IngestResponse(status="accepted", message=f"Ingestion queued for source {source_id}")


# ── Internal helpers ──────────────────────────────────────────

async def _get_or_404(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session,
) -> Source:
    stmt = select(Source).where(
        Source.id == source_id,
        Source.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    src = result.scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return src


async def _verify_bot_profile(
    bot_profile_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session,
) -> None:
    """Ensure the referenced bot_profile belongs to the same tenant."""
    stmt = select(BotProfile.id).where(
        BotProfile.id == bot_profile_id,
        BotProfile.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Bot profile not found or belongs to a different tenant",
        )
