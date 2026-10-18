from fastapi import APIRouter, Depends

from src.api.rest.dependencies import CurrentUser, Role, get_current_user, require_roles
from src.core.services import resume_service
from src.handlers.http_clients import embedding_client
from src.observability.metrics import metrics

SystemRouter = APIRouter(prefix="/system", tags=["System"])


@SystemRouter.get(
    "/embedding-quota",
    summary="Embedding quota",
    description="Today's embedding usage against the free daily quota, and resumes still waiting.",
)
async def embedding_quota(_: CurrentUser = Depends(get_current_user)):
    """Today's embedding usage against the free daily quota, and how many resumes still wait."""
    return {
        **await embedding_client.quota_status(),
        "resumes_waiting": await resume_service.count_missing_embeddings(),
    }


@SystemRouter.get(
    "/metrics",
    summary="Request metrics",
    description="Request counts, error counts and latency per route since the service started.",
)
async def request_metrics(_: CurrentUser = Depends(require_roles(Role.ADMIN))):
    """Per-route request statistics (admins only)."""
    return metrics.snapshot()
