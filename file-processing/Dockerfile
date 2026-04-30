FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /usr/local/bin/

WORKDIR /app

RUN mkdir /scrapped-data

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project --extra file-processing

COPY file-processing/ /app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--log-config", "log_conf.yaml", "--port", "8888", "--reload"]
