"""Shared authenticated request dependencies; identity never comes from request bodies."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.integrations.supabase.wardrobe import SupabaseWardrobe


def repository(request: Request) -> SupabaseWardrobe:
    return SupabaseWardrobe(request.app.state.http, request.app.state.settings)


Repository = Annotated[SupabaseWardrobe, Depends(repository)]


async def owner(
    repo: Repository,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in required")
    user_id = await repo.user_id(authorization[7:])
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired")
    return user_id


Owner = Annotated[str, Depends(owner)]
