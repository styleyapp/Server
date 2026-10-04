"""Owned outfits and bounded generation contracts, independent of provider payloads."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.domains.wardrobe.schemas import GarmentMetadata, WardrobeItem

GENERATION_VERSION = "outfit-v1"

Slot = Literal["top", "bottom", "one_piece", "outerwear", "shoes", "accessory"]


class OutfitContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    occasion: str = Field(default="Everyday", min_length=1, max_length=80)
    mood: str = Field(default="Relaxed", min_length=1, max_length=80)
    notes: str = Field(default="", max_length=500)


class GenerateOutfit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    context: OutfitContext


class ProposedPiece(BaseModel):
    model_config = ConfigDict(extra="forbid")
    garment_id: UUID
    slot: Slot


class OutfitProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    complete: StrictBool
    title_en: str = Field(min_length=1, max_length=120)
    title_he: str = Field(min_length=1, max_length=120)
    reason_en: str = Field(min_length=1, max_length=500)
    reason_he: str = Field(min_length=1, max_length=500)
    pieces: list[ProposedPiece] = Field(max_length=6)

    @model_validator(mode="after")
    def valid_composition(self) -> "OutfitProposal":
        if not self.complete:
            return self
        slots = [p.slot for p in self.pieces]
        ids = [p.garment_id for p in self.pieces]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate garments")
        if any(slots.count(s) > (2 if s == "accessory" else 1) for s in set(slots)):
            raise ValueError("Duplicate outfit roles")
        separates = "top" in slots and "bottom" in slots and "one_piece" not in slots
        one_piece = "one_piece" in slots and "top" not in slots and "bottom" not in slots
        if not (separates or one_piece):
            raise ValueError("An outfit requires a top and bottom, or a one-piece garment")
        return self


class OutfitPiece(BaseModel):
    slot: Slot
    garment_id: UUID
    available: bool
    snapshot: GarmentMetadata
    item: WardrobeItem | None


class Outfit(BaseModel):
    id: UUID
    request_id: UUID
    context: OutfitContext
    title_en: str
    title_he: str
    reason_en: str
    reason_he: str
    created_at: datetime
    saved_at: datetime | None
    pieces: list[OutfitPiece] = Field(min_length=1, max_length=6)


class OutfitPage(BaseModel):
    items: list[Outfit]
    next_cursor: str | None = None


class OutfitFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
