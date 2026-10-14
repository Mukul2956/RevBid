"""Public bidder identity.

Bidders are anonymous to each other: everyone sees a short alias derived from
``(auction_id, user_id)``. It is stable within an auction (so you can follow a rival's bids)
but differs across auctions (so bidders can't be profiled across the platform).
"""

import hashlib
import uuid


def bidder_alias(auction_id: uuid.UUID, user_id: uuid.UUID) -> str:
    digest = hashlib.sha256(f"{auction_id}:{user_id}".encode()).hexdigest()
    return f"Bidder-{digest[:5].upper()}"
