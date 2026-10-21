from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn
from ..queries import ACTIVE_PICKS, ODDS_HISTORY, PICK_BY_ID
from ..schemas import OddsAlert, OddsPoint, Pick, PickDetail

router = APIRouter(prefix="/picks", tags=["picks"])


async def _with_history(conn, rows) -> list[dict]:
    ids = [r["id"] for r in rows]
    hist = {h["pick_id"]: h["history"] for h in await conn.fetch(ODDS_HISTORY, ids)} if ids else {}
    out = []
    for r in rows:
        d = dict(r)
        d["odds_history"] = hist.get(r["id"]) or [d["opening_odds"], d["current_odds"]]
        out.append(d)
    return out


@router.get("/active", response_model=list[Pick])
async def active_picks(tier: str | None = None, conn=Depends(get_conn)):
    """Picks publicados de partidos que aún no han empezado."""
    rows = await conn.fetch(ACTIVE_PICKS)
    if tier:
        rows = [r for r in rows if r["tier"] == tier]
    return await _with_history(conn, rows)


@router.get("/{pick_id}", response_model=PickDetail)
async def pick_detail(pick_id: int, conn=Depends(get_conn)):
    row = await conn.fetchrow(PICK_BY_ID, pick_id)
    if row is None:
        raise HTTPException(404, "Pick no encontrado")
    [d] = await _with_history(conn, [row])

    series = await conn.fetch(
        """
        SELECT time_bucket('5 minutes', o.ts) AS ts, last(o.price, o.ts) AS price
        FROM odds_snapshots o JOIN picks p ON p.selection_id = o.selection_id AND p.bookmaker_id = o.bookmaker_id
        WHERE p.id = $1 AND o.ts > now() - INTERVAL '48 hours'
        GROUP BY 1 ORDER BY 1
        """,
        pick_id,
    )
    alerts = await conn.fetch(
        """
        SELECT a.id, a.kind::text, b.name AS bookmaker, a.price_before, a.price_after,
               a.pct_change, a.window_minutes, a.detected_at
        FROM odds_alerts a JOIN bookmakers b ON b.id = a.bookmaker_id
        JOIN picks p ON p.selection_id = a.selection_id
        WHERE p.id = $1 ORDER BY a.detected_at DESC
        """,
        pick_id,
    )
    d["odds_series"] = [OddsPoint(**dict(s)) for s in series]
    d["alerts"] = [OddsAlert(**dict(a)) for a in alerts]
    d["ledger_hash"] = d.pop("row_hash")
    return d
