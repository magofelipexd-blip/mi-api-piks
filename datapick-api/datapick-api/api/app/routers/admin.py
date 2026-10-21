"""Endpoints internos: los llaman los workers (liquidación, optimizador), no la app."""

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn
from ..deps import require_admin
from ..schemas import SettleIn
from ..services import generate_parlays, settle_pick

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/parlays/generate", status_code=201)
async def generate(include_bet_builder: bool = True, force: bool = False, conn=Depends(get_conn)):
    existing = await conn.fetchval(
        "SELECT count(*) FROM parlays WHERE status = 'published' AND first_kickoff_at > now()"
    )
    if existing and not force:
        raise HTTPException(409, "Ya hay combinadas publicadas para hoy. Usa force=true para publicar otra tanda.")
    ids = await generate_parlays(conn, include_bet_builder=include_bet_builder)
    return {"created": ids}


@router.post("/picks/{pick_id}/settle")
async def settle(pick_id: int, body: SettleIn, conn=Depends(get_conn)):
    try:
        return await settle_pick(conn, pick_id, body.result, body.closing_odds)
    except LookupError:
        raise HTTPException(404, "Pick no encontrado")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/transparency/refresh", status_code=204)
async def refresh(conn=Depends(get_conn)):
    await conn.execute("REFRESH MATERIALIZED VIEW mv_transparency")
