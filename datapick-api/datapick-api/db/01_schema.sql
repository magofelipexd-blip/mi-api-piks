-- =====================================================================
--  PRONOSTICOS FUTBOL · Esquema PostgreSQL 16 + TimescaleDB
--  Orden: catalogo -> futbol -> cuotas -> modelos -> picks -> combinadas
--         -> usuarios/bankroll -> notificaciones -> transparencia
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------------
-- Tipos enumerados
-- ---------------------------------------------------------------------
CREATE TYPE match_status    AS ENUM ('scheduled','lineups_confirmed','live','finished','postponed','cancelled');
CREATE TYPE injury_status   AS ENUM ('out','doubtful','suspended','returning');
CREATE TYPE market_category AS ENUM ('result','goals','corners','cards','shots_team','shots_player','player_other','handicap','other');
CREATE TYPE pick_tier       AS ENUM ('favorito','segura');
CREATE TYPE pick_status     AS ENUM ('published','no_value','void','won','lost','push','half_won','half_lost');
CREATE TYPE parlay_status   AS ENUM ('published','won','lost','void','partially_void');
CREATE TYPE calc_trigger    AS ENUM ('scheduled','lineups_confirmed','injury_update','odds_move','manual');
CREATE TYPE alert_kind      AS ENUM ('steam_move','drift','value_lost','value_gained','lineup_change','pick_settled','new_pick','new_parlay');

-- ---------------------------------------------------------------------
-- 1. Catalogo de competicion
-- ---------------------------------------------------------------------
CREATE TABLE leagues (
    id              SERIAL PRIMARY KEY,
    provider_id     INT UNIQUE,                     -- id en API-Football / Sportmonks
    espn_code       TEXT UNIQUE,                    -- 'eng.1', 'uefa.champions'
    name            TEXT NOT NULL,
    country         TEXT NOT NULL,
    strength        NUMERIC(4,3) NOT NULL DEFAULT 1, -- coeficiente de liga para cruces europeos
    liquidity_rank  SMALLINT,                       -- 1 = mas liquida
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE seasons (
    id          SERIAL PRIMARY KEY,
    league_id   INT NOT NULL REFERENCES leagues(id),
    label       TEXT NOT NULL,                      -- '2026/27'
    starts_on   DATE NOT NULL,
    ends_on     DATE NOT NULL,
    is_current  BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (league_id, label)
);

CREATE TABLE teams (
    id          SERIAL PRIMARY KEY,
    provider_id INT UNIQUE,
    espn_id     INT UNIQUE,
    name        TEXT NOT NULL,
    short_name  TEXT,
    country     TEXT,
    logo_url    TEXT
);

CREATE TABLE players (
    id            SERIAL PRIMARY KEY,
    provider_id   INT UNIQUE,
    name          TEXT NOT NULL,
    position      TEXT CHECK (position IN ('GK','DF','MF','FW')),
    birth_date    DATE,
    current_team  INT REFERENCES teams(id)
);

CREATE TABLE referees (
    id                SERIAL PRIMARY KEY,
    provider_id       INT UNIQUE,
    name              TEXT NOT NULL,
    cards_per_match   NUMERIC(4,2),                 -- media movil, la actualiza el ETL
    fouls_per_match   NUMERIC(5,2)
);

-- ---------------------------------------------------------------------
-- 2. Partidos y contexto
-- ---------------------------------------------------------------------
CREATE TABLE matches (
    id              BIGSERIAL PRIMARY KEY,
    provider_id     BIGINT UNIQUE,
    espn_id         BIGINT UNIQUE,
    season_id       INT NOT NULL REFERENCES seasons(id),
    venue           TEXT,
    home_team_id    INT NOT NULL REFERENCES teams(id),
    away_team_id    INT NOT NULL REFERENCES teams(id),
    referee_id      INT REFERENCES referees(id),
    kickoff_at      TIMESTAMPTZ NOT NULL,
    round           TEXT,
    status          match_status NOT NULL DEFAULT 'scheduled',
    lineups_at      TIMESTAMPTZ,                    -- cuando se confirmaron
    home_goals      SMALLINT,
    away_goals      SMALLINT,
    CHECK (home_team_id <> away_team_id)
);
CREATE INDEX ON matches (kickoff_at);
CREATE INDEX ON matches (status, kickoff_at);

-- Dias ya importados de cada competicion (para no repetir peticiones al proveedor)
CREATE TABLE etl_days (
    league_id   INT NOT NULL REFERENCES leagues(id),
    day         DATE NOT NULL,
    complete    BOOLEAN NOT NULL DEFAULT FALSE,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, day)
);

