from fastapi import APIRouter, Depends

from ..db import get_conn
from ..schemas import Parlay

router = APIRouter(prefix="/parlays", tags=["combinadas"])


@router.get("/today", response_model=list[Parlay])
async def parlays_today(conn=Depends(get_conn)):
    """Combinadas publicadas cuyos partidos aún no han empezado, de mejor a peor."""
    rows = await conn.fetch(
        """
        SELECT pa.id, pa.rank_of_day, pa.total_odds, pa.joint_probability, pa.ev, pa.stake_pct,
               COALESCE(json_agg(json_build_object(
                   'key', CASE WHEN pl.is_bet_builder THEN 'bb-' || pl.joint_prediction_id ELSE 'leg-' || pl.id END,
                   'matchId', pl.match_id,
                   'title', th.name || ' vs ' || ta.name,
                   'selections', (SELECT json_agg(s.label ORDER BY s.id) FROM parlay_leg_selections x
                                  JOIN selections s ON s.id = x.selection_id WHERE x.leg_id = pl.id),
                   'pickIds', (SELECT COALESCE(json_agg(x.pick_id) FILTER (WHERE x.pick_id IS NOT NULL), '[]')
                               FROM parlay_leg_selections x WHERE x.leg_id = pl.id),
                   'odds', pl.leg_odds,
                   'probability', pl.leg_probability,
                   'isBetBuilder', pl.is_bet_builder
               ) ORDER BY m.kickoff_at) FILTER (WHERE pl.id IS NOT NULL), '[]') AS legs
        FROM parlays pa
        LEFT JOIN parlay_legs pl ON pl.parlay_id = pa.id
        LEFT JOIN matches m      ON m.id = pl.match_id
        LEFT JOIN teams th       ON th.id = m.home_team_id
        LEFT JOIN teams ta       ON ta.id = m.away_team_id
        WHERE pa.status = 'published' AND pa.first_kickoff_at > now()
        GROUP BY pa.id
        ORDER BY pa.published_at DESC, pa.rank_of_day
        LIMIT 3
        """
    )
    return [
        {
            "id": f"P{r['id']}",
            "legs": r["legs"],
            "total_odds": r["total_odds"],
            "probability": r["joint_probability"],
            "ev": r["ev"],
            "stake_pct": r["stake_pct"],
        }
        for r in rows
    ]
