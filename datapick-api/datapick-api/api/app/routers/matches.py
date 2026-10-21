from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ..db import get_conn
from ..schemas import ScheduleMatch

router = APIRouter(prefix="/matches", tags=["calendario"])


@router.get("/week", response_model=list[ScheduleMatch])
async def week(
    start: datetime | None = Query(None, alias="from", description="Inicio del rango. Por defecto, hace 6 horas."),
    days: int = Query(7, ge=1, le=14),
    league: int | None = Query(None, description="Filtrar por id de liga"),
    conn=Depends(get_conn),
):
    """Calendario de partidos con probabilidades 1X2 del modelo y los picks publicados de cada uno.

    La app manda `from` con el inicio del día en su zona horaria, para que "hoy"
    incluya también los partidos que ya terminaron.
    """
    start = start or datetime.now(timezone.utc) - timedelta(hours=6)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    rows = await conn.fetch(
        """
        SELECT m.id, l.id AS league_id, l.name AS league, m.round, m.kickoff_at AS kickoff, m.status::text,
               th.name AS home, ta.name AS away, th.short_name AS home_short, ta.short_name AS away_short,
               m.home_goals, m.away_goals,
               (m.status = 'lineups_confirmed') AS lineups_confirmed,
               r.name AS referee,
               (SELECT max(importance_index) FROM match_importance mi WHERE mi.match_id = m.id) AS importance,
               x.probabilities, x.source AS probability_source, x.odds AS odds, m.venue,
               COALESCE(pk.picks, '[]'::json) AS picks
        FROM matches m
        JOIN seasons se ON se.id = m.season_id
        JOIN leagues l  ON l.id = se.league_id
        JOIN teams th   ON th.id = m.home_team_id
        JOIN teams ta   ON ta.id = m.away_team_id
        LEFT JOIN referees r ON r.id = m.referee_id
        -- última predicción 1X2 de cada resultado
        LEFT JOIN LATERAL (
            SELECT json_object_agg(s.outcome, lp.p_final) AS probabilities,
                   CASE WHEN bool_and(lp.model_version = 'market-novig') THEN 'market' ELSE 'model' END AS source,
                   CASE WHEN count(ol.price) = 3 THEN json_object_agg(s.outcome, ol.price) END AS odds
            FROM selections s
            JOIN LATERAL (SELECT p_final, model_version FROM predictions pr WHERE pr.selection_id = s.id
                          ORDER BY computed_at DESC LIMIT 1) lp ON TRUE
            LEFT JOIN odds_latest ol ON ol.selection_id = s.id AND ol.bookmaker_id = 4
            WHERE s.match_id = m.id AND s.market_type_id = (SELECT id FROM market_types WHERE code = '1X2')
            HAVING count(*) = 3
        ) x ON TRUE
        LEFT JOIN LATERAL (
            SELECT json_agg(json_build_object(
                       'id', p.id, 'tier', p.tier, 'selection', s.label,
                       'odds', p.odds, 'probability', p.probability, 'status', p.status) ORDER BY p.probability DESC) AS picks
            FROM picks p JOIN selections s ON s.id = p.selection_id
            WHERE s.match_id = m.id AND p.status <> 'no_value' AND p.superseded_by IS NULL
        ) pk ON TRUE
        WHERE m.kickoff_at >= $1 AND m.kickoff_at < $1 + make_interval(days => $2)
          AND m.status <> 'cancelled'
          AND ($3::int IS NULL OR l.id = $3)
        ORDER BY m.kickoff_at, l.liquidity_rank, m.id
        """,
        start, days, league,
    )
    return [dict(r) for r in rows]
