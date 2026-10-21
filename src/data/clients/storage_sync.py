"""One-time copy of local files to the GCS bucket.

    docker compose exec core uv run --no-sync python -m src.data.clients.storage_sync
"""

import logging

from src.data.clients.storage import storage

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uploaded, present = storage.sync_local_to_gcs()
    print(f"GCS bucket ready: {uploaded} file(s) uploaded, {present} were already there.")
