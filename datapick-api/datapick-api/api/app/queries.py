"""SQL compartido entre routers."""

PICK_COLUMNS = """
    p.id, m.id AS match_id, l.name AS league, th.name AS home, ta.name AS away,
    m.kickoff_at AS kickoff, mt.category::text AS category, mt.name AS market,
    s.id AS selection_id, s.label AS selection, b.name AS bookmaker, p.tier::text AS tier,
    p.probability, pr.p_model AS model_probability, pr.p_market AS market_probability,
    p.odds AS published_odds,
    COALESCE(ol.price, p.odds) AS current_odds,
    COALESCE(ol.opening_price, p.odds) AS opening_odds,
    (m.status = 'lineups_confirmed') AS lineups_confirmed,
    pr.p_start_used AS p_start,
    COALESCE(pr.features -> 'reasons', '[]'::jsonb) AS reasons,
    p.stake_pct,
    p.probability * COALESCE(ol.price, p.odds) - 1 AS current_ev,
    p.row_hash
"""

PICK_FROM = """
FROM picks p
JOIN predictions pr  ON pr.id = p.prediction_id
JOIN selections s    ON s.id = p.selection_id
JOIN market_types mt ON mt.id = s.market_type_id
JOIN matches m       ON m.id = s.match_id
JOIN teams th        ON th.id = m.home_team_id
JOIN teams ta        ON ta.id = m.away_team_id
JOIN seasons se      ON se.id = m.season_id
JOIN leagues l       ON l.id = se.league_id
JOIN bookmakers b    ON b.id = p.bookmaker_id
LEFT JOIN odds_latest ol ON ol.selection_id = p.selection_id AND ol.bookmaker_id = p.bookmaker_id
"""

ACTIVE_PICKS = f"""
SELECT {PICK_COLUMNS}
{PICK_FROM}
WHERE p.status = 'published' AND p.superseded_by IS NULL AND m.kickoff_at > now()
ORDER BY m.kickoff_at, p.id
"""

PICK_BY_ID = f"SELECT {PICK_COLUMNS} {PICK_FROM} WHERE p.id = $1"

# Cuota cada 30 minutos en las últimas 24 h, para el minigráfico de cada pick
ODDS_HISTORY = """
SELECT pick_id, array_agg(close ORDER BY bucket) AS history
FROM (
    SELECT p.id AS pick_id, time_bucket('30 minutes', o.ts) AS bucket, last(o.price, o.ts) AS close
    FROM picks p
    JOIN odds_snapshots o ON o.selection_id = p.selection_id AND o.bookmaker_id = p.bookmaker_id
    WHERE p.id = ANY($1::bigint[]) AND o.ts > now() - INTERVAL '24 hours'
    GROUP BY p.id, bucket
) x
GROUP BY pick_id
"""
