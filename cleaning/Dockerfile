FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    libreoffice \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-est \
    tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
RUN mkdir /scrapped-data

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY cleaning ./

EXPOSE 8123

CMD ["uv", "run", "uvicorn", "api.app:app", "--port", "8123", "--host", "0.0.0.0"]
