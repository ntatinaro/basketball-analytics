#!/usr/bin/env bash
# First-time setup on a Linux VPS. Run once, as the user that will own the app (not root),
# from the cloned repository:
#   ./deploy/setup.sh
#
# It checks the server, creates deploy/.env, deploys, loads past seasons and this season so
# far, runs the model exams, builds rating history, and only then starts the worker, so
# nothing is predicted or locked with untested models. The long steps take several hours:
# run it inside tmux or screen so a dropped SSH connection does not stop it. It is safe to
# re-run: finished steps are skipped, and an interrupted backfill picks up where it stopped.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$HOME/.config/hoops"
DONE="$CONFIG/setup-done"
IN_PROGRESS="$CONFIG/setup-in-progress"
FIRST_SEASON=2022          # 2021-22, the first season after the bubble seasons

step() { current="$*"; printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
current="Checking the server"
stopped() {
  local status=$?
  [ "$status" -eq 0 ] && return
  printf '\nSetup stopped during: %s\n' "$current" >&2
  printf 'Fix the problem above and run ./deploy/setup.sh again; finished steps are skipped.\n' >&2
}
trap stopped EXIT

# -- 1. Checks ----------------------------------------------------------------------------

step "Checking the server"
[ "$(id -u)" -ne 0 ] || die "run this as the user that will own the app, not as root"
for cmd in podman git openssl awk; do
  command -v "$cmd" >/dev/null || die "$cmd is not installed"
done

version="$(podman version --format '{{.Client.Version}}')"
major="${version%%.*}"; rest="${version#*.}"; minor="${rest%%.*}"
if [ "$major" -lt 4 ] || { [ "$major" -eq 4 ] && [ "$minor" -lt 4 ]; }; then
  die "Podman $version is too old: Quadlet needs 4.4 or newer (Ubuntu 24.04 and Debian 13 ship new enough versions)"
fi
echo "Podman $version"

systemctl --user show-environment >/dev/null 2>&1 \
  || die "cannot reach your systemd user session. Log in over SSH as this user directly (su and sudo -u do not start one)."
[ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null)" = yes ] \
  || die "the app's services must keep running after you log out. Run once: sudo loginctl enable-linger $USER"
podman info >/dev/null 2>&1 || die "rootless Podman does not work for this user (see 'podman info')"

avail_kb="$(df -Pk "$HOME" 2>/dev/null | awk 'NR == 2 {print $4}')"
avail_gb=$(( ${avail_kb:-0} / 1024 / 1024 ))
[ "$avail_gb" -ge 10 ] || warn "only ${avail_gb} GB free in $HOME; plan for about 10 GB (images, database, raw ESPN files, backups)"
mem_gb=$(( $(awk '/^MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
[ "$mem_gb" -ge 3 ] || warn "only ${mem_gb} GB of memory; the model exams need about 2 GB on top of the database"
if command -v ss >/dev/null && ss -ltnH 'sport = :8080' | grep -q .; then
  warn "something already listens on port 8080; change PublishPort in deploy/quadlet/hoops-web.container"
fi

mkdir -p "$CONFIG"
if grep -qx complete "$DONE" 2>/dev/null; then
  echo "Setup already finished. Deploy updates with ./deploy/deploy.sh"
  exit 0
fi
touch "$IN_PROGRESS"

# -- 2. Settings --------------------------------------------------------------------------

admin_note=""
if [ ! -f "$REPO/deploy/.env" ]; then
  step "Creating deploy/.env"
  db_password="$(openssl rand -hex 24)"      # hex: safe in the database URL as is
  secret_key="$(openssl rand -hex 32)"
  admin_password=""
  if [ -t 0 ]; then
    read -rsp "Password for the admin panel (empty: generate one): " admin_password; echo
  fi
  if [ -z "$admin_password" ]; then
    admin_password="$(openssl rand -base64 18)"
    admin_note="Admin panel password (also in deploy/.env): $admin_password"
    echo "$admin_note"
  fi
  (umask 077; DB="$db_password" KEY="$secret_key" ADMIN="$admin_password" awk '
    /^HOOPS_DATABASE_URL=/ { sub(/CHANGE_ME/, ENVIRON["DB"]) }
    /^POSTGRES_PASSWORD=/  { $0 = "POSTGRES_PASSWORD=" ENVIRON["DB"] }
    /^HOOPS_ADMIN_PASSWORD=/ { $0 = "HOOPS_ADMIN_PASSWORD=" ENVIRON["ADMIN"] }
    /^HOOPS_SECRET_KEY=/   { $0 = "HOOPS_SECRET_KEY=" ENVIRON["KEY"] }
    /^HOOPS_REHEARSAL=/    { $0 = "HOOPS_REHEARSAL=0" }
    { print }
  ' "$REPO/deploy/.env.example" > "$REPO/deploy/.env")
  echo "Written with a generated database password and session key."
else
  if grep -q CHANGE_ME "$REPO/deploy/.env"; then
    die "deploy/.env still contains CHANGE_ME; fill it in or delete it to have one generated"
  fi
  if grep -qx 'HOOPS_REHEARSAL=1' "$REPO/deploy/.env"; then
    warn "HOOPS_REHEARSAL=1 in deploy/.env: preseason mode, set it to 0 during the season"
  fi
fi

# -- 3. Deploy (database, API and website; the worker stays off) --------------------------

step "Deploying"
"$REPO/deploy/deploy.sh"

hoops() {
  podman run --rm --network hoops --env-file "$CONFIG/hoops.env" -v hoops-raw:/data/raw \
    localhost/hoops-backend:latest "$@"
}
once() {
  local name="$1" what="$2"; shift 2
  if grep -qx "$name" "$DONE" 2>/dev/null; then
    echo "already done: $what"
    return
  fi
  step "$what"
  "$@"
  echo "$name" >> "$DONE"
}

# Seasons are named by the year they end in; a new season starts in August.
year=$(TZ=America/New_York date +%Y); month=$(TZ=America/New_York date +%-m)
season=$(( month >= 8 ? year + 1 : year ))
last_full=$(( season - 1 ))

# -- 4. Data ------------------------------------------------------------------------------

once backfill "Loading seasons $FIRST_SEASON to $season (this season so far included; about 2 to 3 hours)" \
  hoops backfill --league nba --seasons "$FIRST_SEASON-$season"
once rosters "Loading current rosters" \
  hoops sync-rosters --league nba --season "$season"
step "Data quality report (each season should show at most 2% of games flagged)"
hoops quality-report --league nba

# -- 5. Models ----------------------------------------------------------------------------

once exams "Game model exams on seasons $((FIRST_SEASON + 1)) to $last_full (half an hour or more)" \
  hoops exams --league nba --warmup "$FIRST_SEASON" --seasons "$((FIRST_SEASON + 1))-$last_full"
once projection-exams "Player projection exams on the same seasons" \
  hoops projection-exams --league nba --warmup "$FIRST_SEASON" --seasons "$((FIRST_SEASON + 1))-$last_full"
once ratings "Building rating history, this season included" \
  hoops rebuild-ratings --league nba --seasons "$FIRST_SEASON-$season"
once predictions "Predicting upcoming games" \
  hoops refresh-predictions --league nba

# -- 6. Start -----------------------------------------------------------------------------

step "Starting the worker"
rm -f "$IN_PROGRESS"
systemctl --user start hoops-worker.service
echo complete >> "$DONE"

step "Setup finished"
cat <<EOF
The app is running on 127.0.0.1:8080. To put it online (deploy/README.md, steps 4 and 5):
  - add a DNS A record for hoops.<your domain> pointing at this server
  - add the hoops.<your domain> site block to your existing Caddy and reload it
Worker log:  journalctl --user -u hoops-worker -f
Backups:     nightly to ~/hoops-backups (systemctl --user list-timers hoops-backup.timer)
EOF
[ -z "$admin_note" ] || printf '\n%s\n' "$admin_note"
