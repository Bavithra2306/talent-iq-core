from fastapi import APIRouter, Depends, status

from src.api.rest.dependencies import CurrentUser, Role, require_roles
from src.api.rest.schemas.sourcing_schema import SourceInfo, SourcingRunAccepted, SourcingRunRequest
from src.core.services import sourcing_service

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
    parse -> extract -> embed pipeline. Poll /resumes/batches/{batch_id}."""
    return SourcingRunAccepted(batch_id=await sourcing_service.start(user, body))
