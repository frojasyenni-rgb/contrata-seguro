FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium --with-deps

COPY . .

ENV PORT=8080
EXPOSE 8080

# Sin $PORT en el CMD: Railway y otros validan el comando; entrypoint_gunicorn.py lee os.environ.
CMD ["python", "entrypoint_gunicorn.py"]
