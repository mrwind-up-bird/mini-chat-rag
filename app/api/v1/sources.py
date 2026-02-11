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
from app.models.source import (
    Source,
    SourceCreate,
    SourceRead,
    SourceStatus,
    SourceType,
    SourceUpdate,
)
from app.services.extract import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, extract_text
from app.workers.main import _redis_settings

router = APIRouter(prefix="/sources", tags=["sources"])


# ── Schemas for batch / children endpoints ────────────────────


class BatchChildCreate(BaseModel):
    name: str
    description: str = ""
    source_type: SourceType
    config: dict = {}
    content: str | None = None


class BatchSourceCreate(BaseModel):
    bot_profile_id: uuid.UUID
    name: str
    description: str = ""
    source_type: SourceType = SourceType.URL
    children: list[BatchChildCreate]


class BatchSourceResponse(BaseModel):
    parent: SourceRead
    children: list[SourceRead]


class IngestResponse(BaseModel):
    status: str
    message: str


class IngestChildrenResponse(BaseModel):
    status: str
    message: str
    enqueued: int


# ── Helpers ───────────────────────────────────────────────────


def _to_read(
    src: Source,
    children_count: int = 0,
    agg_status: SourceStatus | None = None,
    agg_chunk_count: int | None = None,
) -> SourceRead:
    """Convert a Source ORM instance to SourceRead schema."""
    config = json.loads(src.config) if isinstance(src.config, str) else src.config
    return SourceRead(
        id=src.id,
        tenant_id=src.tenant_id,
        bot_profile_id=src.bot_profile_id,
        parent_id=src.parent_id,
        name=src.name,
        description=src.description,
        source_type=src.source_type,
        status=agg_status if agg_status is not None else src.status,
        config=config,
        document_count=src.document_count,
        chunk_count=agg_chunk_count if agg_chunk_count is not None else src.chunk_count,
        error_message=src.error_message,
        is_active=src.is_active,
        refresh_schedule=src.refresh_schedule,
        last_refreshed_at=src.last_refreshed_at,
        children_count=children_count,
        created_at=src.created_at,
        updated_at=src.updated_at,
    )


def _aggregate_status(children: list[Source]) -> SourceStatus:
    """Determine parent status from children statuses."""
    if not children:
        return SourceStatus.PENDING
    statuses = {c.status for c in children}
    if SourceStatus.PROCESSING in statuses:
        return SourceStatus.PROCESSING
    if SourceStatus.ERROR in statuses:
        return SourceStatus.ERROR
    if statuses == {SourceStatus.READY}:
        return SourceStatus.READY
    return SourceStatus.PENDING


async def _get_children(parent_id: uuid.UUID, tenant_id: uuid.UUID, session) -> list[Source]:
    """Fetch active children of a parent source."""
    stmt = (
        select(Source)
        .where(
            Source.parent_id == parent_id,
            Source.tenant_id == tenant_id,
            Source.is_active == True,  # noqa: E712
        )
        .order_by(Source.created_at.asc())
    )  # type: ignore[union-attr]
    result = await session.execute(stmt)
    return list(result.scalars().all())


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


async def _validate_parent(
    parent_id: uuid.UUID,
    tenant_id: uuid.UUID,
    bot_profile_id: uuid.UUID,
    session,
) -> Source:
    """Validate parent_id: exists, same tenant, same bot_profile, no grandparenting."""
    parent = await _get_or_404(parent_id, tenant_id, session)
    if parent.bot_profile_id != bot_profile_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Parent source belongs to a different bot profile",
        )
    if parent.parent_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Nesting beyond one level is not allowed",
        )
    return parent