-- Estadisticas finales por equipo y partido (alimentan modelos y liquidacion)
CREATE TABLE match_team_stats (
    match_id        BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    team_id         INT NOT NULL REFERENCES teams(id),
    is_home         BOOLEAN NOT NULL,
    goals           SMALLINT,
    xg              NUMERIC(4,2),
    shots           SMALLINT,
    shots_on_target SMALLINT,
    corners         SMALLINT,
    yellow_cards    SMALLINT,
    red_cards       SMALLINT,
    fouls           SMALLINT,
    possession      NUMERIC(4,1),
    PRIMARY KEY (match_id, team_id)
);

CREATE TABLE match_player_stats (
    match_id        BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id       INT NOT NULL REFERENCES players(id),
    team_id         INT NOT NULL REFERENCES teams(id),
    started         BOOLEAN NOT NULL,
    minutes         SMALLINT NOT NULL DEFAULT 0,
    goals           SMALLINT DEFAULT 0,
    assists         SMALLINT DEFAULT 0,
    shots           SMALLINT DEFAULT 0,
    shots_on_target SMALLINT DEFAULT 0,
    xg              NUMERIC(4,2),
    xa              NUMERIC(4,2),
    yellow_cards    SMALLINT DEFAULT 0,
    fouls           SMALLINT DEFAULT 0,
    PRIMARY KEY (match_id, player_id)
);
CREATE INDEX ON match_player_stats (player_id);

-- Importancia del partido (Monte Carlo de temporada)
CREATE TABLE match_importance (
    match_id            BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    team_id             INT NOT NULL REFERENCES teams(id),
    importance_index    NUMERIC(5,4) NOT NULL CHECK (importance_index BETWEEN 0 AND 1),
    objective           TEXT,                       -- 'title','ucl','relegation',...
    delta_win_vs_loss   NUMERIC(5,4),               -- swing de probabilidad del objetivo
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, team_id)
);

-- Ratings de equipo versionados en el tiempo (calidad y forma)
CREATE TABLE team_ratings (
    team_id         INT NOT NULL REFERENCES teams(id),
    as_of           TIMESTAMPTZ NOT NULL,
    attack          NUMERIC(6,4) NOT NULL,
    defense         NUMERIC(6,4) NOT NULL,
    form_xg_delta   NUMERIC(6,4),                   -- forma: xG reciente vs largo plazo
    elo             NUMERIC(7,2),
    model_version   TEXT NOT NULL,
    PRIMARY KEY (team_id, as_of)
);

-- Bajas y lesionados
CREATE TABLE injuries (
    id              BIGSERIAL PRIMARY KEY,
    player_id       INT NOT NULL REFERENCES players(id),
    team_id         INT NOT NULL REFERENCES teams(id),
    status          injury_status NOT NULL,
    reason          TEXT,
    reported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expected_return DATE,
    impact_xg90     NUMERIC(5,3),                   -- perdida estimada de xG+xA por 90'
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX ON injuries (team_id) WHERE resolved_at IS NULL;

-- Probabilidad de titularidad (antes de alineaciones)
CREATE TABLE player_start_probabilities (
    match_id         BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id        INT NOT NULL REFERENCES players(id),
    p_start          NUMERIC(5,4) NOT NULL CHECK (p_start BETWEEN 0 AND 1),
    expected_minutes NUMERIC(5,2),
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_version    TEXT NOT NULL,
    PRIMARY KEY (match_id, player_id, computed_at)
);

-- Alineaciones oficiales
CREATE TABLE lineups (
    match_id     BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    team_id      INT NOT NULL REFERENCES teams(id),
    player_id    INT NOT NULL REFERENCES players(id),
    is_starter   BOOLEAN NOT NULL,
    position     TEXT,
    shirt        SMALLINT,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, player_id)
);

