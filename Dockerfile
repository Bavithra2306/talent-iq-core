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

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

# Run as a normal user, not as root.
RUN useradd --create-home --uid 1000 app && chown app:app /app /opt
USER app

COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Bake Docling's models into the image so the first parse doesn't download them.
# A local ./docling-models folder (copied from an earlier image) is reused when present,
# so builds don't depend on the model sites being reachable; otherwise they are downloaded.
COPY --chown=app:app pyproject.toml docling-model[s] /tmp/models/
RUN if [ -d /tmp/models/RapidOcr ]; then mv /tmp/models /opt/docling-models;     else rm -rf /tmp/models && uv run --no-sync docling-tools models download -o /opt/docling-models; fi

COPY --chown=app:app src/ ./src/
COPY --chown=app:app alembic.ini ./

EXPOSE 8001

CMD ["uv", "run", "--no-sync", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
