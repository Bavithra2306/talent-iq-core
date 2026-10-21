"""Test settings: dummy values so the app's settings load without a .env file.
No test talks to the database, an LLM or Google Cloud."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret")
os.environ.setdefault("STORAGE_BACKEND", "local")
