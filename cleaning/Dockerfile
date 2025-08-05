FROM python:3.13 AS build

RUN apt-get update && apt-get install ffmpeg libreoffice poppler-utils tesseract-ocr \
                                      tesseract-ocr-est tesseract-ocr-rus -y

WORKDIR /app
RUN mkdir /scrapped-data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8123

CMD ["uvicorn", "api.app:app", "--port", "8123", "--host", "0.0.0.0"]
