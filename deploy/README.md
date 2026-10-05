# Deploying hoops on the VPS

The app runs as four rootless Podman containers managed by systemd (Quadlet):

| Service | What it is | Reachable from |
| --- | --- | --- |
| `hoops-postgres` | Postgres 17 | the `hoops` container network only |
| `hoops-api` | FastAPI | the `hoops` network only |
| `hoops-worker` | Background jobs (low CPU priority) | nothing (it only calls out to ESPN) |
| `hoops-web` | Website files and `/api` forwarding (Caddy) | `127.0.0.1:8080` on the VPS |

Your existing Caddy keeps ports 80 and 443 and forwards `hoops.<your domain>` to `hoops-web`.

## First-time setup

Run these as the user that will own the app (not root).

1. **Let the user's services run without a login session** (one time, needs sudo):

   ```
   sudo loginctl enable-linger "$USER"
   ```

2. **Clone the repository** anywhere, for example `~/hoops`:

   ```
   git clone https://github.com/ntatinaro/basketball-analytics.git ~/hoops
   cd ~/hoops
   ```

3. **Create the settings file** and fill in a strong database password (the same value in both places):

   ```
   cp deploy/.env.example deploy/.env
   nano deploy/.env
   ```

4. **Deploy:**

   ```
   ./deploy/deploy.sh main
   ```

   This builds the images, installs the services, applies database migrations, and starts everything.

5. **Load past seasons** (one time, about 1 to 1.5 hours; it runs in the background at low priority):

   ```
   podman run -d --name hoops-backfill --network hoops --env-file ~/.config/hoops/hoops.env \
     -v hoops-raw:/data/raw localhost/hoops-backend:latest backfill --league nba --seasons 2022-2027
   podman logs -f hoops-backfill          # follow progress
   podman run --rm --network hoops --env-file ~/.config/hoops/hoops.env \
     localhost/hoops-backend:latest quality-report --league nba
   ```

6. **DNS:** add an `A` record for `hoops.<your domain>` pointing at the VPS's IP address.

7. **Your existing Caddy:** add a site block for the subdomain. Which address to forward to depends on how that Caddy runs:

   - **Caddy installed directly on the VPS:**

     ```
     hoops.example.com {
         reverse_proxy 127.0.0.1:8080
     }
     ```

   - **Caddy in a Podman container, run by the same user as hoops:** connect it to the `hoops` network (`podman network connect hoops <caddy-container>`, or add `Network=hoops.network` to its Quadlet file) and use:

     ```
     hoops.example.com {
         reverse_proxy hoops-web:8080
     }
     ```

   - **Caddy in a container run by another user or root:** use `reverse_proxy host.containers.internal:8080`, and change `PublishPort` in `deploy/quadlet/hoops-web.container` from `127.0.0.1:8080:8080` to `<VPS private IP>:8080:8080` if the container cannot reach the VPS's loopback address. Do not publish on a public address.

   Then reload your Caddy. It obtains the HTTPS certificate for the subdomain automatically.

## Everyday operations

| Task | Command |
| --- | --- |
| Deploy the latest code | `./deploy/deploy.sh` |
| Follow the worker's log | `journalctl --user -u hoops-worker -f` |
| Service status | `systemctl --user status 'hoops-*'` |
| Data quality report | `podman run --rm --network hoops --env-file ~/.config/hoops/hoops.env localhost/hoops-backend:latest quality-report --league nba` |
| Database shell | `podman exec -it hoops-postgres psql -U hoops` |
| Restart everything | `systemctl --user restart hoops-api hoops-worker hoops-web` |

## Dress rehearsal mode

Before opening night, set `HOOPS_REHEARSAL=1` in `deploy/.env` and run `./deploy/deploy.sh`. Preseason games then get predictions, locks, and grades, marked as rehearsal and never shown in public grading. Set it back to `0` and deploy again before the regular season starts.

## Settings

All settings are in `deploy/.env` (template: `deploy/.env.example`). `deploy.sh` copies it to `~/.config/hoops/hoops.env`, which the services read. `deploy/.env` is never committed.
