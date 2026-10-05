#!/usr/bin/env bash
# Deploys the latest code on the VPS. Run as the user that owns the app (not root):
#   ./deploy/deploy.sh            deploy the branch currently checked out
#   ./deploy/deploy.sh main       switch to and deploy a specific branch
# First-time setup is in deploy/README.md. This script never touches other services.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${1:-$(git -C "$REPO" rev-parse --abbrev-ref HEAD)}"
CONFIG="$HOME/.config/hoops"
UNITS="$HOME/.config/containers/systemd"

step() { printf '\n==> %s\n' "$*"; }

if [ ! -f "$REPO/deploy/.env" ]; then
  echo "Missing deploy/.env. Copy deploy/.env.example to deploy/.env and fill it in." >&2
  exit 1
fi

step "Updating code ($BRANCH)"
git -C "$REPO" fetch origin "$BRANCH"
git -C "$REPO" checkout "$BRANCH"
git -C "$REPO" pull --ff-only origin "$BRANCH"

step "Building images"
podman build -t hoops-backend:latest -f "$REPO/deploy/Containerfile.backend" "$REPO"
if [ -d "$REPO/web" ]; then
  podman build -t hoops-web:latest -f "$REPO/deploy/Containerfile.web" "$REPO"
fi

step "Installing settings and service definitions"
mkdir -p "$CONFIG" "$UNITS"
install -m 600 "$REPO/deploy/.env" "$CONFIG/hoops.env"
cp "$REPO"/deploy/quadlet/* "$UNITS/"
systemctl --user daemon-reload

step "Starting the database and applying migrations"
systemctl --user start hoops-postgres.service
for _ in $(seq 1 30); do
  podman exec hoops-postgres pg_isready -h localhost -U hoops -q && break
  sleep 2
done
podman run --rm --network hoops --env-file "$CONFIG/hoops.env" localhost/hoops-backend:latest migrate

step "Restarting the app"
services="hoops-api.service hoops-worker.service"
if podman image exists localhost/hoops-web:latest; then
  services="$services hoops-web.service"
fi
# shellcheck disable=SC2086
systemctl --user restart $services
# Only this app's images (labelled in the Containerfiles); never other projects'.
podman image prune -f --filter label=org.hoops.app=hoops >/dev/null

step "Status"
# shellcheck disable=SC2086
systemctl --user --no-pager --lines=0 status hoops-postgres.service $services || true
echo
echo "Done. Logs: journalctl --user -u hoops-worker -f"
