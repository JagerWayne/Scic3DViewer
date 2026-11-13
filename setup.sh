#!/usr/bin/env bash
# One-time setup on Ubuntu: virtual environment + dependencies + .env. Safe to run again (it also works as "update").
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

command -v python3 >/dev/null || { echo "python3 is missing: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10 or newer is required (Ubuntu 22.04+ has it)"; exit 1; }
python3 -m venv .venv 2>/dev/null || { echo "The venv module is missing: sudo apt install -y python3-venv"; exit 1; }

.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
chmod +x ./*.sh

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env  # it will hold secrets
  echo "Created .env: edit it now (nano .env), at least SCIC_BASE_URL."
fi
mkdir -p data backups
chmod 700 data backups
if [ -f credentials.json ]; then
  chmod 600 credentials.json || echo "Warning: cannot chmod credentials.json (owned by another user? run: sudo chown wayne:wayne credentials.json)"
else
  echo "Note: credentials.json (Google) is not here yet, see GOOGLE_DRIVE_SETUP.md Part A."
fi

echo
echo "Setup complete in $(pwd)."
echo "Try it:        ./run.sh"
echo "Run as service: ./install-service.sh   (see UBUNTU.md)"
