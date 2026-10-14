"""Load test: a "hot auction" with many bidders and WebSocket spectators, then a many-auctions run.

What it proves (and prints):
  * bid throughput and latency percentiles under heavy contention on ONE auction (the worst case
    for the per-auction row lock), and under spread-out load across MANY auctions
  * realtime fan-out: how many events reach how many sockets, and the delivery lag
  * correctness AFTER the storm, re-checked through the public API: strictly increasing amounts,
    price == max bid, bid count == accepted responses, zero 5xx

Usage (API must run with relaxed rate limits, see loadtest/README.md):
    python loadtest/run.py --base http://127.0.0.1:8100 --bidders 200 --spectators 300 --seconds 30
    # several instances behind no load balancer: bidders and spectators are spread round-robin
    python loadtest/run.py --base http://127.0.0.1:8100,http://127.0.0.1:8101,http://127.0.0.1:8102

Run with the backend venv (needs httpx + websockets, both already installed there).
"""

import argparse
import asyncio
import contextlib
import json
import random
import statistics
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets

ADMIN = ("admin@bikeauction.dev", "Admin@12345")
PASSWORD = "LoadTest-123"


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def summarize(name: str, values_ms: list[float]) -> dict[str, float]:
    return {
        "n": len(values_ms),
        "p50_ms": round(pct(values_ms, 0.50), 1),
        "p95_ms": round(pct(values_ms, 0.95), 1),
        "p99_ms": round(pct(values_ms, 0.99), 1),
        "max_ms": round(max(values_ms), 1) if values_ms else float("nan"),
        "mean_ms": round(statistics.fmean(values_ms), 1) if values_ms else float("nan"),
    }


async def scrape_hist(client: httpx.AsyncClient, metric: str) -> dict[float, float]:
    """Cumulative bucket counts of a Prometheus histogram from /metrics (token-less local run)."""
    text = (await client.get("/metrics")).text
    out: dict[float, float] = {}
    for line in text.splitlines():
        if line.startswith(f"{metric}_bucket{{"):
            le = line.split('le="', 1)[1].split('"', 1)[0]
            out[float("inf") if le == "+Inf" else float(le)] = float(line.rsplit(" ", 1)[1])
    return out


async def scrape_all(clients: list[httpx.AsyncClient], metric: str) -> dict[float, float]:
    """Sum a histogram across every API instance (each exposes its own /metrics)."""
    total: dict[float, float] = {}
    for c in clients:
        for le, n in (await scrape_hist(c, metric)).items():
            total[le] = total.get(le, 0.0) + n
    return total


def hist_percentiles(before: dict[float, float], after: dict[float, float]) -> dict[str, float]:
    """Percentiles (as bucket upper bounds, i.e. conservative) of what happened between two scrapes."""
    delta = {le: after[le] - before.get(le, 0.0) for le in after}
    total = delta.get(float("inf"), 0.0)
    result: dict[str, float] = {"n": total}
    for label, q in (("p50_ms", 0.5), ("p95_ms", 0.95), ("p99_ms", 0.99)):
        bound = next((le for le in sorted(delta) if total and delta[le] >= q * total), float("inf"))
        result[label] = round(bound * 1000, 1) if bound != float("inf") else float("inf")
    return result


class Stats:
    def __init__(self) -> None:
        self.bid_latency_ms: list[float] = []
        self.status: Counter[str] = Counter()
        self.accepted: list[int] = []
        self.fanout_lag_ms: list[float] = []
        self.ws_messages = 0
        self.ws_connected = 0


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return str(r.json()["access_token"])


async def register_users(client: httpx.AsyncClient, n: int) -> list[str]:
    """Create n bidders concurrently (argon2 is slow on purpose, so bound the concurrency)."""
    run = uuid.uuid4().hex[:8]
    sem = asyncio.Semaphore(12)
    tokens: list[str] = []

    async def one(i: int) -> None:
        async with sem:
            body = {"email": f"load-{run}-{i}@example.com", "password": PASSWORD, "display_name": f"Load {i}"}
            r = await client.post("/api/v1/auth/register", json=body)
            r.raise_for_status()
            tokens.append(r.json()["access_token"])

    await asyncio.gather(*(one(i) for i in range(n)))
    return tokens


