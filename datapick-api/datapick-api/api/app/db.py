import json
from collections.abc import AsyncIterator

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # NUMERIC -> float y JSONB -> dict para no convertir a mano en cada consulta
    await conn.set_type_codec("numeric", encoder=str, decoder=float, schema="pg_catalog", format="text")
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def open_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10, init=_init_connection)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    """Dependencia de FastAPI: una conexión del pool por petición."""
    assert _pool is not None, "pool no inicializado"
    async with _pool.acquire() as conn:
        yield conn
