from datetime import datetime
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Keyset-paginated list. Pass ``next_cursor`` back as ``?cursor=`` for the next page."""

    items: list[T]
    next_cursor: str | None = None


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone offset (e.g. 2030-01-01T10:00:00Z)")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)
