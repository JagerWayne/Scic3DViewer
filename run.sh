#!/usr/bin/env bash
# Foreground run for testing. In production use the systemd service (install-service.sh).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ -x .venv/bin/uvicorn ] || ./setup.sh
set -a; [ -f .env ] && . ./.env; set +a
# 127.0.0.1 only: the public face is the HTTPS reverse proxy (Caddy), never this port
exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 6711 --proxy-headers
