FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

# Sin $PORT en el CMD: Railway y otros validan el comando; entrypoint_gunicorn.py lee os.environ.
CMD ["python", "entrypoint_gunicorn.py"]
