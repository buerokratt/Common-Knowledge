FROM python:3.12.10-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY file-processing/app ./app
COPY file-processing/log_conf.yaml ./

EXPOSE 8888

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--log-config", "log_conf.yaml", "--port", "8888"]
