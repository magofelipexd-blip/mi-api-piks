"""Calendario, resultados y cuotas reales desde la API pública de ESPN.

No necesita clave. Es una API no documentada: sirve para desarrollo y pruebas;
en producción conviene un proveedor con contrato (API-Football, Sportmonks...).

Cuotas: ESPN publica las de DraftKings para 1X2 y más/menos goles, con su
apertura y su valor actual.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard"
DRAFTKINGS_ID = 4  # bookmakers.id


# ---------------------------------------------------------------------------
# Parseo (funciones puras, cubiertas por tests)
# ---------------------------------------------------------------------------
def american_to_decimal(value: Any) -> float | None:
    """'+105' -> 2.05, '-175' -> 1.571, 'EVEN' -> 2.0."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if s in ("", "OFF", "N/A", "-"):
        return None
    if s == "EVEN":
        return 2.0
    try:
        v = int(s.replace("+", ""))
    except ValueError:
        return None
    if v == 0:
        return None
    dec = 1 + v / 100 if v > 0 else 1 + 100 / abs(v)
    return round(dec, 3)


def map_status(t: dict[str, Any]) -> str:
    name = t.get("name", "")
    if name == "STATUS_POSTPONED":
        return "postponed"
    if name in ("STATUS_CANCELED", "STATUS_ABANDONED", "STATUS_FORFEIT"):
        return "cancelled"
    if t.get("completed"):
        return "finished"
    if t.get("state") == "in":
        return "live"
    return "scheduled"


def _line(text: Any, fallback: Any) -> float | None:
    """'o2.5' -> 2.5"""
    try:
        return float(str(text).lstrip("ouOU"))
    except (TypeError, ValueError):
        try:
            return float(fallback)
        except (TypeError, ValueError):
            return None


def _open_close(node: dict | None) -> tuple[float | None, float | None]:
    node = node or {}
    o = american_to_decimal((node.get("open") or {}).get("odds"))
    c = american_to_decimal((node.get("close") or {}).get("odds"))
    return o, c or o


def parse_odds(comp: dict[str, Any]) -> dict[str, Any] | None:
    o = next((x for x in (comp.get("odds") or []) if isinstance(x, dict)), None)
    if o is None:
        return None
    ml = o.get("moneyline") or {}
    out: dict[str, Any] = {"provider": (o.get("provider") or {}).get("name")}
    for side in ("home", "draw", "away"):
        op, cl = _open_close(ml.get(side))
        if cl is None and side == "draw":
            cl = american_to_decimal((o.get("drawOdds") or {}).get("moneyLine"))
            op = op or cl
        out[side] = {"open": op, "close": cl}
    total = o.get("total") or {}
    over, under = total.get("over") or {}, total.get("under") or {}
    line = _line(((over.get("close") or over.get("open") or {}).get("line")), o.get("overUnder"))
    out["total_line"] = line
    for side, node in (("over", over), ("under", under)):
        op, cl = _open_close(node)
        out[side] = {"open": op, "close": cl}
    return out


def parse_event(e: dict[str, Any]) -> dict[str, Any]:
    comp = e["competitions"][0]
    teams = {c["homeAway"]: c for c in comp["competitors"]}
    status = map_status(e["status"]["type"])

    def team(side: str) -> dict[str, Any]:
        t = teams[side]["team"]
        return {"espn_id": int(t["id"]), "name": t.get("displayName") or t.get("name"),
                "short": t.get("abbreviation"), "logo": t.get("logo")}

    def score(side: str) -> int | None:
        if status not in ("finished", "live"):
            return None
        try:
            return int(teams[side].get("score"))
        except (TypeError, ValueError):
            return None

    return {
        "espn_id": int(e["id"]),
        "kickoff_at": datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
        "status": status,
        "venue": (comp.get("venue") or {}).get("fullName"),
        "home": team("home"),
        "away": team("away"),
        "home_goals": score("home"),
        "away_goals": score("away"),
        "odds": _safe_odds(comp),
    }


