"""Version 1 API router."""
from fastapi import APIRouter

from app.api.v1 import audit, auth, system, users

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(audit.router)
