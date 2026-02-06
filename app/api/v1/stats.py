"""Usage statistics endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, cast, Date
from sqlmodel import select

from app.api.deps import Auth, Session
from app.models.bot_profile import BotProfile
from app.models.chat import Chat
from app.models.source import Source
from app.models.usage_event import UsageEvent

router = APIRouter(prefix="/stats", tags=["stats"])


# ── Schemas ──────────────────────────────────────────────────

class OverviewStats(BaseModel):
    bot_profiles: int
    sources: int
    chats: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int


class DailyUsage(BaseModel):
    date: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int


# ── Routes ───────────────────────────────────────────────────

@router.get("/overview", response_model=OverviewStats)
async def get_overview(auth: Auth, session: Session) -> OverviewStats:
    """Summary counts for the tenant."""
    tid = auth.tenant_id

    bp_count = (await session.execute(
        select(func.count()).select_from(BotProfile).where(BotProfile.tenant_id == tid)
    )).scalar_one()

    src_count = (await session.execute(
        select(func.count()).select_from(Source).where(Source.tenant_id == tid)
    )).scalar_one()

    chat_count = (await session.execute(
        select(func.count()).select_from(Chat).where(Chat.tenant_id == tid)
    )).scalar_one()

    token_sums = (await session.execute(
        select(
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
        ).where(UsageEvent.tenant_id == tid)
    )).one()

    return OverviewStats(
        bot_profiles=bp_count,
        sources=src_count,
        chats=chat_count,
        total_prompt_tokens=token_sums[0],
        total_completion_tokens=token_sums[1],
        total_tokens=token_sums[2],
    )


@router.get("/usage", response_model=list[DailyUsage])
async def get_usage(auth: Auth, session: Session) -> list[DailyUsage]:
    """Token usage aggregated by day and model."""
    stmt = (
        select(
            cast(UsageEvent.created_at, Date).label("date"),
            UsageEvent.model,
            func.sum(UsageEvent.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageEvent.completion_tokens).label("completion_tokens"),
            func.sum(UsageEvent.total_tokens).label("total_tokens"),
            func.count().label("request_count"),
        )
        .where(UsageEvent.tenant_id == auth.tenant_id)
        .group_by(cast(UsageEvent.created_at, Date), UsageEvent.model)
        .order_by(cast(UsageEvent.created_at, Date).desc())
    )
    result = await session.execute(stmt)

    return [
        DailyUsage(
            date=str(row.date),
            model=row.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            request_count=row.request_count,
        )
        for row in result.all()
    ]
