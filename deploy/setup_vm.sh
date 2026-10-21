#!/usr/bin/env bash
# Take a fresh Ubuntu 22.04/24.04 VM to a running Patchwright demo (public-safe mode).
#
#   curl -fsSL https://raw.githubusercontent.com/rohith95-p/patchwright/main/deploy/setup_vm.sh -o setup_vm.sh
#   bash setup_vm.sh                      # first run: installs everything, then asks you to fill in .env
#   bash setup_vm.sh                      # second run: checks the keys and starts the service
#   DOMAIN=demo.example.com bash setup_vm.sh   # optionally put HTTPS in front with Caddy
#
# It never prints your keys. Not yet run on a real VM by the author: read it before you run it.
set -euo pipefail

REPO="${REPO:-https://github.com/rohith95-p/patchwright.git}"
DIR="${DIR:-$HOME/patchwright}"
PORT="${PORT:-8000}"

echo "==> installing Docker, git and Python"
sudo apt-get update -y
sudo apt-get install -y docker.io git python3-venv python3-pip
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true

if [ ! -d "$DIR/.git" ]; then
  echo "==> cloning $REPO"
  git clone "$REPO" "$DIR"
else
  git -C "$DIR" pull --ff-only
fi
cd "$DIR"

echo "==> installing Patchwright"
python3 -m venv .venv
.venv/bin/pip install -q -e .

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i 's/^PROVIDER=.*/PROVIDER=nebius/' .env
  echo
  echo "Now edit $DIR/.env and set NEBIUS_API_KEY and TAVILY_API_KEY, then run this script again."
  exit 0
fi
if ! grep -q '^NEBIUS_API_KEY=.\+' .env; then
  echo "NEBIUS_API_KEY is empty in $DIR/.env: fill it in and run this script again."; exit 1
fi

echo "==> checking models and keys (a few cents)"
sg docker -c ".venv/bin/python -m patchwright configure"
sg docker -c ".venv/bin/python -m patchwright doctor"

echo "==> starting the service in DEMO_MODE (bundled bugs only; no custom repositories; capped queue)"
sudo tee /etc/systemd/system/patchwright.service >/dev/null <<UNIT
[Unit]
Description=Patchwright demo
After=network-online.target docker.service
Requires=docker.service

[Service]
User=$USER
Group=docker
WorkingDirectory=$DIR
Environment=DEMO_MODE=1
Environment=HOST=0.0.0.0
Environment=PORT=$PORT
Environment=MAX_CONCURRENT_RUNS=2
ExecStart=$DIR/.venv/bin/python -m webapp.app
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now patchwright

if [ -n "${DOMAIN:-}" ]; then
  echo "==> HTTPS with Caddy for $DOMAIN"
  sudo apt-get install -y caddy
  echo "$DOMAIN { reverse_proxy localhost:$PORT }" | sudo tee /etc/caddy/Caddyfile >/dev/null
  sudo systemctl restart caddy
  echo "Open https://$DOMAIN (ports 80 and 443 must be open in the VM firewall)."
else
  echo "Open http://<this VM's public IP>:$PORT (open that port in the VM firewall)."
fi
echo "Status: sudo systemctl status patchwright   |   Logs: journalctl -u patchwright -f"
