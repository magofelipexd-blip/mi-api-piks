"""Del dato a la decisión: predicciones, picks, liquidación y combinadas."""

from __future__ import annotations

import json
import logging

import asyncpg

from .betting import classify, expected_value, settle_profit, stake_pct
from .config import settings
from .etl.espn import DRAFTKINGS_ID
from .model.poisson import Fit, Result, expected_goals, fit, no_vig, outcome_probabilities

log = logging.getLogger("pipeline")

DOMESTIC = (1, 2, 3, 4, 5)  # ids de ligas nacionales: alimentan los ratings
PUBLISH_HORIZON_HOURS = 72  # se publican picks de partidos de los próximos 3 días


async def fit_model(conn: asyncpg.Connection) -> Fit:
    rows = await conn.fetch(
        """
        SELECT se.league_id, m.home_team_id, m.away_team_id, m.home_goals, m.away_goals
        FROM matches m JOIN seasons se ON se.id = m.season_id
        WHERE m.status = 'finished' AND m.home_goals IS NOT NULL AND se.league_id = ANY($1::int[])
          AND m.kickoff_at > now() - INTERVAL '400 days'
        """,
        list(DOMESTIC),
    )
    strengths = {r["id"]: r["strength"] for r in await conn.fetch("SELECT id, strength FROM leagues")}
    return fit([Result(r["league_id"], r["home_team_id"], r["away_team_id"], r["home_goals"], r["away_goals"])
                for r in rows], strengths)


def _reasons(home: str, away: str, outcome: str, lam: tuple[float, float] | None, f: Fit | None,
             home_id: int, away_id: int, p_model: float | None, p_market: float, line: float | None) -> list[str]:
    out = []
    if lam:
        out.append(f"Goles esperados por el modelo: {home} {lam[0]:.2f} · {away} {lam[1]:.2f}")
    if f:
        th, ta = f.teams.get(home_id), f.teams.get(away_id)
        if th and ta:
            out.append(f"Ataque {home} {th.attack:.2f}× la media, defensa {away} {ta.defense:.2f}× ({th.games} y {ta.games} partidos)")
    if p_model is not None:
        out.append(f"Modelo Poisson {p_model:.0%} frente a mercado DraftKings sin margen {p_market:.0%}")
    if line is not None and outcome in ("over", "under"):
        out.append(f"Línea de goles {line:g}")
    return out


async def predict_and_publish(conn: asyncpg.Connection, f: Fit) -> dict[str, int]:
    weight = await conn.fetchval("SELECT model_weight FROM model_versions WHERE code = 'poisson-v1'") or 0.3
    matches = await conn.fetch(
        """
        SELECT m.id, m.home_team_id, m.away_team_id, m.kickoff_at, se.league_id, th.name AS home, ta.name AS away
        FROM matches m JOIN seasons se ON se.id = m.season_id
        JOIN teams th ON th.id = m.home_team_id JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.status IN ('scheduled', 'lineups_confirmed')
          AND m.kickoff_at BETWEEN now() + INTERVAL '10 minutes' AND now() + INTERVAL '8 days'
        """
    )
    stats = {"predictions": 0, "picks": 0}
    for m in matches:
        sels = await conn.fetch(
            """
            SELECT s.id, s.market_type_id, s.outcome, s.line, s.label, ol.price
            FROM selections s JOIN odds_latest ol ON ol.selection_id = s.id AND ol.bookmaker_id = $2
            WHERE s.match_id = $1 AND s.market_type_id IN (1, 3)
            """,
            m["id"], DRAFTKINGS_ID,
        )
        result_sels = {s["outcome"]: s for s in sels if s["market_type_id"] == 1}
        total_sels = {s["outcome"]: s for s in sels if s["market_type_id"] == 3}
        lam = expected_goals(f, m["home_team_id"], m["away_team_id"], m["league_id"])

        groups = []
        if len(result_sels) == 3:
            groups.append((result_sels, None))
        if len(total_sels) == 2:
            groups.append((total_sels, float(next(iter(total_sels.values()))["line"])))

        for sel_map, line in groups:
            market = no_vig({k: s["price"] for k, s in sel_map.items()})
            model = outcome_probabilities(*lam, total_line=line or 2.5) if lam else None
            for outcome, s in sel_map.items():
                p_market = market[outcome]
                p_model = model[outcome] if model else None
                p_final = weight * p_model + (1 - weight) * p_market if p_model is not None else p_market
                version = "poisson-v1" if p_model is not None else "market-novig"
                last = await conn.fetchrow(
                    "SELECT id, p_final FROM predictions WHERE selection_id = $1 ORDER BY computed_at DESC LIMIT 1", s["id"]
                )
                if last is None or abs(last["p_final"] - p_final) > 0.002:
                    reasons = _reasons(m["home"], m["away"], outcome, lam, f, m["home_team_id"], m["away_team_id"],
                                       p_model, p_market, line)
                    pred_id = await conn.fetchval(
                        """
                        INSERT INTO predictions (selection_id, model_version, p_model, p_market, p_final, trigger, features)
                        VALUES ($1, $2, $3, $4, $5, 'odds_move', $6) RETURNING id
                        """,
                        s["id"], version, round(p_model if p_model is not None else p_market, 5),
                        round(p_market, 5), round(p_final, 5),
                        json.dumps({"reasons": reasons, "lambda": lam, "price": s["price"]}),
                    )
                    stats["predictions"] += 1
                else:
                    pred_id = last["id"]

                # Publicar solo con modelo propio, dentro del horizonte y si cumple las reglas
                if p_model is None:
                    continue
                hours = (m["kickoff_at"] - await conn.fetchval("SELECT now()")).total_seconds() / 3600
                tier = classify(p_final, s["price"])
                if tier is None or hours > PUBLISH_HORIZON_HOURS or expected_value(p_final, s["price"]) < settings.min_publish_ev:
                    continue
                exists = await conn.fetchval(
                    "SELECT 1 FROM picks WHERE selection_id = $1 AND status = 'published' AND superseded_by IS NULL", s["id"]
                )
                if exists:
                    continue
                await conn.execute(
                    """
                    INSERT INTO picks (selection_id, prediction_id, bookmaker_id, tier, probability, odds, stake_pct, kickoff_at)
                    VALUES ($1, $2, $3, $4::pick_tier, $5, $6, $7, $8)
                    """,
                    s["id"], pred_id, DRAFTKINGS_ID, tier, round(p_final, 5), s["price"],
                    round(stake_pct(p_final, s["price"], 0.03), 4), m["kickoff_at"],
                )
                stats["picks"] += 1
                log.info("Pick %s: %s · %s @ %.2f (p=%.3f)", tier, f"{m['home']} vs {m['away']}", s["label"], s["price"], p_final)
    return stats


