from fastapi import APIRouter

from . import chat, projects

api_router = APIRouter()
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(chat.router, prefix="/public", tags=["public-chat"])