-- ---------------------------------------------------------------------
-- 3. Casas, mercados y cuotas (series temporales)
-- ---------------------------------------------------------------------
CREATE TABLE bookmakers (
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,               -- 'pinnacle','betano','bet365'
    name        TEXT NOT NULL,
    is_sharp    BOOLEAN NOT NULL DEFAULT FALSE,     -- referencia para p_mercado
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

-- Catalogo de mercados estilo Betano
CREATE TABLE market_types (
    id               SERIAL PRIMARY KEY,
    code             TEXT UNIQUE NOT NULL,          -- 'TOTAL_CORNERS','PLAYER_SHOTS_OT'
    name             TEXT NOT NULL,
    category         market_category NOT NULL,
    has_line         BOOLEAN NOT NULL DEFAULT FALSE,
    is_player_market BOOLEAN NOT NULL DEFAULT FALSE,
    outcomes         TEXT[] NOT NULL                -- {'over','under'} | {'home','draw','away'}
);

-- Seleccion canonica: una apuesta concreta de un partido.
-- Ej: (partido 10, TOTAL_CORNERS, linea 9.5, 'over', sin jugador)
CREATE TABLE selections (
    id              BIGSERIAL PRIMARY KEY,
    match_id        BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    market_type_id  INT NOT NULL REFERENCES market_types(id),
    line            NUMERIC(6,2),
    outcome         TEXT NOT NULL,
    player_id       INT REFERENCES players(id),
    team_id         INT REFERENCES teams(id),       -- para mercados por equipo
    label           TEXT NOT NULL                   -- 'Mas de 9.5 corners'
);
CREATE UNIQUE INDEX selections_uq ON selections
    (match_id, market_type_id, COALESCE(line,-999), outcome, COALESCE(player_id,0), COALESCE(team_id,0));

-- Hypertable: cada cambio de cuota observado
CREATE TABLE odds_snapshots (
    ts              TIMESTAMPTZ NOT NULL,
    selection_id    BIGINT NOT NULL REFERENCES selections(id) ON DELETE CASCADE,
    bookmaker_id    INT NOT NULL REFERENCES bookmakers(id),
    price           NUMERIC(7,3) NOT NULL CHECK (price > 1),
    is_suspended    BOOLEAN NOT NULL DEFAULT FALSE
);
SELECT create_hypertable('odds_snapshots', 'ts', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ON odds_snapshots (selection_id, bookmaker_id, ts DESC);
ALTER TABLE odds_snapshots SET (timescaledb.compress, timescaledb.compress_segmentby = 'selection_id, bookmaker_id');
SELECT add_compression_policy('odds_snapshots', INTERVAL '7 days');

-- Velas de 5 minutos para el grafico de Odds Movement
CREATE MATERIALIZED VIEW odds_5m
WITH (timescaledb.continuous) AS
SELECT time_bucket('5 minutes', ts) AS bucket,
       selection_id, bookmaker_id,
       first(price, ts) AS open,
       max(price)       AS high,
       min(price)       AS low,
       last(price, ts)  AS close
FROM odds_snapshots
GROUP BY bucket, selection_id, bookmaker_id
WITH NO DATA;
SELECT add_continuous_aggregate_policy('odds_5m',
    start_offset => INTERVAL '2 days', end_offset => INTERVAL '5 minutes', schedule_interval => INTERVAL '5 minutes');

-- Ultima cuota por seleccion y casa (lectura rapida para la app)
CREATE TABLE odds_latest (
    selection_id    BIGINT NOT NULL REFERENCES selections(id) ON DELETE CASCADE,
    bookmaker_id    INT NOT NULL REFERENCES bookmakers(id),
    price           NUMERIC(7,3) NOT NULL,
    opening_price   NUMERIC(7,3) NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (selection_id, bookmaker_id)
);

CREATE OR REPLACE FUNCTION trg_odds_latest() RETURNS trigger AS $$
BEGIN
    INSERT INTO odds_latest (selection_id, bookmaker_id, price, opening_price, updated_at)
    VALUES (NEW.selection_id, NEW.bookmaker_id, NEW.price, NEW.price, NEW.ts)
    ON CONFLICT (selection_id, bookmaker_id) DO UPDATE
       SET price = EXCLUDED.price, updated_at = EXCLUDED.updated_at
     WHERE odds_latest.updated_at <= EXCLUDED.updated_at;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER odds_latest_upsert AFTER INSERT ON odds_snapshots
    FOR EACH ROW EXECUTE FUNCTION trg_odds_latest();

-- ---------------------------------------------------------------------
-- 4. Modelos y predicciones
-- ---------------------------------------------------------------------
CREATE TABLE model_versions (
    code            TEXT PRIMARY KEY,               -- 'corners-negbin-v3'
    category        market_category NOT NULL,
    model_weight    NUMERIC(4,3) NOT NULL CHECK (model_weight BETWEEN 0 AND 1),
                    -- w en p_final = w*p_modelo + (1-w)*p_mercado
    brier_backtest  NUMERIC(6,5),
    trained_at      TIMESTAMPTZ NOT NULL,
    mlflow_run_id   TEXT,
    is_live         BOOLEAN NOT NULL DEFAULT FALSE
);

-- Cada calculo de probabilidad (historico completo, nunca se sobreescribe)
CREATE TABLE predictions (
    id              BIGSERIAL PRIMARY KEY,
    selection_id    BIGINT NOT NULL REFERENCES selections(id) ON DELETE CASCADE,
    model_version   TEXT NOT NULL REFERENCES model_versions(code),
    p_model         NUMERIC(6,5) NOT NULL CHECK (p_model BETWEEN 0 AND 1),
    p_market        NUMERIC(6,5) CHECK (p_market BETWEEN 0 AND 1),   -- sharp sin margen
    p_final         NUMERIC(6,5) NOT NULL CHECK (p_final BETWEEN 0 AND 1),
    p_start_used    NUMERIC(5,4),                   -- solo props de jugador
    trigger         calc_trigger NOT NULL,
    features        JSONB,                          -- snapshot de inputs (auditoria)
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- props de jugador antes de alineacion: titularidad >= 85%
    CHECK (p_start_used IS NULL OR trigger = 'lineups_confirmed' OR p_start_used >= 0.85)
);
CREATE INDEX ON predictions (selection_id, computed_at DESC);

-- Probabilidad conjunta Bet Builder (simulador de partido)
CREATE TABLE joint_predictions (
    id              BIGSERIAL PRIMARY KEY,
    match_id        BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    selection_ids   BIGINT[] NOT NULL,
    p_joint         NUMERIC(6,5) NOT NULL,
    p_independent   NUMERIC(6,5) NOT NULL,          -- producto simple, para ver correlacion
    n_simulations   INT NOT NULL,
    bookmaker_id    INT REFERENCES bookmakers(id),  -- casa que ofrece el Bet Builder
    offered_odds    NUMERIC(7,3) CHECK (offered_odds > 1),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 5. Picks (ledger publico e inmutable)
-- ---------------------------------------------------------------------
CREATE TABLE picks (
    id              BIGSERIAL PRIMARY KEY,
    selection_id    BIGINT NOT NULL REFERENCES selections(id),
    prediction_id   BIGINT NOT NULL REFERENCES predictions(id),
    bookmaker_id    INT NOT NULL REFERENCES bookmakers(id),
    tier            pick_tier NOT NULL,
    probability     NUMERIC(6,5) NOT NULL,
    odds            NUMERIC(7,3) NOT NULL CHECK (odds > 1),
    ev              NUMERIC(8,5) GENERATED ALWAYS AS (probability * odds - 1) STORED,
    kelly_full      NUMERIC(8,5) GENERATED ALWAYS AS ((probability * odds - 1) / (odds - 1)) STORED,
    stake_pct       NUMERIC(5,4) NOT NULL CHECK (stake_pct BETWEEN 0 AND 0.03),
    status          pick_status NOT NULL DEFAULT 'published',
    published_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    kickoff_at      TIMESTAMPTZ NOT NULL,
    closing_odds    NUMERIC(7,3),
    clv             NUMERIC(8,5) GENERATED ALWAYS AS (odds / NULLIF(closing_odds,0) - 1) STORED,
    profit_units    NUMERIC(8,4),                   -- por 1 unidad apostada, al liquidar
    settled_at      TIMESTAMPTZ,
    superseded_by   BIGINT REFERENCES picks(id),    -- el recalculo T-60 publica un pick nuevo
    prev_hash       TEXT,
    row_hash        TEXT,
    -- Reglas de negocio
    CHECK (published_at < kickoff_at),
    CHECK (probability * odds > 1),                 -- +EV obligatorio
    CHECK (tier <> 'favorito' OR (probability >= 0.70 AND probability < 0.80 AND odds >= 1.50)),
    CHECK (tier <> 'segura'   OR (probability >= 0.80))
);
CREATE INDEX ON picks (kickoff_at);
CREATE INDEX ON picks (tier, status);

-- Hash encadenado: cada pick firma el anterior; cualquier edicion rompe la cadena
CREATE OR REPLACE FUNCTION pick_digest(p picks) RETURNS TEXT AS $$
    SELECT encode(digest(concat_ws('|', p.prev_hash, p.selection_id, p.bookmaker_id, p.tier,
                  p.probability, p.odds, p.stake_pct,
                  to_char(p.published_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                  to_char(p.kickoff_at   AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')),
           'sha256'), 'hex');
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION trg_pick_hash() RETURNS trigger AS $$
DECLARE last_hash TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('picks_ledger'));
    SELECT row_hash INTO last_hash FROM picks ORDER BY id DESC LIMIT 1;
    NEW.prev_hash := COALESCE(last_hash, 'GENESIS');
    NEW.row_hash  := pick_digest(NEW);
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER pick_hash BEFORE INSERT ON picks FOR EACH ROW EXECUTE FUNCTION trg_pick_hash();

-- Inmutabilidad: tras publicar solo se pueden tocar los campos de liquidacion
CREATE OR REPLACE FUNCTION trg_pick_immutable() RETURNS trigger AS $$
BEGIN
    IF (NEW.selection_id, NEW.bookmaker_id, NEW.tier, NEW.probability, NEW.odds,
        NEW.stake_pct, NEW.published_at, NEW.kickoff_at, NEW.prev_hash, NEW.row_hash)
       IS DISTINCT FROM
       (OLD.selection_id, OLD.bookmaker_id, OLD.tier, OLD.probability, OLD.odds,
        OLD.stake_pct, OLD.published_at, OLD.kickoff_at, OLD.prev_hash, OLD.row_hash) THEN
        RAISE EXCEPTION 'picks es un ledger inmutable: solo se puede liquidar (pick %)', OLD.id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER pick_immutable BEFORE UPDATE ON picks FOR EACH ROW EXECUTE FUNCTION trg_pick_immutable();

CREATE OR REPLACE FUNCTION trg_no_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'No se pueden borrar registros de %', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER pick_no_delete BEFORE DELETE ON picks FOR EACH ROW EXECUTE FUNCTION trg_no_delete();

-- ---------------------------------------------------------------------
-- 6. Combinadas (5.00x - 7.00x) con soporte Bet Builder
-- ---------------------------------------------------------------------
CREATE TABLE parlays (
    id                  BIGSERIAL PRIMARY KEY,
    total_odds          NUMERIC(8,3) NOT NULL CHECK (total_odds BETWEEN 5.00 AND 7.00),
    joint_probability   NUMERIC(6,5) NOT NULL,
    ev                  NUMERIC(8,5) GENERATED ALWAYS AS (joint_probability * total_odds - 1) STORED,
    stake_pct           NUMERIC(5,4) NOT NULL CHECK (stake_pct BETWEEN 0 AND 0.01),
    rank_of_day         SMALLINT NOT NULL,          -- 1 = mejor combinada del dia
    status              parlay_status NOT NULL DEFAULT 'published',
    published_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_kickoff_at    TIMESTAMPTZ NOT NULL,
    profit_units        NUMERIC(8,4),
    solver_meta         JSONB,                      -- objetivo, tiempo CP-SAT, candidatos
    CHECK (joint_probability * total_odds > 1),
    CHECK (published_at < first_kickoff_at)
);
CREATE TRIGGER parlay_no_delete BEFORE DELETE ON parlays FOR EACH ROW EXECUTE FUNCTION trg_no_delete();

-- Un "leg" es un pick simple o un bloque Bet Builder del mismo partido
CREATE TABLE parlay_legs (
    id                  BIGSERIAL PRIMARY KEY,
    parlay_id           BIGINT NOT NULL REFERENCES parlays(id),
    match_id            BIGINT NOT NULL REFERENCES matches(id),
    is_bet_builder      BOOLEAN NOT NULL DEFAULT FALSE,
    joint_prediction_id BIGINT REFERENCES joint_predictions(id),
    leg_odds            NUMERIC(7,3) NOT NULL CHECK (leg_odds > 1),
    leg_probability     NUMERIC(6,5) NOT NULL,
    UNIQUE (parlay_id, match_id),                   -- max. 1 leg por partido
    CHECK (NOT is_bet_builder OR joint_prediction_id IS NOT NULL)
);

CREATE TABLE parlay_leg_selections (
    leg_id          BIGINT NOT NULL REFERENCES parlay_legs(id) ON DELETE CASCADE,
    selection_id    BIGINT NOT NULL REFERENCES selections(id),
    pick_id         BIGINT REFERENCES picks(id),    -- si la seleccion es un pick publicado
    PRIMARY KEY (leg_id, selection_id)
);

-- ---------------------------------------------------------------------
-- 7. Usuarios, bankroll y apuestas del usuario
-- ---------------------------------------------------------------------
CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               CITEXT UNIQUE NOT NULL,
    display_name        TEXT,
    birth_date          DATE NOT NULL,
    country             CHAR(2) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    self_excluded_until TIMESTAMPTZ,                -- juego responsable
    is_anonymous        BOOLEAN NOT NULL DEFAULT FALSE  -- cuenta creada por un dispositivo
);

-- Mayoria de edad: se valida con trigger porque CURRENT_DATE no es inmutable
CREATE OR REPLACE FUNCTION trg_user_adult() RETURNS trigger AS $$
BEGIN
    IF NEW.birth_date > (CURRENT_DATE - INTERVAL '18 years') THEN
        RAISE EXCEPTION 'El usuario debe ser mayor de 18 anos';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER user_adult BEFORE INSERT OR UPDATE OF birth_date ON users
    FOR EACH ROW EXECUTE FUNCTION trg_user_adult();

-- Tokens de dispositivo: cada instalación de la app recibe uno al registrarse.
-- Solo se guarda el hash SHA-256; el token en claro solo lo conoce el teléfono.
CREATE TABLE auth_tokens (
    token_hash   TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX ON auth_tokens (user_id);

CREATE TABLE bankrolls (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    currency            CHAR(3) NOT NULL DEFAULT 'EUR',
    initial_amount      NUMERIC(12,2) NOT NULL CHECK (initial_amount > 0),
    current_amount      NUMERIC(12,2) NOT NULL,
    kelly_fraction      NUMERIC(3,2) NOT NULL DEFAULT 0.25 CHECK (kelly_fraction BETWEEN 0.05 AND 0.50),
    max_single_pct      NUMERIC(4,3) NOT NULL DEFAULT 0.030,
    max_parlay_pct      NUMERIC(4,3) NOT NULL DEFAULT 0.010,
    max_daily_exposure  NUMERIC(4,3) NOT NULL DEFAULT 0.100,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_bets (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pick_id         BIGINT REFERENCES picks(id),
    parlay_id       BIGINT REFERENCES parlays(id),
    stake           NUMERIC(12,2) NOT NULL CHECK (stake > 0),
    odds_taken      NUMERIC(8,3) NOT NULL CHECK (odds_taken > 1),
    placed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    profit          NUMERIC(12,2),
    CHECK ((pick_id IS NULL) <> (parlay_id IS NULL))
);
CREATE INDEX ON user_bets (user_id, placed_at DESC);

-- ---------------------------------------------------------------------
-- 8. Alertas de cuotas y notificaciones push
-- ---------------------------------------------------------------------
CREATE TABLE odds_alerts (
    id              BIGSERIAL PRIMARY KEY,
    selection_id    BIGINT NOT NULL REFERENCES selections(id),
    bookmaker_id    INT NOT NULL REFERENCES bookmakers(id),
    kind            alert_kind NOT NULL,
    price_before    NUMERIC(7,3) NOT NULL,
    price_after     NUMERIC(7,3) NOT NULL,
    window_minutes  SMALLINT NOT NULL,
    pct_change      NUMERIC(8,5) GENERATED ALWAYS AS (price_after / price_before - 1) STORED,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON odds_alerts (detected_at DESC);

CREATE TABLE devices (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    push_token      TEXT UNIQUE NOT NULL,           -- Expo / FCM / APNs
    platform        TEXT NOT NULL CHECK (platform IN ('ios','android','web')),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notification_preferences (
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            alert_kind NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (user_id, kind)
);

CREATE TABLE notifications (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            alert_kind NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    payload         JSONB,                          -- {pick_id, parlay_id, match_id}
    sent_at         TIMESTAMPTZ,
    read_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON notifications (user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- 9. Vistas para la app y el panel publico de transparencia
-- ---------------------------------------------------------------------
-- Picks vigentes de hoy, listos para la pantalla principal
CREATE VIEW v_active_picks AS
SELECT p.id, p.tier, p.probability, p.odds, p.ev, p.stake_pct, p.kickoff_at, p.published_at,
       s.label AS selection, mt.code AS market, mt.category,
       th.name AS home, ta.name AS away, l.name AS league,
       ol.price AS current_odds, ol.opening_price,
       (ol.price * p.probability - 1) AS current_ev
FROM picks p
JOIN selections s    ON s.id = p.selection_id
JOIN market_types mt ON mt.id = s.market_type_id
JOIN matches m       ON m.id = s.match_id
JOIN teams th        ON th.id = m.home_team_id
JOIN teams ta        ON ta.id = m.away_team_id
JOIN seasons se      ON se.id = m.season_id
JOIN leagues l       ON l.id = se.league_id
LEFT JOIN odds_latest ol ON ol.selection_id = p.selection_id AND ol.bookmaker_id = p.bookmaker_id
WHERE p.status = 'published' AND p.superseded_by IS NULL AND p.kickoff_at > now();

CREATE VIEW v_settled_picks AS
SELECT p.*, mt.category, l.name AS league
FROM picks p
JOIN selections s    ON s.id = p.selection_id
JOIN market_types mt ON mt.id = s.market_type_id
JOIN matches m       ON m.id = s.match_id
JOIN seasons se      ON se.id = m.season_id
JOIN leagues l       ON l.id = se.league_id
WHERE p.status IN ('won','lost','push','half_won','half_lost')
  AND p.superseded_by IS NULL;

CREATE MATERIALIZED VIEW mv_transparency AS
SELECT COALESCE(tier::text, 'total')                                          AS tier,
       count(*)                                                               AS picks,
       count(*) FILTER (WHERE status IN ('won','half_won'))                   AS won,
       COALESCE(round(avg((status IN ('won','half_won'))::int), 4), 0)        AS win_rate,
       COALESCE(round(sum(profit_units), 2), 0)                               AS profit_units,
       COALESCE(round(sum(profit_units) / NULLIF(count(*),0), 4), 0)          AS yield,
       COALESCE(round(avg(odds), 3), 0)                                       AS avg_odds,
       COALESCE(round(avg(probability), 4), 0)                                AS avg_probability,
       round(avg(clv), 4)                                                     AS avg_clv,
       COALESCE(round(avg(power(probability - (status = 'won')::int, 2)), 5), 0) AS brier
FROM v_settled_picks
GROUP BY ROLLUP (tier);

-- Calibracion: probabilidad prometida vs frecuencia real, en tramos de 5 puntos
CREATE VIEW v_calibration AS
SELECT floor(probability * 20) / 20                 AS bucket_from,
       count(*)                                     AS n,
       round(avg(probability), 4)                   AS promised,
       round(avg((status = 'won')::int), 4)         AS observed
FROM v_settled_picks
WHERE status IN ('won','lost')
GROUP BY 1 ORDER BY 1;

-- Verificacion publica de la cadena de hashes
CREATE VIEW v_ledger_integrity AS
SELECT p.id,
       p.row_hash = pick_digest(p)                                        AS hash_ok,
       p.prev_hash = COALESCE(lag(p.row_hash) OVER (ORDER BY p.id), 'GENESIS') AS chain_ok
FROM picks p;
