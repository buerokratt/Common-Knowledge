FROM python:3.12.10-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY scrapper/api ./api
COPY scrapper/scrapper ./scrapper
COPY scrapper/worker ./worker
COPY scrapper/scrapy.cfg ./
COPY scrapper/run_*.py ./

FROM base AS server

EXPOSE 8080
CMD ["uv", "run", "uvicorn", "api.app:app", "--port", "8080", "--host", "0.0.0.0"]

FROM base AS celery

RUN uv run playwright install-deps
RUN uv run playwright install

CMD ["uv", "run", "celery", "-A", "worker.tasks", "worker", "--loglevel=INFO", "-E"]
