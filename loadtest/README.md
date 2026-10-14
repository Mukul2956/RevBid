# Load test

`run.py` exercises the real API over HTTP **and** WebSocket and re-verifies correctness afterwards through the public
API. It is a plain asyncio script (no extra dependencies beyond the backend venv: `httpx` + `websockets`) because it
needs thousands of WebSocket spectators and post-run invariant checks, which a generic load tool doesn't give you.

## Scenarios

1. **Hot auction:** N bidders hammer *one* auction (the worst case for the per-auction row lock) while M WebSocket
   spectators watch it. Reports request/accept throughput, bid latency p50/p95/p99, event fan-out lag, and the outcome mix.
2. **Many auctions:** A auctions × B bidders in parallel, to show that auctions don't contend with each other.

After each run it re-reads the auction via the API and asserts: amounts strictly increasing with the minimum increment
respected, `current_price == max(bid)`, `bid_count == rows`, persisted amounts == accepted responses. Any 5xx or
transport error is reported.

## Running it

The login/bid rate limits are protections; relax them for the test (the test accounts are brand-new users):

```bash
# API (local or in Docker) with relaxed limits and demo mode off
RATE_LIMIT_LOGIN_PER_MIN=100000 RATE_LIMIT_REGISTER_PER_MIN=100000 RATE_LIMIT_BIDS_PER_10S=100000 \
WS_MAX_CONNECTIONS_PER_USER=1000 DEMO_MODE=false \
python -m uvicorn app.main:create_app --factory --port 8100

# test (from the repo root, using the backend venv)
backend/.venv/bin/python loadtest/run.py --base http://127.0.0.1:8100 \
    --bidders 200 --spectators 300 --seconds 30 --auctions 20 --per-auction 10
```

Results are printed and written to `loadtest/results/latest.json`. The numbers in
[ARCHITECTURE.md §11](../docs/ARCHITECTURE.md#11-load-test-results) were produced this way; treat them as indicative
for a developer laptop (the load generator and the system under test share the machine), not as a capacity claim.