def _safe_odds(comp: dict[str, Any]) -> dict[str, Any] | None:
    """Una cuota mal formada no debe impedir guardar el partido."""
    try:
        return parse_odds(comp)
    except (AttributeError, TypeError, KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------
async def fetch_day(client: httpx.AsyncClient, code: str, day: date) -> list[dict[str, Any]]:
    for attempt in range(3):
        try:
            r = await client.get(BASE.format(code=code), params={"dates": day.strftime("%Y%m%d")})
            r.raise_for_status()
            return [parse_event(e) for e in r.json().get("events", [])]
        except (httpx.HTTPError, KeyError, ValueError):
            if attempt == 2:
                raise
            await asyncio.sleep(1.5 * (attempt + 1))
    return []


# ---------------------------------------------------------------------------
# Escritura en la base
# ---------------------------------------------------------------------------
async def _team_id(conn: asyncpg.Connection, t: dict[str, Any]) -> int:
    return await conn.fetchval(
        """
        INSERT INTO teams (espn_id, name, short_name, logo_url) VALUES ($1, $2, $3, $4)
        ON CONFLICT (espn_id) DO UPDATE SET name = EXCLUDED.name, short_name = EXCLUDED.short_name,
                                            logo_url = EXCLUDED.logo_url
        RETURNING id
        """,
        t["espn_id"], t["name"], t["short"], t["logo"],
    )


async def _selection(conn: asyncpg.Connection, match_id: int, market_id: int, outcome: str,
                     line: float | None, label: str) -> int:
    await conn.execute(
        """
        INSERT INTO selections (match_id, market_type_id, line, outcome, label)
        VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING
        """,
        match_id, market_id, line, outcome, label,
    )
    return await conn.fetchval(
        """
        SELECT id FROM selections WHERE match_id = $1 AND market_type_id = $2 AND outcome = $3
          AND COALESCE(line, -999) = COALESCE($4::numeric, -999) AND player_id IS NULL AND team_id IS NULL
        """,
        match_id, market_id, outcome, line,
    )


async def _record_price(conn: asyncpg.Connection, selection_id: int, open_: float | None, current: float | None) -> None:
    """Guarda la apertura la primera vez y después cada cambio de cuota."""
    if current is None or current <= 1:
        return
    last = await conn.fetchval(
        "SELECT price FROM odds_latest WHERE selection_id = $1 AND bookmaker_id = $2", selection_id, DRAFTKINGS_ID
    )
    if last is None:
        if open_ and open_ > 1 and abs(open_ - current) > 1e-6:
            await conn.execute(
                "INSERT INTO odds_snapshots (ts, selection_id, bookmaker_id, price) VALUES (now() - INTERVAL '1 second', $1, $2, $3)",
                selection_id, DRAFTKINGS_ID, open_,
            )
            # la apertura queda como opening_price; ahora la cuota actual
        await conn.execute(
            "INSERT INTO odds_snapshots (ts, selection_id, bookmaker_id, price) VALUES (now(), $1, $2, $3)",
            selection_id, DRAFTKINGS_ID, current,
        )
    elif abs(last - current) > 1e-6:
        await conn.execute(
            "INSERT INTO odds_snapshots (ts, selection_id, bookmaker_id, price) VALUES (now(), $1, $2, $3)",
            selection_id, DRAFTKINGS_ID, current,
        )


async def upsert_events(conn: asyncpg.Connection, league_id: int, season_id: int, events: list[dict]) -> int:
    n = 0
    for ev in events:
        async with conn.transaction():
            home_id = await _team_id(conn, ev["home"])
            away_id = await _team_id(conn, ev["away"])
            if home_id == away_id:
                continue
            match_id = await conn.fetchval(
                """
                INSERT INTO matches (espn_id, season_id, home_team_id, away_team_id, kickoff_at, status,
                                     venue, home_goals, away_goals)
                VALUES ($1, $2, $3, $4, $5, $6::match_status, $7, $8, $9)
                ON CONFLICT (espn_id) DO UPDATE SET
                    kickoff_at = EXCLUDED.kickoff_at, venue = EXCLUDED.venue,
                    home_goals = EXCLUDED.home_goals, away_goals = EXCLUDED.away_goals,
                    status = CASE WHEN matches.status = 'lineups_confirmed' AND EXCLUDED.status = 'scheduled'
                                  THEN matches.status ELSE EXCLUDED.status END
                RETURNING id
                """,
                ev["espn_id"], season_id, home_id, away_id, ev["kickoff_at"], ev["status"],
                ev["venue"], ev["home_goals"], ev["away_goals"],
            )
            odds = ev["odds"]
            if odds and ev["status"] == "scheduled":
                labels = {"home": f"Gana {ev['home']['name']}", "draw": "Empate", "away": f"Gana {ev['away']['name']}"}
                for side in ("home", "draw", "away"):
                    sid = await _selection(conn, match_id, 1, side, None, labels[side])
                    await _record_price(conn, sid, odds[side]["open"], odds[side]["close"])
                line = odds.get("total_line")
                if line is not None:
                    for side, label in (("over", f"Más de {line:g} goles"), ("under", f"Menos de {line:g} goles")):
                        sid = await _selection(conn, match_id, 3, side, line, label)
                        await _record_price(conn, sid, odds[side]["open"], odds[side]["close"])
            n += 1
    return n


async def sync(conn: asyncpg.Connection, days: list[date], skip_complete: bool = True, concurrency: int = 4) -> dict[str, int]:
    """Importa los días indicados para todas las competiciones con código ESPN."""
    leagues = await conn.fetch(
        """
        SELECT l.id, l.espn_code, s.id AS season_id FROM leagues l
        JOIN seasons s ON s.league_id = l.id AND s.is_current
        WHERE l.espn_code IS NOT NULL AND l.is_active
        """
    )
    done = set()
    if skip_complete:
        done = {(r["league_id"], r["day"]) for r in await conn.fetch("SELECT league_id, day FROM etl_days WHERE complete")}
    jobs = [(lg, d) for lg in leagues for d in days if (lg["id"], d) not in done]
    sem = asyncio.Semaphore(concurrency)
    today = datetime.now(timezone.utc).date()
    stats = {"days": 0, "events": 0, "errors": 0}

    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "DataPick/0.1"}) as client:
        async def one(lg, d):
            async with sem:
                try:
                    return lg, d, await fetch_day(client, lg["espn_code"], d)
                except Exception as exc:  # noqa: BLE001 - un día fallido no para la sincronización
                    return lg, d, exc

        for lg, d, result in await asyncio.gather(*(one(lg, d) for lg, d in jobs)):
            if isinstance(result, Exception):
                stats["errors"] += 1
                continue
            stats["events"] += await upsert_events(conn, lg["id"], lg["season_id"], result)
            complete = d < today and all(e["status"] in ("finished", "postponed", "cancelled") for e in result)
            await conn.execute(
                """
                INSERT INTO etl_days (league_id, day, complete) VALUES ($1, $2, $3)
                ON CONFLICT (league_id, day) DO UPDATE SET complete = EXCLUDED.complete, fetched_at = now()
                """,
                lg["id"], d, complete,
            )
            stats["days"] += 1
    return stats


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
