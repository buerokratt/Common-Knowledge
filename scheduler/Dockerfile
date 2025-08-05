FROM python:3.13 AS build


WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8124

CMD ["uvicorn", "api.app:app", "--port", "8124", "--host", "0.0.0.0"]
