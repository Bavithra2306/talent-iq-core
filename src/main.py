import logging

from src.api.rest.app import create_app

# Server log: audit/stage events, LLM calls and parsing show up in `docker compose logs core`.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = create_app()
