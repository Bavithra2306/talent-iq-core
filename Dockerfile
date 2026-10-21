FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models

# System libraries Docling's PDF/image stack needs (Debian mirrors over HTTPS).
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Bake Docling's models into the image so the first parse doesn't download them.
# A local ./docling-models folder (copied from an earlier image) is reused when present,
# so builds don't depend on the model sites being reachable; otherwise they are downloaded.
COPY pyproject.toml docling-model[s] /tmp/models/
RUN if [ -d /tmp/models/RapidOcr ]; then mv /tmp/models /opt/docling-models;     else rm -rf /tmp/models && uv run --no-sync docling-tools models download -o /opt/docling-models; fi

COPY src/ ./src/
COPY alembic.ini ./

EXPOSE 8001

CMD ["uv", "run", "--no-sync", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
