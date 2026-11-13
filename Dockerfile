FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m scic && mkdir /data && chown scic /data
COPY --chown=scic . .
USER scic
ENV SCIC_DATA=/data
VOLUME /data
EXPOSE 6711
# --forwarded-allow-ips=* is only safe because the container is reachable only through the Caddy proxy (docker-compose.yml)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6711", "--proxy-headers", "--forwarded-allow-ips=*"]
