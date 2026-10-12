from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.integrations.supabase.lifecycle import SupabaseLifecycle

router = APIRouter(prefix="/v1/me", tags=["account"])


def lifecycle(request: Request):
    return SupabaseLifecycle(request.app.state.http, request.app.state.settings)


class DeleteAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["DELETE"]


@router.delete("/account", status_code=202)
async def delete_account(
    body: DeleteAccount,
    store: Annotated[SupabaseLifecycle, Depends(lifecycle)],
    authorization: Annotated[str | None, Header()] = None,
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail="Sign in required")
    # Accepted requests remain retryable while the deletion outbox blocks regular APIs.
    user = await store.authenticated_user(authorization[7:])
    if not user:
        raise HTTPException(401, detail="Session expired")
    await store.enqueue_deletion(user)
    return {"status": "deletion_requested"}
