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
