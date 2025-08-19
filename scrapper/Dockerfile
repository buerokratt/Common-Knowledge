FROM python:3.13-bookworm AS build

WORKDIR /app
RUN mkdir /scrapped-data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

FROM build AS server

EXPOSE 8080
CMD ["uvicorn", "api.app:app", "--port", "8080", "--host", "0.0.0.0"]

FROM build AS celery

RUN playwright install-deps
RUN playwright install


CMD ["celery", "-A", "worker.tasks", "worker", "--loglevel=INFO", "-E"]