async def create_auction(client: httpx.AsyncClient, admin: str, minutes: int = 30) -> str:
    h = {"Authorization": f"Bearer {admin}"}
    bike = await client.post(
        "/api/v1/admin/bikes",
        headers=h,
        json={"make": "Load", "model": f"Bike-{uuid.uuid4().hex[:6]}", "year": 2021, "km_driven": 1000,
              "owner_number": 1, "condition": "GOOD", "description": "load test", "images": []},
    )
    bike.raise_for_status()
    now = datetime.fromisoformat((await client.get("/api/v1/time")).json()["server_time"])
    auction = await client.post(
        "/api/v1/admin/auctions",
        headers=h,
        json={"bike_id": bike.json()["id"], "start_at": now.isoformat(),
              "end_at": datetime.fromtimestamp(now.timestamp() + minutes * 60, UTC).isoformat(),
              "starting_price": 5_000_000, "min_increment": 50_000, "schedule": True},
    )
    auction.raise_for_status()
    auction_id = str(auction.json()["id"])
    for _ in range(60):  # wait for the scheduler to promote it
        if (await client.get(f"/api/v1/auctions/{auction_id}")).json()["status"] == "LIVE":
            return auction_id
        await asyncio.sleep(0.5)
    raise RuntimeError("auction never went LIVE")


async def bidder(client: httpx.AsyncClient, token: str, auction_id: str, deadline: float, stats: Stats) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    floor = 5_000_000
    while time.monotonic() < deadline:
        amount = floor + random.randint(0, 3) * 50_000
        started = time.perf_counter()
        try:
            r = await client.post(f"/api/v1/auctions/{auction_id}/bids", json={"amount": amount}, headers=headers)
        except httpx.HTTPError:
            stats.status["transport_error"] += 1
            continue
        stats.bid_latency_ms.append((time.perf_counter() - started) * 1000)
        if r.status_code == 201:
            stats.status["201 accepted"] += 1
            stats.accepted.append(amount)
            floor = r.json()["auction"]["min_next_bid"]
        else:
            body = r.json() if r.headers.get("content-type", "").startswith("application/") else {}
            stats.status[f"{r.status_code} {body.get('code', '?')}"] += 1
            floor = body.get("min_next_bid", floor + 50_000)
        await asyncio.sleep(random.uniform(0.02, 0.15))  # human-ish think time, still very aggressive


async def spectator(ws_url: str, origin: str, room: str, deadline: float, stats: Stats) -> None:
    try:
        async with websockets.connect(ws_url, origin=origin, max_size=65536) as ws:
            await ws.send(json.dumps({"type": "subscribe", "room": room}))
            stats.ws_connected += 1
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "bid_placed":
                    stats.ws_messages += 1
                    sent = datetime.fromisoformat(msg["server_time"]).timestamp()
                    stats.fanout_lag_ms.append(max(0.0, (time.time() - sent) * 1000))
    except Exception:  # noqa: BLE001 - a dropped spectator is data, not a crash
        stats.status["ws_error"] += 1


async def verify(client: httpx.AsyncClient, auction_id: str, accepted: list[int]) -> dict[str, object]:
    """Re-read the auction through the public API and check the invariants that must always hold."""
    auction = (await client.get(f"/api/v1/auctions/{auction_id}")).json()
    amounts: list[int] = []
    cursor = None
    while True:
        page = (await client.get(f"/api/v1/auctions/{auction_id}/bids", params={"limit": 100, "cursor": cursor})).json()
        amounts += [b["amount"] for b in page["items"]]  # newest first
        cursor = page["next_cursor"]
        if not cursor:
            break
    chronological = amounts[::-1]
    checks = {
        "strictly_increasing": all(a < b for a, b in zip(chronological, chronological[1:], strict=False)),
        "min_increment_respected": all(b - a >= 50_000 for a, b in zip(chronological, chronological[1:], strict=False)),
        "price_equals_max_bid": auction["current_price"] == (max(amounts) if amounts else 5_000_000),
        "bid_count_matches_rows": auction["bid_count"] == len(amounts),
        "rows_equal_accepted_responses": sorted(amounts) == sorted(accepted),
    }
    return {"ok": all(checks.values()), "checks": checks, "bid_rows": len(amounts), "final_price_inr": auction["current_price"] / 100}


