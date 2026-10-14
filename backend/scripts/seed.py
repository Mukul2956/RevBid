"""Seed demo data: users, a catalogue of used bikes, and a few open auctions.

Idempotent: safe to run on every deploy. Run from ``backend/``::

    python -m scripts.seed

Accounts (override passwords with SEED_ADMIN_PASSWORD / SEED_BUYER_PASSWORD):
    admin@bikeauction.dev   role ADMIN
    buyer1@bikeauction.dev  role BIDDER
    buyer2@bikeauction.dev  role BIDDER
"""

import asyncio
import json
import os
from pathlib import Path

import structlog
from sqlalchemy import select

from app.core.clock import DbClock
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.db.session import create_engine, create_sessionmaker
from app.models.bike import Bike
from app.models.user import User
from app.realtime.publisher import RecordingPublisher
from app.workers.demo import ensure_demo_auctions

log = structlog.stdlib.get_logger("seed")

# Openly licensed photos from Wikimedia Commons (credits are shown on the site's /credits page).
PHOTOS: dict[str, dict[str, str]] = json.loads(
    (Path(__file__).parent / "bike_images.json").read_text(encoding="utf-8")
)

ADMIN_EMAIL = "admin@bikeauction.dev"
BUYERS = [("buyer1@bikeauction.dev", "Aarav Sharma"), ("buyer2@bikeauction.dev", "Diya Mehta")]

