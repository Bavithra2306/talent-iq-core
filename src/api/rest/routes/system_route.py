from fastapi import APIRouter, Depends

from src.api.dependencies.auth_dependency import CurrentUser, get_current_user
from src.core.services import embedding_service, resume_service

SystemRouter = APIRouter(prefix="/system", tags=["System"])


@SystemRouter.get("/embedding-quota")
async def embedding_quota(_: CurrentUser = Depends(get_current_user)):
    """Today's embedding usage against the free daily quota, and how many resumes still wait."""
    return {
        **await embedding_service.quota_status(),
        "resumes_waiting": await resume_service.count_missing_embeddings(),
    }
