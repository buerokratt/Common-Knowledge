FROM python:3.13

WORKDIR /app

RUN mkdir /scrapped-data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--log-config", "log_conf.yaml", "--port", "8888", "--reload"]