# make, model, variant, year, km, owner, cc, colour, RTO state, condition, inspection, notes
BIKES: list[tuple[str, str, str | None, int, int, int, int, str, str, str, int, str]] = [
    (
        "Royal Enfield",
        "Classic 350",
        "Signals",
        2021,
        18400,
        1,
        349,
        "Airborne Blue",
        "Karnataka",
        "EXCELLENT",
        88,
        "Single owner, full service history at authorised RE centre. "
        "New tyres fitted at 16,000 km.",
    ),
    (
        "Royal Enfield",
        "Himalayan",
        "BS6",
        2019,
        32100,
        2,
        411,
        "Granite Black",
        "Maharashtra",
        "GOOD",
        79,
        "Touring-ready with crash guard and pannier mounts. Minor scratches on the tank.",
    ),
    (
        "Bajaj",
        "Pulsar NS200",
        None,
        2020,
        24500,
        1,
        199,
        "Pearl White",
        "Delhi",
        "GOOD",
        82,
        "Well-kept commuter-sport. Chain set replaced recently, insurance valid.",
    ),
    (
        "KTM",
        "Duke 390",
        "Gen 2",
        2018,
        28900,
        2,
        373,
        "Orange",
        "Karnataka",
        "GOOD",
        76,
        "Slipper clutch, LED headlamp. Regular dealer servicing, no accidental history.",
    ),
    (
        "Yamaha",
        "R15",
        "V3",
        2019,
        21000,
        1,
        155,
        "Racing Blue",
        "Tamil Nadu",
        "EXCELLENT",
        86,
        "Garage kept, never raced. Original paint, all panels intact.",
    ),
    (
        "TVS",
        "Apache RTR 160 4V",
        None,
        2021,
        15800,
        1,
        160,
        "Matte Black",
        "Maharashtra",
        "EXCELLENT",
        90,
        "Race-tuned fuel injection and glide-through-traffic torque. Showroom-fresh condition.",
    ),
    (
        "Honda",
        "CB350 H'ness",
        "DLX Pro",
        2022,
        9600,
        1,
        348,
        "Pearl Spartan Red",
        "Delhi",
        "EXCELLENT",
        93,
        "Barely used, Honda Smartphone Voice Control, dual-channel ABS. Warranty remaining.",
    ),
    (
        "Hero",
        "Splendor Plus",
        None,
        2020,
        38000,
        2,
        97,
        "Black with Grey",
        "Uttar Pradesh",
        "FAIR",
        68,
        "Dependable daily rider. Mileage-tested at 60+ kmpl, some cosmetic wear.",
    ),
    (
        "Suzuki",
        "Gixxer SF",
        None,
        2019,
        26300,
        1,
        155,
        "Metallic Triton Blue",
        "Karnataka",
        "GOOD",
        80,
        "Fully faired sport commuter. Fresh battery, brake pads replaced last month.",
    ),
    (
        "Kawasaki",
        "Ninja 300",
        None,
        2017,
        19700,
        2,
        296,
        "Lime Green",
        "Maharashtra",
        "GOOD",
        77,
        "Iconic green, twin-cylinder smoothness. Slight fork seepage noted in inspection.",
    ),
    (
        "Royal Enfield",
        "Interceptor 650",
        None,
        2020,
        14200,
        1,
        648,
        "Orange Crush",
        "Gujarat",
        "EXCELLENT",
        91,
        "Parallel-twin with a glorious exhaust note. Stock condition, no modifications.",
    ),
    (
        "Bajaj",
        "Dominar 400",
        None,
        2019,
        30500,
        2,
        373,
        "Aurora Green",
        "Rajasthan",
        "GOOD",
        74,
        "Highway tourer with dual-channel ABS. Service done at 30,000 km, tyres at 60%.",
    ),
    (
        "Honda",
        "Activa 6G",
        "DLX",
        2021,
        12400,
        1,
        109,
        "Pearl Precious White",
        "Karnataka",
        "EXCELLENT",
        87,
        "Silent-start scooter, ideal city runabout. Single owner with all invoices.",
    ),
    (
        "Triumph",
        "Speed 400",
        None,
        2024,
        4200,
        1,
        398,
        "Carnival Red",
        "Maharashtra",
        "EXCELLENT",
        95,
        "Nearly new, under factory warranty. Accessory tank pad and bar-end mirrors included.",
    ),
    (
        "Harley-Davidson",
        "Street 750",
        None,
        2018,
        22300,
        1,
        749,
        "Vivid Black",
        "Delhi",
        "GOOD",
        81,
        "Liquid-cooled Revolution X engine, serviced at authorised dealer, stock exhaust.",
    ),
    (
        "Yamaha",
        "FZ-S",
        "V3",
        2020,
        27400,
        1,
        149,
        "Dark Matte Blue",
        "Telangana",
        "GOOD",
        78,
        "Reliable naked street bike. Recently serviced, new rear tyre.",
    ),
]


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    admin_pw = os.environ.get("SEED_ADMIN_PASSWORD", "Admin@12345")
    buyer_pw = os.environ.get("SEED_BUYER_PASSWORD", "Buyer@12345")

    async with sessionmaker() as session, session.begin():
        existing = set((await session.execute(select(User.email))).scalars())
        if ADMIN_EMAIL not in existing:
            session.add(
                User(
                    email=ADMIN_EMAIL,
                    password_hash=hash_password(admin_pw),
                    display_name="Auction Admin",
                    role="ADMIN",
                )
            )
        for email, name in BUYERS:
            if email not in existing:
                session.add(
                    User(
                        email=email,
                        password_hash=hash_password(buyer_pw),
                        display_name=name,
                        role="BIDDER",
                    )
                )

        existing_bikes = {
            (b.make, b.model, b.year): b for b in (await session.execute(select(Bike))).scalars()
        }
        added = 0
        for make, model, variant, year, km, owner, cc, colour, state, cond, score, notes in BIKES:
            photo = PHOTOS.get(f"{make}|{model}")
            images = [photo["url"]] if photo else []
            found = existing_bikes.get((make, model, year))
            if found is not None:
                if not found.images and images:
                    found.images = images  # backfill bikes seeded before photos existed
                continue
            session.add(
                Bike(
                    make=make,
                    model=model,
                    variant=variant,
                    year=year,
                    km_driven=km,
                    owner_number=owner,
                    engine_cc=cc,
                    color=colour,
                    registration_state=state,
                    condition=cond,
                    inspection_score=score,
                    description=notes,
                    images=images,
                )
            )
            added += 1
    log.info("seeded", new_bikes=added)

    created = await ensure_demo_auctions(sessionmaker, DbClock(), RecordingPublisher(), settings)
    log.info("seeded_auctions", created=created)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
