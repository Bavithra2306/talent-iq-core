from src.api.rest.app import create_app
from src.config.settings import settings
from src.observability.logging import setup_logging

# Server logs go to stdout as JSON lines: read them with `docker compose logs core`.
# Pipeline stages (upload, reading, scoring, sharing ...) are also stored in the
# audit_logs table and shown on the Activity log page.
setup_logging(settings.log_level, settings.log_json)

app = create_app()
