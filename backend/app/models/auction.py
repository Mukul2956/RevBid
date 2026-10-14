import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow
from app.models.bike import Bike


class Auction(Base):
    """An auction for one bike.

    ``current_price``, ``leader_id``, ``bid_count`` and ``version`` are denormalised from
    ``bids`` and are only ever written while holding this row's lock (see bid_service), so
    listing pages never need an aggregate over the bids table.

    All money values are integer paise.
    """

    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','SCHEDULED','LIVE','ENDED','CANCELLED')", name="status_valid"
        ),
        CheckConstraint("outcome IS NULL OR outcome IN ('SOLD','UNSOLD')", name="outcome_valid"),
        CheckConstraint("end_at > start_at", name="window_valid"),
        CheckConstraint("starting_price > 0", name="start_price_positive"),
        CheckConstraint("min_increment > 0", name="increment_positive"),
        CheckConstraint("current_price >= starting_price", name="price_not_below_start"),
        CheckConstraint(
            "reserve_price IS NULL OR reserve_price >= starting_price", name="reserve_valid"
        ),
        # A bike can be re-listed later, but never in two open auctions at once.
        Index(
            "uq_auctions_open_bike",
            "bike_id",
            unique=True,
            postgresql_where=text("status IN ('DRAFT','SCHEDULED','LIVE')"),
        ),
        Index("ix_auctions_status_end_at", "status", "end_at"),
        Index("ix_auctions_status_start_at", "status", "start_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bike_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("bikes.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")
    outcome: Mapped[str | None] = mapped_column(String(8), default=None)
    cancel_reason: Mapped[str | None] = mapped_column(String(255), default=None)

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    original_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    starting_price: Mapped[int] = mapped_column(BigInteger)
    reserve_price: Mapped[int | None] = mapped_column(BigInteger, default=None)
    min_increment: Mapped[int] = mapped_column(BigInteger)
    snipe_window_s: Mapped[int] = mapped_column(Integer, default=120)
    snipe_extension_s: Mapped[int] = mapped_column(Integer, default=120)
    max_extension_s: Mapped[int] = mapped_column(Integer, default=1800)

    current_price: Mapped[int] = mapped_column(BigInteger)
    leader_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    bid_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )

    # INNER join: bike_id is NOT NULL, and Postgres refuses FOR UPDATE on the nullable side of
    # an outer join, which the bid/lifecycle paths rely on.
    bike: Mapped[Bike] = relationship(lazy="joined", innerjoin=True)


class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        # Defence in depth behind the row lock: two bids for the same amount can never coexist.
        UniqueConstraint("auction_id", "amount", name="uq_bids_auction_amount"),
        # Idempotency: a retried request (same key) can never create a second bid.
        UniqueConstraint("bidder_id", "idempotency_key", name="uq_bids_bidder_idem"),
        Index("ix_bids_bidder_created", "bidder_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auctions.id", ondelete="CASCADE")
    )
    bidder_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    amount: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class AuctionEvent(Base):
    """Append-only audit trail of everything that happens to an auction."""

    __tablename__ = "auction_events"
    __table_args__ = (Index("ix_auction_events_auction_id_id", "auction_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auctions.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
