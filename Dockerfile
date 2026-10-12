FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libspatialindex-dev sumo sumo-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

EXPOSE 10000
CMD ["sh", "-c", "uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-10000}"]