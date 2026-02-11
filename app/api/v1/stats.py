"""Usage statistics endpoints."""

import uuid
from datetime import timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core import cache
from app.core.pricing import MODEL_PRICING, calc_cost, get_pricing
from app.models.base import utcnow
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


class BotUsage(BaseModel):
    bot_profile_id: uuid.UUID
    bot_name: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int
    cost_usd: float


class ModelUsage(BaseModel):
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int
    cost_usd: float
    prompt_cost_per_1m: float
    completion_cost_per_1m: float


class CostEstimate(BaseModel):
    total_cost_usd: float
    daily_avg_cost_usd: float
    projected_monthly_usd: float
    active_days: int
    by_model: list[ModelUsage]
    by_bot: list[BotUsage]


class ModelPricingEntry(BaseModel):
    prompt_cost_per_1m: float
    completion_cost_per_1m: float


class PricingResponse(BaseModel):
    models: dict[str, ModelPricingEntry]
    default: ModelPricingEntry


# ── Routes ───────────────────────────────────────────────────

@router.get("/pricing", response_model=PricingResponse)
async def get_pricing_map(auth: Auth) -> PricingResponse:
    """Return the model pricing table so clients don't need a local copy."""
    return PricingResponse(
        models={
            model: ModelPricingEntry(
                prompt_cost_per_1m=rates[0],
                completion_cost_per_1m=rates[1],
            )
            for model, rates in MODEL_PRICING.items()
        },
        default=ModelPricingEntry(
            prompt_cost_per_1m=get_pricing("__unknown__")[0],
            completion_cost_per_1m=get_pricing("__unknown__")[1],
        ),
    )

@router.get("/overview", response_model=OverviewStats)
async def get_overview(auth: Auth, session: Session) -> OverviewStats:
    """Summary counts for the tenant."""
    tid = auth.tenant_id
    cache_key = ("stats", "overview", tid)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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

    result = OverviewStats(
        bot_profiles=bp_count,
        sources=src_count,
        chats=chat_count,
        total_prompt_tokens=token_sums[0],
        total_completion_tokens=token_sums[1],
        total_tokens=token_sums[2],
    )
    cache.put(cache_key, result)
    return result


