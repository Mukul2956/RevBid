"""Domain errors: a stable machine-readable ``code`` plus a human message and extra details.

These carry no HTTP knowledge; ``app.core.errors`` maps codes to status codes at the edge.
"""

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


# --- generic --------------------------------------------------------------------
class NotFound(DomainError):
    code = "NOT_FOUND"


class Forbidden(DomainError):
    code = "FORBIDDEN"


class Unauthenticated(DomainError):
    code = "UNAUTHENTICATED"


class Conflict(DomainError):
    code = "CONFLICT"


class RateLimited(DomainError):
    code = "RATE_LIMITED"


# --- auth -----------------------------------------------------------------------
class InvalidCredentials(DomainError):
    code = "INVALID_CREDENTIALS"


class EmailTaken(DomainError):
    code = "EMAIL_TAKEN"


class InvalidToken(DomainError):
    code = "INVALID_TOKEN"


# --- bidding --------------------------------------------------------------------
class AuctionNotLive(DomainError):
    code = "AUCTION_NOT_LIVE"


class AuctionEnded(DomainError):
    code = "AUCTION_ENDED"


class BidTooLow(DomainError):
    code = "BID_TOO_LOW"


class BidTooHigh(DomainError):
    code = "BID_TOO_HIGH"


class AlreadyHighestBidder(DomainError):
    code = "ALREADY_HIGHEST"


class AdminCannotBid(DomainError):
    code = "ADMIN_CANNOT_BID"


class IdempotencyConflict(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


# --- lifecycle / admin ----------------------------------------------------------
class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"


class AuctionNotEditable(DomainError):
    code = "AUCTION_NOT_EDITABLE"


class BikeAlreadyListed(DomainError):
    code = "BIKE_ALREADY_LISTED"


class InvalidSchedule(DomainError):
    code = "INVALID_SCHEDULE"
