#!/usr/bin/env bash
# Database backup: a compressed pg_dump of the hoops database, keeping the newest few.
# hoops-backup.timer runs it nightly; it is also safe to run by hand:
#   ./deploy/backup.sh
# Restoring is in deploy/README.md.
set -euo pipefail

DIR="${HOOPS_BACKUP_DIR:-$HOME/hoops-backups}"
KEEP="${HOOPS_BACKUP_KEEP:-7}"
CONTAINER="${HOOPS_DB_CONTAINER:-hoops-postgres}"

umask 077
mkdir -p "$DIR"
chmod 700 "$DIR"
file="$DIR/hoops-$(date -u +%Y%m%dT%H%M%SZ).dump"
# Written under a temporary name so a failed dump never replaces a good one.
podman exec "$CONTAINER" pg_dump -U hoops -d hoops --format=custom > "$file.partial"
mv "$file.partial" "$file"
# shellcheck disable=SC2012  # file names are ours: hoops-<timestamp>.dump
ls -1t "$DIR"/hoops-*.dump | tail -n +"$((KEEP + 1))" | xargs -r rm --
echo "backup written: $file ($(du -h "$file" | cut -f1)); keeping the newest $KEEP"
