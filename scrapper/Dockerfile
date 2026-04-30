FROM python:3.12-bookworm AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /usr/local/bin/

WORKDIR /app
RUN mkdir /scrapped-data

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project --extra scrapper

COPY scrapper/ /app/

FROM build AS server

EXPOSE 8080
CMD ["uvicorn", "api.app:app", "--port", "8080", "--host", "0.0.0.0"]

FROM build AS celery

RUN playwright install-deps
RUN playwright install


CMD ["celery", "-A", "worker.tasks", "worker", "--loglevel=INFO", "-E"]
