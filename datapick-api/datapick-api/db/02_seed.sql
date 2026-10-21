-- =====================================================================
--  Catálogo inicial. No contiene partidos ni picks inventados:
--  el worker importa el calendario, los resultados y las cuotas reales
--  desde ESPN (app/etl/espn.py) y publica los picks que tengan valor.
-- =====================================================================

-- strength: coeficiente aproximado de cada liga para los cruces europeos
INSERT INTO leagues (id, provider_id, espn_code, name, country, liquidity_rank, strength) VALUES
 (1, 39,  'eng.1',          'Premier League',        'England', 1, 1.000),
 (2, 140, 'esp.1',          'LaLiga',                'Spain',   2, 0.970),
 (3, 135, 'ita.1',          'Serie A',               'Italy',   3, 0.950),
 (4, 78,  'ger.1',          'Bundesliga',            'Germany', 4, 0.950),
 (5, 61,  'fra.1',          'Ligue 1',               'France',  5, 0.880),
 (6, 2,   'uefa.champions', 'UEFA Champions League', 'Europe',  1, 1.000);
SELECT setval('leagues_id_seq', 6);

INSERT INTO seasons (league_id, label, starts_on, ends_on, is_current)
SELECT id, '2026/27', DATE '2026-07-01', DATE '2027-06-30', TRUE FROM leagues;

INSERT INTO bookmakers (id, code, name, is_sharp) VALUES
 (1, 'pinnacle',   'Pinnacle',   TRUE),
 (2, 'betano',     'Betano',     FALSE),
 (3, 'bet365',     'bet365',     FALSE),
 (4, 'draftkings', 'DraftKings', FALSE);
SELECT setval('bookmakers_id_seq', 4);

INSERT INTO market_types (id, code, name, category, has_line, is_player_market, outcomes) VALUES
 (1,  '1X2',             'Resultado final',          'result',       FALSE, FALSE, '{home,draw,away}'),
 (2,  'DOUBLE_CHANCE',   'Doble oportunidad',        'result',       FALSE, FALSE, '{1X,12,X2}'),
 (3,  'TOTAL_GOALS',     'Total de goles',           'goals',        TRUE,  FALSE, '{over,under}'),
 (4,  'BTTS',            'Ambos marcan',             'goals',        FALSE, FALSE, '{yes,no}'),
 (5,  'TOTAL_CORNERS',   'Total de córners',         'corners',      TRUE,  FALSE, '{over,under}'),
 (6,  'TEAM_CORNERS',    'Córners por equipo',       'corners',      TRUE,  FALSE, '{over,under}'),
 (7,  'TOTAL_CARDS',     'Total de tarjetas',        'cards',        TRUE,  FALSE, '{over,under}'),
 (8,  'TEAM_SHOTS_OT',   'Remates a puerta equipo',  'shots_team',   TRUE,  FALSE, '{over,under}'),
 (9,  'PLAYER_SHOTS',    'Remates del jugador',      'shots_player', TRUE,  TRUE,  '{over,under}'),
 (10, 'PLAYER_SHOTS_OT', 'Remates a puerta jugador', 'shots_player', TRUE,  TRUE,  '{over,under}'),
 (11, 'ASIAN_HANDICAP',  'Hándicap asiático',        'handicap',     TRUE,  FALSE, '{home,away}'),
 (12, 'TEAM_GOALS',      'Goles del equipo',         'goals',        TRUE,  FALSE, '{over,under}');
SELECT setval('market_types_id_seq', 12);

-- model_weight = w en p_final = w·p_modelo + (1−w)·p_mercado
INSERT INTO model_versions (code, category, model_weight, trained_at, is_live) VALUES
 ('poisson-v1',   'goals',  0.30, now(), TRUE),
 ('market-novig', 'result', 0.00, now(), TRUE);

-- Usuario de desarrollo (id fijo)
INSERT INTO users (id, email, display_name, birth_date, country)
VALUES ('00000000-0000-4000-8000-000000000001', 'demo@example.com', 'Demo', DATE '1992-04-10', 'ES');
INSERT INTO bankrolls (user_id, initial_amount, current_amount)
VALUES ('00000000-0000-4000-8000-000000000001', 1000, 1000);
INSERT INTO notification_preferences (user_id, kind, enabled)
SELECT '00000000-0000-4000-8000-000000000001', k, k <> 'pick_settled'
FROM unnest(enum_range(NULL::alert_kind)) k;

REFRESH MATERIALIZED VIEW mv_transparency;
