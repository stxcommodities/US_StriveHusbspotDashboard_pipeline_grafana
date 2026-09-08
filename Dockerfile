FROM python:3.12-slim

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config.py hubspot_client.py db.py etl.py schema.sql ./

RUN useradd --create-home --shell /bin/bash appuser
USER appuser

ENTRYPOINT ["python", "etl.py"]
