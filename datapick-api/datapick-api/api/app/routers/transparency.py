from fastapi import APIRouter, Depends

from ..db import get_conn
from ..schemas import Transparency

router = APIRouter(prefix="/transparency", tags=["transparencia"])


@router.get("", response_model=Transparency)
async def transparency(conn=Depends(get_conn)):
    """Panel público: todo sale del ledger de picks, no de cifras escritas a mano."""
    rows = await conn.fetch(
        """
        SELECT tier, picks, won, win_rate, profit_units, yield AS yield_pct, avg_odds,
               avg_probability, avg_clv, brier
        FROM mv_transparency ORDER BY tier = 'total' DESC, tier
        """
    )
    parlays = await conn.fetchrow(
        """
        SELECT count(*) AS played,
               count(*) FILTER (WHERE status = 'won') AS won,
               COALESCE(sum(profit_units) / NULLIF(count(*), 0), 0) AS yield_pct
        FROM parlays WHERE status IN ('won', 'lost')
        """
    )
    curve = await conn.fetch(
        """
        SELECT sum(sum(profit_units)) OVER (ORDER BY date_trunc('week', kickoff_at)) AS cumulative
        FROM v_settled_picks GROUP BY date_trunc('week', kickoff_at)
        ORDER BY date_trunc('week', kickoff_at)
        """
    )
    calibration = await conn.fetch(
        "SELECT promised, observed, n FROM v_calibration WHERE n >= 20 ORDER BY bucket_from"
    )
    ledger = await conn.fetchrow(
        """
        SELECT count(*) AS picks,
               COALESCE(array_agg(id) FILTER (WHERE NOT hash_ok OR NOT chain_ok), '{}') AS broken,
               (SELECT row_hash FROM picks ORDER BY id DESC LIMIT 1) AS last_hash
        FROM v_ledger_integrity
        """
    )
    return {
        "rows": [dict(r) for r in rows],
        "parlays": dict(parlays),
        "profit_curve": [0.0] + [round(c["cumulative"], 2) for c in curve],
        "calibration": [dict(c) for c in calibration],
        "ledger": dict(ledger),
    }
