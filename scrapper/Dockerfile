FROM python:3.13 AS build

WORKDIR /app
RUN mkdir /scrapped-data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8080


FROM build AS server

CMD ["uvicorn", "api.app:app", "--port", "8080", "--host", "0.0.0.0"]

FROM build AS celery

RUN playwright install-deps
RUN playwright install


CMD ["celery", "-A", "worker.tasks", "worker", "--loglevel=INFO", "-E"]
