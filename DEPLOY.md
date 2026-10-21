# Deploying the demo

The app needs Docker on the machine that runs it (the agent starts a sandbox container per run), so it
runs on a VM, not on a serverless platform. A small Linux VM works. Nebius AI Cloud VMs are a natural
fit and show another Nebius service in use, but any Linux host with Docker works.

## On a fresh Ubuntu VM

```bash
sudo apt-get update && sudo apt-get install -y docker.io git python3-venv
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/<you>/patchwright && cd patchwright
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
cp .env.example .env     # set PROVIDER=nebius, NEBIUS_API_KEY, TAVILY_API_KEY
python -m patchwright configure && python -m patchwright doctor
```

## Run it publicly (safe mode)

```bash
DEMO_MODE=1 HOST=0.0.0.0 PORT=8000 MAX_CONCURRENT_RUNS=2 python -m webapp.app
```

`DEMO_MODE=1` allows only the bundled example bugs, disables custom repositories and benchmark control,
and the queue is capped, so strangers cannot use your server or your credits for arbitrary work.

Keep it running with systemd or `tmux`. Put it behind HTTPS (Caddy is the simplest:
`caddy reverse-proxy --from your.domain --to localhost:8000`). Open only ports 80/443 in the firewall.

## Costs and limits

Each example run uses roughly 10k-60k tokens. Set a spending limit in your Nebius account, and keep
`MAX_QUEUED_RUNS` low. Rotate the API key after the hackathon.
