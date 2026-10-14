import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel

Condition = Literal["EXCELLENT", "GOOD", "FAIR"]


def _validate_images(urls: list[str]) -> list[str]:
    for u in urls:
        # http(s) URLs or app-relative asset paths only: no javascript:/data: URLs in an <img src>.
        if not (u.startswith(("https://", "http://")) or u.startswith("/")):
            raise ValueError("Image must be an http(s) URL or an app-relative path")
        if len(u) > 500:
            raise ValueError("Image URL too long")
    return urls


class BikeIn(ApiModel):
    make: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=80)
    variant: str | None = Field(default=None, max_length=80)
    year: int = Field(ge=1980, le=2100)
    km_driven: int = Field(ge=0, le=1_000_000)
    owner_number: int = Field(default=1, ge=1, le=10)
    engine_cc: int | None = Field(default=None, ge=20, le=3000)
    color: str | None = Field(default=None, max_length=40)
    registration_state: str | None = Field(default=None, max_length=40)
    condition: Condition = "GOOD"
    inspection_score: int | None = Field(default=None, ge=0, le=100)
    description: str = Field(default="", max_length=5000)
    images: list[str] = Field(default_factory=list, max_length=8)

    _images = field_validator("images")(_validate_images)


class BikeUpdate(ApiModel):
    make: str | None = Field(default=None, min_length=1, max_length=60)
    model: str | None = Field(default=None, min_length=1, max_length=80)
    variant: str | None = Field(default=None, max_length=80)
    year: int | None = Field(default=None, ge=1980, le=2100)
    km_driven: int | None = Field(default=None, ge=0, le=1_000_000)
    owner_number: int | None = Field(default=None, ge=1, le=10)
    engine_cc: int | None = Field(default=None, ge=20, le=3000)
    color: str | None = Field(default=None, max_length=40)
    registration_state: str | None = Field(default=None, max_length=40)
    condition: Condition | None = None
    inspection_score: int | None = Field(default=None, ge=0, le=100)
    description: str | None = Field(default=None, max_length=5000)
    images: list[str] | None = Field(default=None, max_length=8)

    @field_validator("images")
    @classmethod
    def _imgs(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _validate_images(v)


class BikeOut(ApiModel):
    id: uuid.UUID
    make: str
    model: str
    variant: str | None
    year: int
    km_driven: int
    owner_number: int
    engine_cc: int | None
    color: str | None
    registration_state: str | None
    condition: str
    inspection_score: int | None
    description: str
    images: list[str]
    created_at: datetime
