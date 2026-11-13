#!/usr/bin/env bash
# Backup of the database + secret.key into ./backups (keeps the newest 14). Safe while the service runs.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
set -a; [ -f .env ] && . ./.env; set +a
umask 077
exec .venv/bin/python backup.py "${1:-14}"
