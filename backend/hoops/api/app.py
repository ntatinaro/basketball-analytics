"""The read-only JSON API (architecture doc, section 11)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from hoops.api import games, misc, players, projections, teams

app = FastAPI(title="hoops", docs_url="/api/docs", openapi_url="/api/openapi.json",
              redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
for router in (misc.router, games.router, projections.router, teams.router, players.router):
    app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
