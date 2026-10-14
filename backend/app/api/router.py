from fastapi import APIRouter

from app.api.v1 import admin, auctions, auth

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(auctions.router)
api_router.include_router(admin.router)
