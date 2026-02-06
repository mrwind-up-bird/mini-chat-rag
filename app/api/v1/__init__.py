"""V1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1.api_tokens import router as api_tokens_router
from app.api.v1.bot_profiles import router as bot_profiles_router
from app.api.v1.chat import router as chat_router
from app.api.v1.sources import router as sources_router
from app.api.v1.tenants import router as tenants_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(tenants_router)
v1_router.include_router(api_tokens_router)
v1_router.include_router(bot_profiles_router)
v1_router.include_router(sources_router)
v1_router.include_router(chat_router)
