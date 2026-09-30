from fastapi import APIRouter

from app.api.routes import health, lab, qualification

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(qualification.router)
api_router.include_router(lab.router)