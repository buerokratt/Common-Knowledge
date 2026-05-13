FROM python:3.13-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install --no-install-recommends -y \
        libreoffice \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir /scrapped-data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8123

CMD ["uvicorn", "api.app:app", "--port", "8123", "--host", "0.0.0.0"]
