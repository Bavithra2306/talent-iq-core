COMPOSE = docker compose -f docker-compose.yml

up:
	$(COMPOSE) up -d --build

logs:
	$(COMPOSE) logs -f core

migrate:
	$(COMPOSE) run --rm core uv run --no-sync alembic upgrade head

check:
	$(COMPOSE) run --rm core uv run --no-sync alembic check

revision:
	$(COMPOSE) run --rm core uv run --no-sync alembic revision --autogenerate -m "$(m)"

# Before every commit / deployment: lint, then the unit tests (run on this machine).
lint:
	uv run ruff check src tests

test:
	uv run pytest -q

check-all: lint test
