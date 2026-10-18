import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.middleware.cors import register_cors_middleware
from src.api.middleware.error_handler import register_exception_handlers
from src.api.middleware.logging import register_logging_middleware
from src.api.middleware.metrics import register_metrics_middleware
from src.api.middleware.rate_limiting import register_rate_limit_middleware
from src.api.rest.routes.health import HealthRouter
from src.api.rest.routes.job_route import JobRouter
from src.api.rest.routes.resume_route import ResumeRouter
from src.api.rest.routes.review_route import ReviewRouter
from src.api.rest.routes.shortlist_route import ShortlistRouter
from src.api.rest.routes.sourcing_route import SourcingRouter
from src.api.rest.routes.system_route import SystemRouter
from src.config.settings import settings
from src.core.services.document_parser import parser
from src.core.services.resume_service import backfill_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the Docling worker processes now so the first upload is fast.
    warm_up = asyncio.create_task(parser.warm_up())
    # Fill in embeddings that were paused (daily quota) or failed earlier.
    backfill = asyncio.create_task(backfill_loop())
    yield
    warm_up.cancel()
    backfill.cancel()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="Resume sourcing, reading, matching and shortlisting.",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Middleware added last runs first: request logging (sets the request id)
    # wraps metrics, which wraps the rate limiter, then CORS.
    register_cors_middleware(app)
    register_rate_limit_middleware(app)
    register_metrics_middleware(app)
    register_logging_middleware(app)
    register_exception_handlers(app)
    app.include_router(HealthRouter, prefix="/api/v1")
    app.include_router(JobRouter, prefix="/api/v1")
    app.include_router(ResumeRouter, prefix="/api/v1")
    app.include_router(ShortlistRouter, prefix="/api/v1")
    app.include_router(ReviewRouter, prefix="/api/v1")
    app.include_router(SystemRouter, prefix="/api/v1")
    app.include_router(SourcingRouter, prefix="/api/v1")
    return app
