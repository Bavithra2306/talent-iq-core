COMPOSE = docker compose -f ../docker-compose.yml

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
