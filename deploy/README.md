# Deploying hoops on the VPS

The app runs as four rootless Podman containers managed by systemd (Quadlet), plus a nightly backup:

| Service | What it is | Reachable from |
| --- | --- | --- |
| `hoops-postgres` | Postgres 17 | the `hoops` container network only |
| `hoops-api` | FastAPI | the `hoops` network only |
| `hoops-worker` | Background jobs (low CPU priority) | nothing (it only calls out to ESPN) |
| `hoops-web` | Website files and `/api` forwarding (Caddy) | `127.0.0.1:8080` on the VPS |
| `hoops-backup.timer` | Nightly database backup to `~/hoops-backups` | nothing |

Your existing Caddy keeps ports 80 and 443 and forwards `hoops.<your domain>` to `hoops-web`.

## What the server needs

- **Linux with systemd**, for example Ubuntu 24.04 or Debian 13.
- **Podman 4.4 or newer** (for Quadlet), plus `git` and `openssl`. On Ubuntu or Debian: `sudo apt install podman git tmux`.
- **About 2 CPU cores, 4 GB of memory, and 10 GB of free disk** for the images, database (about 1 GB), raw ESPN files and backups. The app caps its own memory so the VPS's other services keep theirs.
- **A user account that owns the app** (not root), with a normal SSH login.
- **Port 8080 free on 127.0.0.1**, where the app's internal web server listens.
- **Your existing Caddy** (or another reverse proxy) for public HTTPS.

## First-time setup

1. **Let the user's services run without a login session** (one time, needs sudo):

   ```
   sudo loginctl enable-linger "$USER"
   ```

2. **Clone the repository** anywhere, for example `~/hoops`:

   ```
   git clone https://github.com/ntatinaro/basketball-analytics.git ~/hoops
   cd ~/hoops
   ```

3. **Run the setup script** inside tmux (or screen), so a dropped SSH connection does not stop it:

   ```
   tmux new -s hoops
   ./deploy/setup.sh
   ```

   It checks the server, creates `deploy/.env` (it asks for an admin panel password, or generates one, and generates the database password and session key), then:

   - deploys the database, API and website;
   - loads every season from 2021-22, including this season's games so far (about 2 to 3 hours);
   - runs the game model and player projection exams on the finished seasons (an hour or more), and records the winners as champions;
   - builds rating history, this season included, and predicts upcoming games;
   - starts the worker last, so no game is predicted or locked by an untested model.

   Detach from tmux with `Ctrl-b d` and come back with `tmux attach -t hoops`. If it stops, run it again: finished steps are skipped, and the backfill continues where it stopped.

   Without the script, run the same steps by hand (seasons are named by their end year, so 2026-27 is 2027):

   ```
   cp deploy/.env.example deploy/.env && nano deploy/.env    # passwords: see the comments
   mkdir -p ~/.config/hoops && touch ~/.config/hoops/setup-in-progress    # keeps the worker off
   ./deploy/deploy.sh main
   HOOPS="podman run --rm --network hoops --env-file $HOME/.config/hoops/hoops.env -v hoops-raw:/data/raw localhost/hoops-backend:latest"
   $HOOPS backfill --league nba --seasons 2022-2027
   $HOOPS sync-rosters --league nba --season 2027
   $HOOPS quality-report --league nba
   $HOOPS exams --league nba --warmup 2022 --seasons 2023-2026
   $HOOPS projection-exams --league nba --warmup 2022 --seasons 2023-2026
   $HOOPS rebuild-ratings --league nba --seasons 2022-2027
   $HOOPS refresh-predictions --league nba
   rm ~/.config/hoops/setup-in-progress && systemctl --user start hoops-worker
   ```

   In `deploy/.env`, the database password goes in two places: as is in `POSTGRES_PASSWORD`, and URL-encoded inside `HOOPS_DATABASE_URL` (letters and digits need no encoding). `POSTGRES_PASSWORD` is only read when the database is first created.

4. **DNS:** add an `A` record for `hoops.<your domain>` pointing at the VPS's IP address.

5. **Your existing Caddy:** add a site block for the subdomain. Which address to forward to depends on how that Caddy runs:

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

6. **Check it:** open `https://hoops.<your domain>`, log in to `/nba/admin`, and look at Data health. On the next game night, the worker's log (`journalctl --user -u hoops-worker -f`) shows each prediction locking 30 minutes before tip-off, and the grade after the final.

## Everyday operations

| Task | Command |
| --- | --- |
| Deploy the latest code | `./deploy/deploy.sh` |
| Follow the worker's log | `journalctl --user -u hoops-worker -f` |
| Service status | `systemctl --user status 'hoops-*'` |
| Data quality report | `podman run --rm --network hoops --env-file ~/.config/hoops/hoops.env localhost/hoops-backend:latest quality-report --league nba` |
| Database shell | `podman exec -it hoops-postgres psql -U hoops` |
| Restart everything | `systemctl --user restart hoops-api hoops-worker hoops-web` |
| Back up now | `./deploy/backup.sh` |
| Backup schedule | `systemctl --user list-timers hoops-backup.timer` |

## Backups

`deploy.sh` installs a nightly backup (`hoops-backup.timer`, 10:00 UTC, after the night's games). It writes a compressed copy of the database to `~/hoops-backups` (about 90 MB with five past seasons, growing through the season) and keeps the newest 7. To change either, run `systemctl --user edit hoops-backup.service` and add `Environment=HOOPS_BACKUP_DIR=/path` or `Environment=HOOPS_BACKUP_KEEP=14` under `[Service]`. Copy them off the VPS now and then: a backup on the same disk does not survive losing the disk. The raw ESPN files are not backed up; a backfill downloads them again.

To restore a backup (the database is replaced; predictions keep their append-only protection):

```
systemctl --user stop hoops-worker hoops-api hoops-web
podman exec -i hoops-postgres pg_restore -U hoops -d hoops --clean --if-exists < ~/hoops-backups/hoops-<time>.dump
systemctl --user start hoops-api hoops-worker hoops-web
```

## Admin panel

`setup.sh` fills in `HOOPS_ADMIN_PASSWORD` and `HOOPS_SECRET_KEY`; to change them, edit `deploy/.env` (a new key from `openssl rand -hex 32`) and deploy. The panel is at `https://hoops.<your domain>/nba/admin`: data health, data quality, models, and NCAA absences. Leave either empty to switch the panel off.

## Dress rehearsal mode

For testing on preseason games (for example before next season's opening night), set `HOOPS_REHEARSAL=1` in `deploy/.env` and run `./deploy/deploy.sh`. Preseason games then get predictions, locks, and grades, marked as rehearsal and never shown in public grading. Set it back to `0` and deploy again before the regular season starts. During the season it stays `0`.

## Settings

All settings are in `deploy/.env` (template: `deploy/.env.example`). `deploy.sh` copies it to `~/.config/hoops/hoops.env`, which the services read. `deploy/.env` is never committed.

## Podman versions

Podman 4.4 to 4.9 (Ubuntu 24.04 ships 4.9) ignores the database's `Notify=healthy`, so the API and worker can start before Postgres accepts connections; they retry every 5 seconds until it does. Podman 5 waits for the database first.
