FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /usr/local/bin/

WORKDIR /app

# CONTENT_WORK_DIR: the run lock and the deferred-deletion journal. Never under
# /scrapped-data, which is zipped into the Global Classifier's payload (rule 1).
RUN mkdir /var/lib/content-external

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project --extra content-external

COPY content-external/ /app/

EXPOSE 8125

CMD ["uvicorn", "exporter.api.app:app", "--port", "8125", "--host", "0.0.0.0"]
