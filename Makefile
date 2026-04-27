.PHONY: help install up down logs ps test test-integration test-all lint format typecheck precommit \
        migrate migrate-dry-run migrate-compose seed \
        coolify-plan coolify-apply coolify-status coolify-sync \
        clean build

PYTHON ?= python
COMPOSE_LOCAL := docker compose -f infra/docker-compose.local.yml --env-file .env.local

help:
	@echo "Hedefler:"
	@echo "  install         - Python dev deps + pre-commit install"
	@echo "  up              - Local docker compose (full stack) başlat"
	@echo "  down            - Local stack'i durdur (volume korunur)"
	@echo "  logs            - Servis loglarını takip et"
	@echo "  ps              - Servis durumunu göster"
	@echo "  test            - pytest (slow + integration hariç, hızlı unit suite)"
	@echo "  test-integration- pytest -m integration (testcontainers, Docker gerekir)"
	@echo "  test-all        - pytest tüm marker'lar (CI full run)"
	@echo "  lint            - ruff + mypy strict"
	@echo "  format          - ruff format (kod düzenle)"
	@echo "  typecheck       - mypy strict"
	@echo "  precommit       - Tüm pre-commit hook'larını çalıştır"
	@echo "  migrate         - Pending PostgreSQL migration'larını uygula (idempotent)"
	@echo "  migrate-dry-run - Pending migration'ları listele, uygulama"
	@echo "  migrate-compose - One-shot 'aqi-migrate' container ile migrate (compose profile)"
	@echo "  seed            - dim_station tablosunu config/stations.yaml'dan UPSERT et"
	@echo "  coolify-plan    - Coolify desired-state diff (dry-run)"
	@echo "  coolify-apply   - Coolify provisioning (onaylı)"
	@echo "  coolify-status  - Coolify kaynak sağlık raporu"
	@echo "  coolify-sync    - Custom secret push (aqi-streamlit + aqi-ingestion)"
	@echo "  build           - Docker image'ları local build"
	@echo "  clean           - Cache/build artefakt temizle"

install:
	pip install -e ".[dev,ingestion,processing,streamlit,coolify]"
	pre-commit install

up:
	$(COMPOSE_LOCAL) up -d

down:
	$(COMPOSE_LOCAL) down

logs:
	$(COMPOSE_LOCAL) logs -f --tail=100

ps:
	$(COMPOSE_LOCAL) ps

build:
	$(COMPOSE_LOCAL) build

test:
	pytest tests/ --cov=src -m "not slow and not integration"

test-integration:
	pytest tests/ -m "integration"

test-all:
	pytest tests/ --cov=src

lint:
	ruff check src/ tests/ infra/coolify/ infra/migrations/
	mypy src/ infra/coolify/ infra/migrations/ --strict --ignore-missing-imports

format:
	ruff format src/ tests/ infra/coolify/ infra/migrations/
	ruff check --fix src/ tests/ infra/coolify/ infra/migrations/

typecheck:
	mypy src/ infra/coolify/ infra/migrations/ --strict --ignore-missing-imports

precommit:
	pre-commit run --all-files

migrate:
	@echo "Applying migrations (DSN from environment / Settings.database_url)..."
	@$(PYTHON) -m infra.migrations.run

migrate-dry-run:
	$(PYTHON) -m infra.migrations.run --dry-run

migrate-compose:
	@echo "Running aqi-migrate one-shot container (compose profile=migrate)..."
	$(COMPOSE_LOCAL) --profile migrate run --rm aqi-migrate

seed:
	@echo "Seeding dim_station from config/stations.yaml (idempotent UPSERT)..."
	@$(PYTHON) -m infra.postgres.seed_dim_station

coolify-plan:
	$(PYTHON) -m infra.coolify.provision plan

coolify-apply:
	$(PYTHON) -m infra.coolify.provision apply

coolify-status:
	$(PYTHON) -m infra.coolify.provision status

coolify-sync:
	$(PYTHON) -m infra.coolify.sync_secrets push --app aqi-streamlit
	$(PYTHON) -m infra.coolify.sync_secrets push --app aqi-ingestion

clean:
	find . -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' \) -prune -exec rm -rf {} +
	rm -rf .coverage htmlcov/ dist/ build/ *.egg-info/
