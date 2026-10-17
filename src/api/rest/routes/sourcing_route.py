from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth_dependency import CurrentUser, Role, require_roles
from src.core.services import candidate_service, sourcing_service
from src.data.clients.postgres import get_db
from src.schema.ingestion_schema import BatchStatus
from src.schema.sourcing_schema import SourceInfo, SourcingRunAccepted, SourcingRunRequest

SourcingRouter = APIRouter(prefix="/sourcing", tags=["Sourcing"])

# Only recruiters source resumes.
recruiter_only = require_roles(Role.RECRUITER)


@SourcingRouter.get("/sources", response_model=list[SourceInfo])
async def sources(_: CurrentUser = Depends(recruiter_only)):
    """The sources to offer in the UI, with limits, terms and an example request for each."""
    return sourcing_service.catalog()


@SourcingRouter.post("/runs", response_model=SourcingRunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: SourcingRunRequest, user: CurrentUser = Depends(recruiter_only)):
    """Collect resumes from the chosen sources (and pasted links); they go through the normal
    parse -> extract -> embed pipeline. Poll /sourcing/runs/{batch_id}."""
    return SourcingRunAccepted(batch_id=await sourcing_service.start(user, body))


@SourcingRouter.get("/runs/{batch_id}", response_model=BatchStatus)
async def run_status(batch_id: str, db: AsyncSession = Depends(get_db),
                     _: CurrentUser = Depends(recruiter_only)):
    """Created -> Collecting (one event per source) -> Reading -> done, with counts and failures."""
    return await candidate_service.get_batch_status(db, batch_id)