async def hot_auction(bases: list[str], origin: str, bidders: int, spectators: int, seconds: int) -> dict[str, object]:
    """All bidders target ONE auction, spread round-robin over every API instance in ``bases``."""
    limits = httpx.Limits(max_connections=bidders + 50, max_keepalive_connections=bidders + 50)
    async with contextlib.AsyncExitStack() as stack:
        clients = [
            await stack.enter_async_context(httpx.AsyncClient(base_url=b, timeout=30, limits=limits)) for b in bases
        ]
        client = clients[0]
        admin = await login(client, *ADMIN)
        print(f"  registering {bidders} bidders ...")
        tokens = await register_users(client, bidders)
        auction_id = await create_auction(client, admin)
        stats = Stats()
        metrics = ("bid_duration_seconds", "bid_lock_wait_seconds")
        before = {m: await scrape_all(clients, m) for m in metrics}
        deadline = time.monotonic() + seconds
        print(f"  auction {auction_id}: {bidders} bidders + {spectators} spectators for {seconds}s ...")
        started = time.monotonic()
        ws_urls = [b.replace("http", "ws", 1) + "/ws" for b in bases]
        spectator_tasks = [
            asyncio.create_task(spectator(ws_urls[i % len(ws_urls)], origin, f"auction:{auction_id}", deadline + 2, stats))
            for i in range(spectators)
        ]
        await asyncio.sleep(2)  # let sockets attach before the storm
        deadline = time.monotonic() + seconds
        await asyncio.gather(
            *(bidder(clients[i % len(clients)], t, auction_id, deadline, stats) for i, t in enumerate(tokens))
        )
        elapsed = time.monotonic() - started - 2
        await asyncio.gather(*spectator_tasks, return_exceptions=True)
        result = await verify(client, auction_id, stats.accepted)
        after = {m: await scrape_all(clients, m) for m in before}
        server_side = {m: hist_percentiles(before[m], after[m]) for m in before}
        server_errors = sum(v for k, v in stats.status.items() if k.startswith("5") or k == "transport_error")
        return {
            "scenario": "hot auction (all bidders on ONE auction)",
            "api_instances": len(bases),
            "bidders": bidders, "spectators_connected": stats.ws_connected, "seconds": round(elapsed, 1),
            "requests": len(stats.bid_latency_ms),
            "requests_per_s": round(len(stats.bid_latency_ms) / elapsed, 1),
            "accepted_bids": len(stats.accepted),
            "accepted_per_s": round(len(stats.accepted) / elapsed, 1),
            "outcomes": dict(stats.status),
            "bid_latency_client_side": summarize("bid", stats.bid_latency_ms),
            "bid_latency_server_side": server_side["bid_duration_seconds"],
            "bid_lock_wait_server_side": server_side["bid_lock_wait_seconds"],
            "ws_bid_events_delivered": stats.ws_messages,
            "ws_events_per_s": round(stats.ws_messages / elapsed, 1),
            "ws_fanout_lag": summarize("lag", stats.fanout_lag_ms),
            "server_errors_5xx_or_transport": server_errors,
            "correctness_after_storm": result,
        }


async def many_auctions(base: str, auctions: int, per_auction: int, seconds: int) -> dict[str, object]:
    limits = httpx.Limits(max_connections=auctions * per_auction + 50, max_keepalive_connections=auctions * per_auction + 50)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as client:
        admin = await login(client, *ADMIN)
        tokens = await register_users(client, auctions * per_auction)
        ids = [await create_auction(client, admin) for _ in range(auctions)]
        per_stats = [Stats() for _ in ids]
        before = await scrape_hist(client, "bid_duration_seconds")
        deadline = time.monotonic() + seconds
        started = time.monotonic()
        await asyncio.gather(*(
            bidder(client, tokens[i * per_auction + j], ids[i], deadline, per_stats[i])
            for i in range(auctions) for j in range(per_auction)
        ))
        elapsed = time.monotonic() - started
        server_side = hist_percentiles(before, await scrape_hist(client, "bid_duration_seconds"))
        verdicts = [await verify(client, ids[i], per_stats[i].accepted) for i in range(auctions)]
        latencies = [x for s in per_stats for x in s.bid_latency_ms]
        accepted = sum(len(s.accepted) for s in per_stats)
        return {
            "scenario": f"{auctions} auctions x {per_auction} bidders (spread load)",
            "seconds": round(elapsed, 1), "requests": len(latencies),
            "requests_per_s": round(len(latencies) / elapsed, 1),
            "accepted_bids": accepted, "accepted_per_s": round(accepted / elapsed, 1),
            "bid_latency_client_side": summarize("bid", latencies),
            "bid_latency_server_side": server_side,
            "all_auctions_consistent": all(v["ok"] for v in verdicts),
        }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100", help="API base URL(s), comma-separated to spread load over several instances")
    ap.add_argument("--origin", default="http://localhost:5173")
    ap.add_argument("--bidders", type=int, default=200)
    ap.add_argument("--spectators", type=int, default=300)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--auctions", type=int, default=20)
    ap.add_argument("--per-auction", type=int, default=10)
    ap.add_argument("--out", default="loadtest/results/latest.json")
    args = ap.parse_args()
    bases = [b.strip() for b in args.base.split(",") if b.strip()]

    print(f"== scenario 1: hot auction ({len(bases)} API instance(s))")
    hot = await hot_auction(bases, args.origin, args.bidders, args.spectators, args.seconds)
    print("== scenario 2: many auctions")
    spread = await many_auctions(bases[0], args.auctions, args.per_auction, max(10, args.seconds // 2))
    report = {"when": datetime.now(UTC).isoformat(), "hot": hot, "spread": spread}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
