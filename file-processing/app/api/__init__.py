from fastapi import APIRouter
from app.api.endpoints import upload, download, zip, move, delete

api_router = APIRouter()

api_router.include_router(upload.router, tags=["upload"])
api_router.include_router(download.router, tags=["download"])
api_router.include_router(zip.router, tags=["zip"])
api_router.include_router(move.router, tags=["move"])
api_router.include_router(delete.router, tags=["delete"])

__all__ = ["api_router"]
