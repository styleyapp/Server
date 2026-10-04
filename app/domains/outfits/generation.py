"""Coordinates bounded generation; database leases prevent duplicate paid attempts."""

import asyncio
import hashlib
import json
import re

import httpx
from google.auth.exceptions import GoogleAuthError
from pydantic import ValidationError

from app.domains.outfits.schemas import GenerateOutfit, OutfitFailure, OutfitProposal


async def generate(user: str, request: GenerateOutfit, store, preferences, proposer):
    digest = hashlib.sha256(
        json.dumps(request.context.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    claim = await store.claim(user, request, digest)
    if claim["status"] == "ready":
        return await store.get(user, claim["id"])
    if claim["status"] != "claimed":
        raise OutfitFailure(claim["status"])
    outfit_id, lease = claim["id"], claim["lease_token"]
    try:
        async with asyncio.timeout(35):
            wardrobe, profile = await asyncio.gather(store.candidates(user), preferences.get(user))
            if not wardrobe:
                raise OutfitFailure("insufficient_wardrobe")
            proposal = OutfitProposal.model_validate(
                await proposer.propose_outfit(
                    wardrobe,
                    request.context.model_dump(),
                    {
                        "top_fit": profile.preferences.top_fit,
                        "pants_fit": profile.preferences.pants_fit,
                    },
                )
            )
            if not proposal.complete:
                raise OutfitFailure("insufficient_wardrobe")
            allowed = {row["id"]: row for row in wardrobe}
            if any(str(piece.garment_id) not in allowed for piece in proposal.pieces):
                raise OutfitFailure("invalid_proposal")
            for piece in proposal.pieces:
                supported = supported_slots(allowed[str(piece.garment_id)])
                if supported and piece.slot not in supported:
                    raise OutfitFailure("invalid_proposal")
        status = await store.complete(user, outfit_id, lease, proposal)
        if status != "ready":
            raise OutfitFailure(status)
    except (
        OutfitFailure,
        ValidationError,
        httpx.HTTPError,
        GoogleAuthError,
        TimeoutError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
    ) as error:
        code = error.code if isinstance(error, OutfitFailure) else "provider_unavailable"
        try:
            await store.fail(user, outfit_id, lease, code)
        except (httpx.HTTPError, TimeoutError):
            # A failed cleanup leaves a bounded lease, never an unprotected paid retry.
            pass
        raise OutfitFailure(code) from None
    return await store.get(user, outfit_id)


def supported_slots(row: dict) -> set[str]:
    """Enforce known metadata roles; leave unknown garment types to model interpretation."""
    words = set(re.findall(r"[a-z]+", f"{row.get('category', '')} {row.get('type', '')}".lower()))
    vocabulary = {
        "top": {"top", "tops", "shirt", "shirts", "blouse", "sweater", "hoodie", "tee", "polo"},
        "bottom": {
            "bottom",
            "bottoms",
            "pants",
            "trousers",
            "jeans",
            "shorts",
            "skirt",
            "leggings",
        },
        "one_piece": {"dress", "dresses", "jumpsuit", "romper", "gown"},
        "outerwear": {"outerwear", "coat", "jacket", "blazer", "overshirt", "cardigan"},
        "shoes": {"shoes", "footwear", "sneakers", "boots", "sandals", "loafers"},
        "accessory": {"accessory", "accessories", "hat", "belt", "scarf", "bag"},
    }
    return {slot for slot, names in vocabulary.items() if words & names}
