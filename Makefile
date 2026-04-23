.PHONY: help install up down logs ps test lint format typecheck precommit \
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
	@echo "  test            - pytest (slow marker hariç)"
	@echo "  lint            - ruff + mypy strict"
	@echo "  format          - ruff format (kod düzenle)"
	@echo "  typecheck       - mypy strict"
	@echo "  precommit       - Tüm pre-commit hook'larını çalıştır"
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
	pytest tests/ --cov=src -m "not slow"

lint:
	ruff check src/ tests/ infra/coolify/
	mypy src/ infra/coolify/ --strict --ignore-missing-imports

format:
	ruff format src/ tests/ infra/coolify/
	ruff check --fix src/ tests/ infra/coolify/

typecheck:
	mypy src/ infra/coolify/ --strict --ignore-missing-imports

precommit:
	pre-commit run --all-files

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
