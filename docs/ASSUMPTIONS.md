# Assumptions and trade-offs

The brief allowed reasonable assumptions. These are the ones made, grouped by what they affect, with
the trade-off each one accepts and what would change it. Design rationale lives in
[ARCHITECTURE.md](ARCHITECTURE.md) (§12 has the decision log).

## Product assumptions

| Assumption | Consequence |
|---|---|
| **Market: used two-wheelers in India.** Prices in INR, kilometres, owner count, RTO registration state, inspection score. | Indian digit grouping (₹1,25,000); `en-IN` formatting; seed data of real Indian models. |
| **Admins are the only sellers.** Admin creates bikes and auctions; bidders only bid. | No seller accounts, payouts or seller-side bid visibility. Admins cannot bid (conflict of interest). |
| **One currency, integer paise.** | No FX, no floats; the UI converts at the edge. |
| **No payments.** A winner sees "the team will contact you to complete the purchase". | Out of scope; the integration point is the `ENDED/SOLD` transition plus the private `auction_won` event. |
| **No e-mail/SMS.** In-app notifications only (toasts and the My Bids page, delivered over WebSocket). | Outbid users who are offline find out on return. A notification service would consume the same events. |
| **Reserve price is hidden**; bidders see only "Reserve met / not met". If the reserve isn't met at close, the auction is `UNSOLD` even with bids. | Standard auction behaviour; the bids are retained as history. |
| **Minimum increment is flat per auction**, chosen by the admin (the form suggests ~1 % rounded). | No tiered increments. Easy to add by extending `min_next_bid` in one pure function. |
| **A bidder can't outbid themselves**, and bids can't be withdrawn. Amounts above ₹1 crore are rejected (fat-finger guard); bids ≥ 2× the current price ask for confirmation in the UI. | Prevents price-inflation and accidental bids. |
| **Anti-sniping is on by default**: a bid in the last 2 minutes extends the end to 2 minutes from the bid, capped at +30 minutes in total. Configurable per auction. | An auction can end later than its advertised time; the UI says so when it happens. |
| **Bidders are anonymous** (per-auction alias). | Admins likewise see aliases and the audit trail, not names, in the bid feed. |
| **One open auction per bike.** A bike can be re-listed after its auction ends or is cancelled. | Enforced by a partial unique index. |
| **English only**, desktop and mobile web. | No i18n. |
| **Images are URLs**, not uploads. Seed photos are CC-licensed Wikimedia Commons images (credits at `/credits`). | Admin pastes URLs; production would use presigned uploads to object storage + a CDN. |
| **Demo mode keeps ≥ 4 auctions open** so a reviewer always finds something live (`DEMO_MODE=true`). | Auto-creates auctions for bikes that have none; disabled in a real deployment. |

## Engineering assumptions and trade-offs

| Decision | Trade-off accepted | What would change it |
|---|---|---|
| **Modular monolith**, not microservices | One deploy and one transaction boundary; scaling is whole-app | Distinct scaling needs for realtime/workers |
| **Postgres row lock** serialises bids per auction | A single extremely hot auction has a throughput ceiling | `bid_lock_wait_seconds` / `BidLockContention` alert; then a per-auction sequencer |
| **Database is the only clock** | One extra trivial query per bid | Never; it's what makes multi-instance correct |
| **Realtime is at-most-once**; clients version-check and resync from REST | Not exactly-once; a client can briefly miss an event until the next one or a resync | A need for guaranteed delivery → transactional outbox / Redis Streams |
| **Redis pub/sub** (fire-and-forget) for fan-out | A Redis blip drops live updates (REST still works; clients resync on reconnect) | Larger scale → Streams, or shard channels |
| **Scheduler runs inside the API process** | Fits one free service; restarts catch up | `RUN_SCHEDULER=false` + a dedicated worker |
| **Correctness never depends on the scheduler being awake** | Announcements (close, winner notice) can lag when the free tier is asleep | Paid always-on instance |
| **JWT access tokens (15 min) + rotating refresh cookie** | Deactivating a user bites at the next refresh (≤ 15 min) | A revocation list in Redis if instant kill-switch is needed |
| **Rate limiting fails open** | During a Redis outage limits aren't enforced (metered + alerted) | Compliance requirements → fail closed |
| **Rate limits key on the client IP** as seen after the proxy | Behind several proxies the IP can be spoofable via `X-Forwarded-For` | Configure the exact trusted-proxy list for the host |
| **Keyset pagination** | Sorting by a mutable column (price) can shift items between pages | Acceptable for lists; bid history keys on the immutable amount |
| **No search engine**; `ILIKE` on make/model | Fine for thousands of bikes, not millions | `pg_trgm` index, then OpenSearch |
| **Hand-written API types** in the frontend | Can drift from the backend schemas | Generate from OpenAPI once the generator supports the toolchain's TypeScript version |
| **Free-tier hosting** | Cold starts (~30-60 s), small limits | Paid plan; a keep-alive workflow mitigates |
| **Free Render Postgres may expire** after a trial period | Needs re-provisioning or a permanent free DB (e.g. Neon) | Check Render's current free-tier terms at deploy time |
| **No Prometheus in production** on the free tier | Dashboards are provided and demonstrated locally; prod has `/metrics`, logs and the in-app System page | Grafana Cloud agent or a paid host |
| **E2E tests relax the login rate limit by config** | The limiter is tested separately; E2E logs in dozens of times from one IP | n/a |

## Known limitations (honest list)

- No password reset or e-mail verification (needs an e-mail provider).
- No bid retraction, proxy/auto-bidding, or watchlist (P2 items from the plan).
- Admin UI covers the core operations (bikes, auctions, monitoring), not user management.
- WebSocket events are not replayed after a long disconnect; the client re-fetches state instead.
- Dashboards and alert rules are verified for syntax and wiring, not tuned against production traffic.
