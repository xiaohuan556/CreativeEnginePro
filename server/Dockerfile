FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/server

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/requirements.txt server/requirements-worker.txt /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/requirements.txt -r /tmp/requirements/requirements-worker.txt

COPY . /app
RUN useradd --create-home --uid 10001 creative \
    && mkdir -p /var/lib/creative-engine/media /app/work_temp /app/work_output \
    && chown -R creative:creative /var/lib/creative-engine /app/work_temp /app/work_output

USER creative
WORKDIR /app/server
EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()" || exit 1

CMD ["uvicorn", "creative_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
