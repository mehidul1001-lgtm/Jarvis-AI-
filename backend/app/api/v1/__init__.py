"""Version 1 API router."""

from fastapi import APIRouter

from app.api.v1 import agents, audit, auth, chat, memory, system, tasks, users

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(audit.router)
api_router.include_router(chat.router)
api_router.include_router(memory.router)
api_router.include_router(tasks.router)
api_router.include_router(agents.router)
