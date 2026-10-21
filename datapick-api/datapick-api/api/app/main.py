from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import close_pool, get_conn, open_pool
from .routers import admin, auth, matches, me, parlays, picks, transparency


@asynccontextmanager
async def lifespan(_: FastAPI):
    await open_pool()
    yield
    await close_pool()


app = FastAPI(
    title="DataPick API",
    version="0.1.0",
    description="Pronósticos de fútbol basados en datos: picks +EV, combinadas 5x–7x, Kelly y transparencia.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, picks.router, matches.router, parlays.router, transparency.router, me.router, admin.router):
    app.include_router(r)


@app.get("/health", tags=["sistema"])
async def health(conn=Depends(get_conn)):
    return {"ok": await conn.fetchval("SELECT 1") == 1}
