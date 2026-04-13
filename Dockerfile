FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD gunicorn api:app --bind 0.0.0.0:${PORT} --timeout 300 --workers 1
