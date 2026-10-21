"""Operaciones que escriben en la base: generar combinadas y liquidar picks."""

import json

import asyncpg

from .betting import Leg, ParlayResult, optimize_parlays, settle_profit
from .config import settings
from .queries import ACTIVE_PICKS


async def load_candidate_legs(conn: asyncpg.Connection) -> list[Leg]:
    picks = await conn.fetch(ACTIVE_PICKS)
    legs = [
        Leg(
            key=f"pick-{p['id']}",
            match_id=p["match_id"],
            title=f"{p['home']} vs {p['away']}",
            selections=(p["selection"],),
            selection_ids=(p["selection_id"],),
            odds=p["current_odds"],
            probability=p["probability"],
            pick_ids=(p["id"],),
        )
        for p in picks
    ]

    # Bloques Bet Builder con cuota ofrecida y valor positivo
    builders = await conn.fetch(
        """
        SELECT DISTINCT ON (j.match_id, j.selection_ids)
               j.id, j.match_id, j.selection_ids, j.p_joint, j.offered_odds,
               th.name AS home, ta.name AS away
        FROM joint_predictions j
        JOIN matches m ON m.id = j.match_id
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.kickoff_at > now() AND j.offered_odds IS NOT NULL AND j.p_joint * j.offered_odds > 1
        ORDER BY j.match_id, j.selection_ids, j.computed_at DESC
        """
    )
    for b in builders:
        sels = await conn.fetch(
            """
            SELECT s.id, s.label, p.id AS pick_id
            FROM unnest($1::bigint[]) WITH ORDINALITY u(sid, ord)
            JOIN selections s ON s.id = u.sid
            LEFT JOIN picks p ON p.selection_id = s.id AND p.status = 'published' AND p.superseded_by IS NULL
            ORDER BY u.ord
            """,
            b["selection_ids"],
        )
        legs.append(
            Leg(
                key=f"bb-{b['id']}",
                match_id=b["match_id"],
                title=f"{b['home']} vs {b['away']}",
                selections=tuple(s["label"] for s in sels),
                selection_ids=tuple(s["id"] for s in sels),
                odds=b["offered_odds"],
                probability=b["p_joint"],
                is_bet_builder=True,
                pick_ids=tuple(s["pick_id"] for s in sels),
                joint_prediction_id=b["id"],
            )
        )
    return legs


async def generate_parlays(conn: asyncpg.Connection, include_bet_builder: bool = True) -> list[int]:
    """Calcula y publica las combinadas del día. Devuelve los ids creados."""
    legs = await load_candidate_legs(conn)
    if not include_bet_builder:
        legs = [l for l in legs if not l.is_bet_builder]
    results: list[ParlayResult] = optimize_parlays(
        legs,
        min_odds=settings.parlay_min_odds,
        max_odds=settings.parlay_max_odds,
        top=settings.parlay_top_n,
    )
    kickoffs = {
        r["id"]: r["kickoff_at"]
        for r in await conn.fetch("SELECT id, kickoff_at FROM matches WHERE id = ANY($1::bigint[])", [l.match_id for l in legs])
    }

    created: list[int] = []
    async with conn.transaction():
        for rank, res in enumerate(results, start=1):
            parlay_id = await conn.fetchval(
                """
                INSERT INTO parlays (total_odds, joint_probability, stake_pct, rank_of_day, first_kickoff_at, solver_meta)
                VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
                """,
                round(res.total_odds, 3),
                round(res.probability, 5),
                round(res.stake_pct, 4),
                rank,
                min(kickoffs[l.match_id] for l in res.legs),
                json.dumps({"solver": "dfs", "candidates": len(legs), "include_bet_builder": include_bet_builder}),
            )
            for leg in res.legs:
                leg_id = await conn.fetchval(
                    """
                    INSERT INTO parlay_legs (parlay_id, match_id, is_bet_builder, joint_prediction_id, leg_odds, leg_probability)
                    VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
                    """,
                    parlay_id, leg.match_id, leg.is_bet_builder, leg.joint_prediction_id, leg.odds, leg.probability,
                )
                await conn.executemany(
                    "INSERT INTO parlay_leg_selections (leg_id, selection_id, pick_id) VALUES ($1, $2, $3)",
                    [(leg_id, sid, pid) for sid, pid in zip(leg.selection_ids, leg.pick_ids)],
                )
            created.append(parlay_id)
    return created


async def settle_pick(conn: asyncpg.Connection, pick_id: int, result: str, closing_odds: float | None) -> dict:
    row = await conn.fetchrow("SELECT odds, status::text FROM picks WHERE id = $1", pick_id)
    if row is None:
        raise LookupError(pick_id)
    if row["status"] != "published":
        raise ValueError(f"El pick ya está en estado {row['status']}")
    profit = settle_profit(result, row["odds"])
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE picks SET status = $2::pick_status, profit_units = $3,
                   closing_odds = COALESCE($4, closing_odds), settled_at = now()
            WHERE id = $1
            """,
            pick_id, result, profit, closing_odds,
        )
    await conn.execute("REFRESH MATERIALIZED VIEW mv_transparency")
    return {"id": pick_id, "status": result, "profitUnits": profit}
