"""Worker: sincroniza datos reales y ejecuta el motor cada pocos minutos.

    python -m app.worker            # bucle continuo
    python -m app.worker --once     # una sola pasada
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import asyncpg

from .config import settings
from .etl.espn import date_range, sync
from .pipeline import fit_model, predict_and_publish, settle
from .services import generate_parlays

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("worker")

SEASON_START = date(2026, 7, 25)


async def run_once(conn: asyncpg.Connection, backfill: bool) -> None:
    today = datetime.now(timezone.utc).date()
    if backfill:
        s = await sync(conn, date_range(SEASON_START, today - timedelta(days=3)))
        log.info("Historial: %s", s)
    # Ventana viva: siempre se vuelve a pedir (cuotas y resultados cambian)
    s = await sync(conn, date_range(today - timedelta(days=2), today + timedelta(days=7)), skip_complete=False)
    log.info("Semana: %s", s)

    model = await fit_model(conn)
    log.info("Modelo: %d equipos con rating en %d ligas", len(model.teams), len(model.leagues))
    log.info("Motor: %s", await predict_and_publish(conn, model))
    log.info("Liquidados: %d", await settle(conn))

    pending = await conn.fetchval("SELECT count(*) FROM parlays WHERE status = 'published' AND first_kickoff_at > now()")
    if not pending:
        created = await generate_parlays(conn)
        log.info("Combinadas publicadas: %d", len(created))
    await conn.execute("REFRESH MATERIALIZED VIEW mv_transparency")


async def main(once: bool) -> None:
    first = True
    while True:
        try:
            conn = await asyncpg.connect(settings.database_url)
            from .db import _init_connection
            await _init_connection(conn)
            try:
                await run_once(conn, backfill=first)
                first = False
            finally:
                await conn.close()
        except Exception:  # noqa: BLE001 - el worker no debe morir por un fallo puntual
            log.exception("Fallo en la pasada del worker")
        if once:
            return
        await asyncio.sleep(settings.worker_interval_seconds)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    asyncio.run(main(ap.parse_args().once))
