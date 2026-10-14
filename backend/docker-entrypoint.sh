#!/bin/sh
# Container entrypoint: migrate, optionally seed, then serve.
#
#   RUN_MIGRATIONS=true   apply Alembic migrations before starting (default true)
#   RUN_SEED=true         create demo users/bikes/auctions (idempotent; default false)
#
# `exec` hands PID 1 to uvicorn so SIGTERM from the platform triggers a graceful shutdown.
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] applying migrations"
  python -m alembic upgrade head
fi

if [ "${RUN_SEED:-false}" = "true" ]; then
  echo "[entrypoint] seeding demo data"
  python -m scripts.seed
fi

# --proxy-headers/--forwarded-allow-ips: trust the platform's load balancer for client IPs
#   (rate limiting and logs would otherwise see only the proxy). Defaults to '*' because managed
#   hosts don't publish a stable proxy range; set FORWARDED_ALLOW_IPS to tighten it where they do.
# --ws-max-size: refuse oversized WebSocket frames at the protocol layer.
# --timeout-keep-alive: must exceed the load balancer idle timeout, or a proxy can reuse a connection
#   that uvicorn just closed (uvicorn defaults to 5s) and surface a sporadic 502.
exec python -m uvicorn app.main:create_app --factory \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
  --ws-max-size 65536 \
  --timeout-keep-alive "${KEEPALIVE_TIMEOUT:-75}" \
  --timeout-graceful-shutdown 15
