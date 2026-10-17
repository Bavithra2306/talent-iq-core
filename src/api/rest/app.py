import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.middleware.cors import register_cors_middleware
from src.api.rest.routes.health import HealthRouter
from src.api.rest.routes.job_route import JobRouter
from src.api.rest.routes.me import MeRouter
from src.api.rest.routes.resume_route import ResumeRouter
from src.api.rest.routes.shortlist_route import ShortlistRouter
from src.config.settings import settings
from src.core.services.document_parser import parser


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the Docling worker processes now so the first upload is fast.
    warm_up = asyncio.create_task(parser.warm_up())
    yield
    warm_up.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    register_cors_middleware(app)
    app.include_router(HealthRouter, prefix="/api/v1")
    app.include_router(MeRouter, prefix="/api/v1")
    app.include_router(JobRouter, prefix="/api/v1")
    app.include_router(ResumeRouter, prefix="/api/v1")
    app.include_router(ShortlistRouter, prefix="/api/v1")
    return app
