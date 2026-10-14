# Developer shortcuts (macOS / Linux / WSL / Git Bash). On plain Windows PowerShell see README.md,
# which lists the equivalent commands.
.DEFAULT_GOAL := help
PY ?= backend/.venv/bin/python
ifeq ($(OS),Windows_NT)
  PY = backend/.venv/Scripts/python.exe
endif
NPM = npm --prefix frontend

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## create venv, install backend + frontend dependencies
	python -m venv backend/.venv
	$(PY) -m pip install -r backend/requirements-dev.txt
	NODE_ENV=development $(NPM) ci --include=dev

services: ## start Postgres + Redis (docker)
	docker compose up -d db redis

migrate: ## apply database migrations
	cd backend && ../$(PY) -m alembic upgrade head

seed: ## demo users, bikes (with photos) and open auctions (idempotent)
	cd backend && ../$(PY) -m scripts.seed

dev-api: services migrate seed ## run the API with auto-reload on :8100
	cd backend && DEMO_MODE=true ../$(PY) -m uvicorn app.main:create_app --factory --reload --port 8100

dev-web: ## run the frontend dev server on :5173 (proxies /api to :8100)
	$(NPM) run dev

test: ## backend tests with coverage gate (needs `make services`)
	cd backend && ../$(PY) -m pytest --cov=app --cov-report=term-missing:skip-covered

test-fast: ## backend unit tests only (no services needed)
	cd backend && ../$(PY) -m pytest tests/unit -q

lint: ## ruff + mypy + eslint + tsc
	cd backend && ../$(PY) -m ruff check . && ../$(PY) -m ruff format --check . && ../$(PY) -m mypy app
	$(NPM) run lint && $(NPM) run typecheck

test-web: ## frontend unit + component tests
	$(NPM) test

e2e: ## Playwright (needs API on :8100 with relaxed login limits, and `make dev-web`)
	$(NPM) run e2e

up: ## full stack in containers: web :8080, api :8100
	docker compose up --build

observability: ## full stack + Prometheus :9090 + Grafana :3000
	docker compose --profile observability up --build -d

down: ## stop and remove containers (keeps the database volume)
	docker compose --profile observability down

load-test: ## Locust load test against the running API (see loadtest/README.md)
	$(PY) -m locust -f loadtest/locustfile.py --headless -u 200 -r 20 -t 60s --host http://127.0.0.1:8100 --csv loadtest/results/run

dashboard: ## regenerate the Grafana dashboard JSON
	python deploy/grafana/build_dashboard.py

vercel-config: ## make vercel-config API_HOST=revbid-api.onrender.com
	sed -i.bak 's/__API_HOST__/$(API_HOST)/g' frontend/vercel.json && rm frontend/vercel.json.bak

.PHONY: help install services migrate seed dev-api dev-web test test-fast lint test-web e2e up observability down load-test dashboard vercel-config
