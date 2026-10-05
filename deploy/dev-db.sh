#!/usr/bin/env bash
# Starts a local Postgres 17 for development and tests, in a Podman container.
#   ./deploy/dev-db.sh          start (or keep running) the dev database on port 5432
#   ./deploy/dev-db.sh stop     stop it (data is kept in the hoops-dev-pgdata volume)
#   ./deploy/dev-db.sh reset    delete it and its data
# Creates two databases: `hoops` for development and `hoops_test`, which tests wipe freely.
set -euo pipefail

NAME=hoops-dev-postgres
VOLUME=hoops-dev-pgdata
PORT="${HOOPS_DEV_DB_PORT:-5432}"

case "${1:-start}" in
  start)
    if ! podman container exists "$NAME"; then
      podman run -d --name "$NAME" \
        -e POSTGRES_USER=hoops -e POSTGRES_PASSWORD=hoops -e POSTGRES_DB=hoops \
        -p "127.0.0.1:${PORT}:5432" -v "${VOLUME}:/var/lib/postgresql/data" \
        docker.io/library/postgres:17 >/dev/null
    else
      podman start "$NAME" >/dev/null
    fi
    for _ in $(seq 1 30); do
      podman exec "$NAME" pg_isready -h localhost -U hoops -q && break
      sleep 1
    done
    podman exec "$NAME" psql -U hoops -tAc "SELECT 1 FROM pg_database WHERE datname = 'hoops_test'" \
      | grep -q 1 || podman exec "$NAME" createdb -U hoops -E UTF8 -T template0 hoops_test
    echo "Postgres 17 ready on 127.0.0.1:${PORT}"
    echo "  export HOOPS_DATABASE_URL=postgresql://hoops:hoops@127.0.0.1:${PORT}/hoops"
    echo "  export HOOPS_TEST_DATABASE_URL=postgresql://hoops:hoops@127.0.0.1:${PORT}/hoops_test"
    ;;
  stop)
    podman stop "$NAME" >/dev/null && echo "stopped"
    ;;
  reset)
    podman rm -f "$NAME" >/dev/null 2>&1 || true
    podman volume rm -f "$VOLUME" >/dev/null 2>&1 || true
    echo "removed container and data"
    ;;
  *)
    echo "usage: $0 [start|stop|reset]" >&2
    exit 1
    ;;
esac
