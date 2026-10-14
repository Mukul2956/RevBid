import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class Bike(Base):
    __tablename__ = "bikes"
    __table_args__ = (
        CheckConstraint("year BETWEEN 1980 AND 2100", name="year_range"),
        CheckConstraint("km_driven >= 0", name="km_nonneg"),
        CheckConstraint("owner_number >= 1", name="owner_positive"),
        CheckConstraint(
            "inspection_score IS NULL OR inspection_score BETWEEN 0 AND 100", name="score_range"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    make: Mapped[str] = mapped_column(String(60), index=True)
    model: Mapped[str] = mapped_column(String(80))
    variant: Mapped[str | None] = mapped_column(String(80), default=None)
    year: Mapped[int] = mapped_column(Integer)
    km_driven: Mapped[int] = mapped_column(Integer)
    owner_number: Mapped[int] = mapped_column(Integer, default=1)
    engine_cc: Mapped[int | None] = mapped_column(Integer, default=None)
    color: Mapped[str | None] = mapped_column(String(40), default=None)
    registration_state: Mapped[str | None] = mapped_column(String(40), default=None)
    condition: Mapped[str] = mapped_column(String(20), default="GOOD")
    inspection_score: Mapped[int | None] = mapped_column(Integer, default=None)
    description: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
