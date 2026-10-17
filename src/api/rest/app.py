from fastapi import FastAPI

from src.api.middleware.cors import register_cors_middleware
from src.api.rest.routes.health import HealthRouter
from src.api.rest.routes.job_route import JobRouter
from src.api.rest.routes.me import MeRouter
from src.config.settings import settings


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)
    register_cors_middleware(app)
    app.include_router(HealthRouter, prefix="/api/v1")
    app.include_router(MeRouter, prefix="/api/v1")
    app.include_router(JobRouter, prefix="/api/v1")
    return app
