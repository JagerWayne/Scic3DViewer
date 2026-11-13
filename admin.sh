#!/usr/bin/env bash
# License command line (the /admin web panel does the same). Usage: ./admin.sh new | list | revoke EMAIL | upgrade EMAIL TIER
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ -x .venv/bin/python ] || ./setup.sh
set -a; [ -f .env ] && . ./.env; set +a
exec .venv/bin/python admin.py "$@"
