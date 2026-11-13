#!/usr/bin/env bash
# Installs and starts the systemd service. Run as the normal user (it asks for sudo).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ -x .venv/bin/uvicorn ] || ./setup.sh
if grep -q 'example.com' .env 2>/dev/null; then
  echo "Edit .env first: SCIC_BASE_URL still has the example domain (nano .env), then run this again."; exit 1
fi
sudo cp scic3d.service /etc/systemd/system/scic3d.service
sudo systemctl daemon-reload
sudo systemctl enable --now scic3d
sleep 2
systemctl --no-pager --lines=5 status scic3d || true
echo
echo "Logs:    journalctl -u scic3d -f"
echo "Restart: sudo systemctl restart scic3d    (after editing .env or updating the code)"