@router.get("/usage", response_model=list[DailyUsage])
async def get_usage(
    auth: Auth,
    session: Session,
    days: int | None = None,
) -> list[DailyUsage]:
    """Token usage aggregated by day and model."""
    cache_key = ("stats", "usage", auth.tenant_id, days)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    filters = [UsageEvent.tenant_id == auth.tenant_id]
    if days is not None:
        filters.append(UsageEvent.created_at >= utcnow() - timedelta(days=days))
    date_col = func.date(UsageEvent.created_at)
    stmt = (
        select(
            date_col.label("date"),
            UsageEvent.model,
            func.sum(UsageEvent.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageEvent.completion_tokens).label("completion_tokens"),
            func.sum(UsageEvent.total_tokens).label("total_tokens"),
            func.count().label("request_count"),
        )
        .where(*filters)
        .group_by(date_col, UsageEvent.model)
        .order_by(date_col.desc())
    )
    result = await session.execute(stmt)

    data = [
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
    cache.put(cache_key, data)
    return data


@router.get("/usage/by-bot", response_model=list[BotUsage])
async def get_usage_by_bot(
    auth: Auth,
    session: Session,
    days: int | None = None,
) -> list[BotUsage]:
    """Token usage aggregated by bot profile."""
    cache_key = ("stats", "by-bot", auth.tenant_id, days)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    filters = [UsageEvent.tenant_id == auth.tenant_id]
    if days is not None:
        filters.append(UsageEvent.created_at >= utcnow() - timedelta(days=days))
    stmt = (
        select(
            UsageEvent.bot_profile_id,
            BotProfile.name.label("bot_name"),
            UsageEvent.model,
            func.sum(UsageEvent.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageEvent.completion_tokens).label("completion_tokens"),
            func.sum(UsageEvent.total_tokens).label("total_tokens"),
            func.count().label("request_count"),
        )
        .join(BotProfile, UsageEvent.bot_profile_id == BotProfile.id)
        .where(*filters)
        .group_by(UsageEvent.bot_profile_id, BotProfile.name, UsageEvent.model)
        .order_by(func.sum(UsageEvent.total_tokens).desc())
    )
    result = await session.execute(stmt)

    data = [
        BotUsage(
            bot_profile_id=row.bot_profile_id,
            bot_name=row.bot_name,
            model=row.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            request_count=row.request_count,
            cost_usd=round(calc_cost(row.model, row.prompt_tokens, row.completion_tokens), 6),
        )
        for row in result.all()
    ]
    cache.put(cache_key, data)
    return data


@router.get("/usage/by-model", response_model=list[ModelUsage])
async def get_usage_by_model(
    auth: Auth,
    session: Session,
    days: int | None = None,
) -> list[ModelUsage]:
    """Token usage aggregated by model with cost breakdown."""
    cache_key = ("stats", "by-model", auth.tenant_id, days)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    filters = [UsageEvent.tenant_id == auth.tenant_id]
    if days is not None:
        filters.append(UsageEvent.created_at >= utcnow() - timedelta(days=days))
    stmt = (
        select(
            UsageEvent.model,
            func.sum(UsageEvent.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageEvent.completion_tokens).label("completion_tokens"),
            func.sum(UsageEvent.total_tokens).label("total_tokens"),
            func.count().label("request_count"),
        )
        .where(*filters)
        .group_by(UsageEvent.model)
        .order_by(func.sum(UsageEvent.total_tokens).desc())
    )
    result = await session.execute(stmt)

    data = [
        ModelUsage(
            model=row.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            request_count=row.request_count,
            cost_usd=round(calc_cost(row.model, row.prompt_tokens, row.completion_tokens), 6),
            prompt_cost_per_1m=get_pricing(row.model)[0],
            completion_cost_per_1m=get_pricing(row.model)[1],
        )
        for row in result.all()
    ]
    cache.put(cache_key, data)
    return data


@router.get("/cost-estimate", response_model=CostEstimate)
async def get_cost_estimate(
    auth: Auth,
    session: Session,
    days: int = 30,
) -> CostEstimate:
    """Cost summary and projected monthly spend.

    Looks at the last `days` days (default 30) to calculate averages
    and project monthly costs.
    """
    tid = auth.tenant_id
    cache_key = ("stats", "cost-estimate", tid, days)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    cutoff = utcnow() - timedelta(days=days)

    # Per-model aggregation over the window
    model_stmt = (
        select(
            UsageEvent.model,
            func.sum(UsageEvent.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageEvent.completion_tokens).label("completion_tokens"),
            func.sum(UsageEvent.total_tokens).label("total_tokens"),
            func.count().label("request_count"),
        )
        .where(UsageEvent.tenant_id == tid, UsageEvent.created_at >= cutoff)
        .group_by(UsageEvent.model)
        .order_by(func.sum(UsageEvent.total_tokens).desc())
    )
    model_result = (await session.execute(model_stmt)).all()

    # Per-bot aggregation over the window
    bot_stmt = (
        select(
            UsageEvent.bot_profile_id,
            BotProfile.name.label("bot_name"),
            UsageEvent.model,
            func.sum(UsageEvent.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageEvent.completion_tokens).label("completion_tokens"),
            func.sum(UsageEvent.total_tokens).label("total_tokens"),
            func.count().label("request_count"),
        )
        .join(BotProfile, UsageEvent.bot_profile_id == BotProfile.id)
        .where(UsageEvent.tenant_id == tid, UsageEvent.created_at >= cutoff)
        .group_by(UsageEvent.bot_profile_id, BotProfile.name, UsageEvent.model)
        .order_by(func.sum(UsageEvent.total_tokens).desc())
    )
    bot_result = (await session.execute(bot_stmt)).all()

    # Count distinct active days in the window
    days_stmt = (
        select(func.count(func.distinct(func.date(UsageEvent.created_at))))
        .where(UsageEvent.tenant_id == tid, UsageEvent.created_at >= cutoff)
    )
    active_days = (await session.execute(days_stmt)).scalar_one() or 0

    # Build model breakdown
    by_model = []
    total_cost = 0.0
    for row in model_result:
        cost = calc_cost(row.model, row.prompt_tokens, row.completion_tokens)
        total_cost += cost
        p_rate, c_rate = get_pricing(row.model)
        by_model.append(ModelUsage(
            model=row.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            request_count=row.request_count,
            cost_usd=round(cost, 6),
            prompt_cost_per_1m=p_rate,
            completion_cost_per_1m=c_rate,
        ))

    # Build bot breakdown
    by_bot = [
        BotUsage(
            bot_profile_id=row.bot_profile_id,
            bot_name=row.bot_name,
            model=row.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            request_count=row.request_count,
            cost_usd=round(calc_cost(row.model, row.prompt_tokens, row.completion_tokens), 6),
        )
        for row in bot_result
    ]

    daily_avg = total_cost / active_days if active_days > 0 else 0.0

    result = CostEstimate(
        total_cost_usd=round(total_cost, 6),
        daily_avg_cost_usd=round(daily_avg, 6),
        projected_monthly_usd=round(daily_avg * 30, 2),
        active_days=active_days,
        by_model=by_model,
        by_bot=by_bot,
    )
    cache.put(cache_key, result)
    return result
