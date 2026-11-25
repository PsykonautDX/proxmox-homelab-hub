FROM python:3.11-slim

WORKDIR /app

# openssh-client enables optional 1-click Community-Scripts SSH deploy & LXC Docker inspection
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proxmox_hub ./proxmox_hub
COPY .env.example ./.env.example

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV WEBUI_HOST=0.0.0.0
ENV WEBUI_PORT=8806
ENV PROXMOX_DATA_DIR=/app/data
ENV PROXMOX_ENV_FILE=/app/data/.env

EXPOSE 8806

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8806/health', timeout=4)" || exit 1

CMD ["python", "-m", "proxmox_hub", "web", "--host", "0.0.0.0", "--port", "8806"]
