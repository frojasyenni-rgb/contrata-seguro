FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

# Exec form: sin shell, ${PORT} no se expande. Forzamos sh -c para leer PORT en runtime (Railway, etc.).
CMD ["sh", "-c", "exec gunicorn api:app --bind 0.0.0.0:${PORT:-8080} --timeout 300 --workers 1"]
