from fastapi import APIRouter

from . import auth, projects

admin_router = APIRouter()
admin_router.include_router(auth.router, tags=["admin-auth"])
admin_router.include_router(projects.router, tags=["admin-projects"])