def selection_result(market_type_id: int, outcome: str, line: float | None, hg: int, ag: int) -> str:
    if market_type_id == 1:
        actual = "home" if hg > ag else "away" if ag > hg else "draw"
        return "won" if outcome == actual else "lost"
    if market_type_id == 3 and line is not None:
        total = hg + ag
        if total == line:
            return "push"
        return "won" if (total > line) == (outcome == "over") else "lost"
    raise ValueError(f"Mercado {market_type_id} sin regla de liquidación")


async def settle(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        """
        SELECT p.id, p.odds, p.bookmaker_id, p.selection_id, s.market_type_id, s.outcome, s.line,
               m.status::text AS match_status, m.home_goals, m.away_goals, m.kickoff_at
        FROM picks p JOIN selections s ON s.id = p.selection_id JOIN matches m ON m.id = s.match_id
        WHERE p.status = 'published' AND m.status IN ('finished', 'postponed', 'cancelled')
        """
    )
    n = 0
    for r in rows:
        if r["match_status"] == "finished":
            if r["home_goals"] is None:
                continue
            result = selection_result(r["market_type_id"], r["outcome"],
                                      float(r["line"]) if r["line"] is not None else None, r["home_goals"], r["away_goals"])
        else:
            result = "void"
        closing = await conn.fetchval(
            """
            SELECT price FROM odds_snapshots WHERE selection_id = $1 AND bookmaker_id = $2 AND ts <= $3
            ORDER BY ts DESC LIMIT 1
            """,
            r["selection_id"], r["bookmaker_id"], r["kickoff_at"],
        )
        await conn.execute(
            """
            UPDATE picks SET status = $2::pick_status, profit_units = $3, closing_odds = COALESCE($4, closing_odds),
                   settled_at = now()
            WHERE id = $1
            """,
            r["id"], result, settle_profit(result, r["odds"]), closing,
        )
        n += 1

    # Combinadas: perdida si falla un leg, ganada si aciertan todos
    await conn.execute(
        """
        WITH legs AS (
            SELECT pa.id, pa.total_odds,
                   bool_or(p.status = 'lost') AS any_lost,
                   bool_and(p.status IN ('won', 'push', 'void')) AS all_done,
                   bool_and(p.status = 'won') AS all_won
            FROM parlays pa
            JOIN parlay_legs pl ON pl.parlay_id = pa.id
            JOIN parlay_leg_selections x ON x.leg_id = pl.id
            JOIN picks p ON p.id = x.pick_id
            WHERE pa.status = 'published'
            GROUP BY pa.id
        )
        UPDATE parlays pa SET
            status = CASE WHEN l.any_lost THEN 'lost' WHEN l.all_won THEN 'won' ELSE 'partially_void' END::parlay_status,
            profit_units = CASE WHEN l.any_lost THEN -1 WHEN l.all_won THEN pa.total_odds - 1 ELSE 0 END
        FROM legs l
        WHERE pa.id = l.id AND (l.any_lost OR l.all_done)
        """
    )
    if n:
        await conn.execute("REFRESH MATERIALIZED VIEW mv_transparency")
    return n