async def _enqueue_ingest(source_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    """Enqueue an ARQ ingest job. Swallows Redis errors."""
    redis: ArqRedis = await create_pool(_redis_settings())
    try:
        await redis.enqueue_job(
            "ingest_source",
            source_id=str(source_id),
            tenant_id=str(tenant_id),
        )
    finally:
        await redis.aclose()


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/batch", response_model=BatchSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_batch_source(
    body: BatchSourceCreate,
    auth: Auth,
    session: Session,
) -> BatchSourceResponse:
    """Create a parent source + children in one transaction."""
    await _verify_bot_profile(body.bot_profile_id, auth.tenant_id, session)

    if len(body.children) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one child source is required",
        )

    # Create parent (no content)
    parent = Source(
        tenant_id=auth.tenant_id,
        bot_profile_id=body.bot_profile_id,
        name=body.name,
        description=body.description,
        source_type=body.source_type,
    )
    session.add(parent)
    await session.flush()

    # Create children
    children = []
    for child_data in body.children:
        child = Source(
            tenant_id=auth.tenant_id,
            bot_profile_id=body.bot_profile_id,
            parent_id=parent.id,
            name=child_data.name,
            description=child_data.description,
            source_type=child_data.source_type,
            config=json.dumps(child_data.config),
            content=child_data.content,
        )
        session.add(child)
        children.append(child)

    await session.commit()
    await session.refresh(parent)
    for c in children:
        await session.refresh(c)

    return BatchSourceResponse(
        parent=_to_read(parent, children_count=len(children)),
        children=[_to_read(c) for c in children],
    )


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreate,
    auth: Auth,
    session: Session,
) -> SourceRead:
    # Verify bot_profile belongs to the same tenant
    await _verify_bot_profile(body.bot_profile_id, auth.tenant_id, session)

    # Validate parent if provided
    if body.parent_id is not None:
        await _validate_parent(body.parent_id, auth.tenant_id, body.bot_profile_id, session)

    src = Source(
        tenant_id=auth.tenant_id,
        bot_profile_id=body.bot_profile_id,
        parent_id=body.parent_id,
        name=body.name,
        description=body.description,
        source_type=body.source_type,
        config=json.dumps(body.config),
        content=body.content,
        refresh_schedule=body.refresh_schedule,
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
    parent_id: uuid.UUID | None = None,
    include_children: bool = False,
) -> list[SourceRead]:
    stmt = select(Source).where(
        Source.tenant_id == auth.tenant_id,
        Source.is_active == True,  # noqa: E712
    )

    if bot_profile_id is not None:
        stmt = stmt.where(Source.bot_profile_id == bot_profile_id)

    if parent_id is not None:
        # Fetch children of a specific parent
        stmt = stmt.where(Source.parent_id == parent_id)
    elif not include_children:
        # Default: only top-level sources (no parent)
        stmt = stmt.where(Source.parent_id == None)  # noqa: E711

    stmt = stmt.order_by(Source.created_at.desc())  # type: ignore[union-attr]

    result = await session.execute(stmt)
    sources = list(result.scalars().all())

    # For top-level listing, compute children_count and aggregated stats for parents
    if parent_id is None and not include_children:
        reads = []
        for src in sources:
            children = await _get_children(src.id, auth.tenant_id, session)
            if children:
                agg_status = _aggregate_status(children)
                agg_chunk_count = sum(c.chunk_count for c in children)
                reads.append(
                    _to_read(
                        src,
                        children_count=len(children),
                        agg_status=agg_status,
                        agg_chunk_count=agg_chunk_count,
                    )
                )
            else:
                reads.append(_to_read(src))
        return reads

    return [_to_read(s) for s in sources]


@router.get("/{source_id}/children", response_model=list[SourceRead])
async def list_source_children(
    source_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> list[SourceRead]:
    """Return child sources of a parent, ordered by created_at asc."""
    await _get_or_404(source_id, auth.tenant_id, session)
    children = await _get_children(source_id, auth.tenant_id, session)
    return [_to_read(c) for c in children]


@router.post("/upload", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def upload_source(
    file: UploadFile,
    auth: Auth,
    session: Session,
    bot_profile_id: uuid.UUID = Form(...),
    name: str | None = Form(None),
    description: str = Form(""),
    parent_id: uuid.UUID | None = Form(None),
) -> SourceRead:
    """Upload a file, extract text, create Source, and auto-trigger ingestion."""
    # Validate extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported file type: {ext}. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
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

    # Validate parent if provided
    if parent_id is not None:
        await _validate_parent(parent_id, auth.tenant_id, bot_profile_id, session)

    # Extract text
    extracted = extract_text(file.filename or "file.txt", content)

    src = Source(
        tenant_id=auth.tenant_id,
        bot_profile_id=bot_profile_id,
        parent_id=parent_id,
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
        await _enqueue_ingest(src.id, auth.tenant_id)
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
    children = await _get_children(src.id, auth.tenant_id, session)
    if children:
        agg_status = _aggregate_status(children)
        agg_chunk_count = sum(c.chunk_count for c in children)
        return _to_read(
            src,
            children_count=len(children),
            agg_status=agg_status,
            agg_chunk_count=agg_chunk_count,
        )
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

    # Cascade soft-delete to children if this is a parent
    children = await _get_children(src.id, auth.tenant_id, session)
    for child in children:
        child.is_active = False
        child.updated_at = utcnow()
        session.add(child)

    src.is_active = False
    src.updated_at = utcnow()
    session.add(src)
    await session.commit()


# ── Ingestion triggers ────────────────────────────────────────


@router.post(
    "/{source_id}/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
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

    await _enqueue_ingest(source_id, auth.tenant_id)

    return IngestResponse(status="accepted", message=f"Ingestion queued for source {source_id}")


@router.post(
    "/{source_id}/ingest-children",
    response_model=IngestChildrenResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_ingest_children(
    source_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> IngestChildrenResponse:
    """Enqueue ingest jobs for all non-processing children of a parent source."""
    await _get_or_404(source_id, auth.tenant_id, session)
    children = await _get_children(source_id, auth.tenant_id, session)

    enqueued = 0
    for child in children:
        if child.status != SourceStatus.PROCESSING:
            await _enqueue_ingest(child.id, auth.tenant_id)
            enqueued += 1

    return IngestChildrenResponse(
        status="accepted",
        message=f"Ingestion queued for {enqueued} child sources",
        enqueued=enqueued,
    )
