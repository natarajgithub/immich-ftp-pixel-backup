FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY sql ./sql
COPY config/app.example.yaml config/users.example.yaml ./config/

RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data /config

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"

CMD ["immich-ftp-pixel-backup", "--app-config", "/config/app.yaml", "--users-config", "/config/users.yaml"]
