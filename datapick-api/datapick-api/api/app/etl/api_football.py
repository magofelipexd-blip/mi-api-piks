"""Importa el calendario real desde API-Football (api-sports.io, v3).

Uso, con la clave en la variable de entorno API_FOOTBALL_KEY:

    python -m app.etl.api_football --days 7
    python -m app.etl.api_football --from 2026-10-24 --days 3 --leagues 39,140

Hace upsert de equipos, árbitros y partidos usando el id del proveedor, así que
se puede ejecutar cada hora sin duplicar nada. Las ligas se buscan por
`leagues.provider_id` y deben tener una temporada marcada como actual.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg
import httpx

from ..config import settings

BASE_URL = "https://v3.football.api-sports.io"

STATUS_MAP = {
    **dict.fromkeys(["TBD", "NS"], "scheduled"),
    **dict.fromkeys(["1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"], "live"),
    **dict.fromkeys(["FT", "AET", "PEN", "AWD", "WO"], "finished"),
    "PST": "postponed",
    **dict.fromkeys(["CANC", "ABD"], "cancelled"),
}


def map_status(short: str | None) -> str:
    return STATUS_MAP.get(short or "", "scheduled")


def season_year(day: date) -> int:
    """API-Football nombra la temporada 2026/27 como 2026."""
    return day.year if day.month >= 7 else day.year - 1


def clean_referee(name: str | None) -> str | None:
    """'Anthony Taylor, England' -> 'Anthony Taylor'."""
    if not name:
        return None
    return name.split(",")[0].strip() or None


def parse_fixture(item: dict[str, Any]) -> dict[str, Any]:
    fx, lg, teams, goals = item["fixture"], item["league"], item["teams"], item.get("goals") or {}
    return {
        "provider_id": fx["id"],
        "kickoff_at": datetime.fromisoformat(fx["date"].replace("Z", "+00:00")),
        "status": map_status((fx.get("status") or {}).get("short")),
        "referee": clean_referee(fx.get("referee")),
        "league_provider_id": lg["id"],
        "round": lg.get("round"),
        "home": {"provider_id": teams["home"]["id"], "name": teams["home"]["name"], "logo": teams["home"].get("logo")},
        "away": {"provider_id": teams["away"]["id"], "name": teams["away"]["name"], "logo": teams["away"].get("logo")},
        "home_goals": goals.get("home"),
        "away_goals": goals.get("away"),
    }


async def fetch_fixtures(client: httpx.AsyncClient, league: int, start: date, end: date) -> list[dict]:
    r = await client.get(
        "/fixtures",
        params={"league": league, "season": season_year(start), "from": start.isoformat(), "to": end.isoformat(),
                "timezone": "UTC"},
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"API-Football: {body['errors']}")
    return [parse_fixture(i) for i in body.get("response", [])]


async def upsert(conn: asyncpg.Connection, fixtures: list[dict]) -> int:
    seasons = {
        r["provider_id"]: r["season_id"]
        for r in await conn.fetch(
            "SELECT l.provider_id, s.id AS season_id FROM leagues l JOIN seasons s ON s.league_id = l.id WHERE s.is_current"
        )
    }
    count = 0
    async with conn.transaction():
        for f in fixtures:
            season_id = seasons.get(f["league_provider_id"])
            if season_id is None:
                continue
            team_ids = []
            for side in ("home", "away"):
                t = f[side]
                team_ids.append(await conn.fetchval(
                    """
                    INSERT INTO teams (provider_id, name, logo_url) VALUES ($1, $2, $3)
                    ON CONFLICT (provider_id) DO UPDATE SET name = EXCLUDED.name, logo_url = EXCLUDED.logo_url
                    RETURNING id
                    """,
                    t["provider_id"], t["name"], t["logo"],
                ))
            referee_id = None
            if f["referee"]:
                referee_id = await conn.fetchval("SELECT id FROM referees WHERE name = $1", f["referee"]) or \
                    await conn.fetchval("INSERT INTO referees (name) VALUES ($1) RETURNING id", f["referee"])
            await conn.execute(
                """
                INSERT INTO matches (provider_id, season_id, home_team_id, away_team_id, referee_id, kickoff_at,
                                     round, status, home_goals, away_goals)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::match_status, $9, $10)
                ON CONFLICT (provider_id) DO UPDATE SET
                    kickoff_at = EXCLUDED.kickoff_at, referee_id = COALESCE(EXCLUDED.referee_id, matches.referee_id),
                    round = EXCLUDED.round, home_goals = EXCLUDED.home_goals, away_goals = EXCLUDED.away_goals,
                    -- no pisar 'lineups_confirmed', que lo pone el worker de alineaciones
                    status = CASE WHEN matches.status = 'lineups_confirmed' AND EXCLUDED.status = 'scheduled'
                                  THEN matches.status ELSE EXCLUDED.status END
                """,
                f["provider_id"], season_id, team_ids[0], team_ids[1], referee_id, f["kickoff_at"],
                f["round"], f["status"], f["home_goals"], f["away_goals"],
            )
            count += 1
    return count


async def main(start: date, days: int, leagues: list[int] | None) -> None:
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        raise SystemExit("Falta la variable de entorno API_FOOTBALL_KEY")
    conn = await asyncpg.connect(settings.database_url)
    try:
        if not leagues:
            leagues = [r["provider_id"] for r in await conn.fetch(
                "SELECT provider_id FROM leagues WHERE is_active AND provider_id IS NOT NULL")]
        end = start + timedelta(days=days)
        async with httpx.AsyncClient(base_url=BASE_URL, headers={"x-apisports-key": key}, timeout=20) as client:
            for league in leagues:
                fixtures = await fetch_fixtures(client, league, start, end)
                n = await upsert(conn, fixtures)
                print(f"Liga {league}: {n} partidos importados")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=date.fromisoformat, default=date.today())
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--leagues", type=lambda s: [int(x) for x in s.split(",")], default=None)
    a = ap.parse_args()
    asyncio.run(main(a.start, a.days, a.leagues))
