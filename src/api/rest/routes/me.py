from fastapi import APIRouter, Depends

from src.api.dependencies.auth_dependency import CurrentUser, get_current_user

MeRouter = APIRouter(tags=["Session"])


@MeRouter.get("/me")
def whoami(user: CurrentUser = Depends(get_current_user)):
    """Who the core service thinks is calling (id and role from the auth cookie)."""
    return {"id": user.id, "role": user.role}
