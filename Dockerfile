FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    SUMO_HOME=/usr/local/lib/python3.11/site-packages/sumo

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libspatialindex-dev \
       libgl1 libx11-6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Debian's packaged SUMO is older than the 1.20 network files in auto_tool/,
# so install the official SUMO binaries from PyPI instead.
RUN pip install --no-cache-dir "eclipse-sumo>=1.20,<2" \
    && sumo --version && netconvert --version && duarouter --version \
    && test -d "$SUMO_HOME/data/xsd"

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY auto_tool/ /app/auto_tool/

EXPOSE 10000
CMD ["sh", "-c", "uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-10000}"]
