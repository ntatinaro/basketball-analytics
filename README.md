# hoops

A basketball prediction and analytics web app for a group of friends: NBA first, then NCAA Division I men's basketball.

## Documents

- [Feature spec](docs/feature-spec.md): what the app does and why
- [Technical architecture](docs/technical-architecture.md): stack, data, models, deployment
- [Build plan](docs/build-plan.md): versions, sprints, and their checklists

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and Podman.

```
./deploy/dev-db.sh                      # start Postgres 17 locally
export HOOPS_DATABASE_URL=postgresql://hoops:hoops@127.0.0.1:5432/hoops
export HOOPS_TEST_DATABASE_URL=postgresql://hoops:hoops@127.0.0.1:5432/hoops_test

cd backend
uv sync                                 # install Python 3.13 dependencies
uv run hoops migrate                    # create or update the database tables
uv run ruff check .                     # lint
uv run pytest                           # tests (database tests need HOOPS_TEST_DATABASE_URL)
```

The test database must use UTF-8 encoding (`deploy/dev-db.sh` creates it that way); a
`SQL_ASCII` database makes the ESPN fixture tests fail. Tests wipe it on every run.

Website:

```
cd web
npm ci
npm run dev                             # http://localhost:5173, forwards /api to :8000
npm run typecheck && npm run build      # what CI runs
```

Two checks are manual only (CI does not run them; they need the API running with data):

```
npx playwright test                     # click-through at phone and desktop sizes
node scripts/perf-check.mjs             # first load and sort time on a throttled phone
```
