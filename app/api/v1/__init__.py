"""V1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1.api_tokens import router as api_tokens_router
from app.api.v1.auth import router as auth_router
from app.api.v1.bot_profiles import router as bot_profiles_router
from app.api.v1.chat import router as chat_router
from app.api.v1.sources import router as sources_router
from app.api.v1.stats import router as stats_router
from app.api.v1.system import router as system_router
from app.api.v1.tenants import router as tenants_router
from app.api.v1.users import router as users_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(tenants_router)
v1_router.include_router(auth_router)
v1_router.include_router(api_tokens_router)
v1_router.include_router(bot_profiles_router)
v1_router.include_router(sources_router)
v1_router.include_router(chat_router)
v1_router.include_router(users_router)
v1_router.include_router(stats_router)
v1_router.include_router(system_router)
