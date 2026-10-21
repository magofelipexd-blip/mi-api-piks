"""Modelo de goles Poisson con ratings de ataque y defensa.

v1, deliberadamente simple:
- Ratings iterativos ajustados por rival, con un prior que tira hacia la media
  cuando un equipo tiene pocos partidos.
- Ventaja de campo implícita: medias de goles local y visitante por liga.
- Cruces europeos: los ratings se escalan con el coeficiente de cada liga.

La probabilidad final no es la del modelo: se mezcla con el mercado sin margen
(ver pipeline.py), porque un modelo con pocos partidos no supera al mercado solo.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    league_id: int
    home: int
    away: int
    home_goals: int
    away_goals: int


@dataclass
class TeamRating:
    league_id: int
    attack: float
    defense: float
    games: int


@dataclass
class LeagueParams:
    mu_home: float
    mu_away: float
    strength: float = 1.0


@dataclass
class Fit:
    teams: dict[int, TeamRating]
    leagues: dict[int, LeagueParams]


def fit(results: list[Result], strengths: dict[int, float] | None = None, prior_goals: float = 3.0,
        iterations: int = 20) -> Fit:
    strengths = strengths or {}
    by_league: dict[int, list[Result]] = defaultdict(list)
    for r in results:
        by_league[r.league_id].append(r)

    leagues: dict[int, LeagueParams] = {}
    teams: dict[int, TeamRating] = {}
    for lid, rs in by_league.items():
        n = len(rs)
        if n < 10:
            continue
        mu_h = sum(r.home_goals for r in rs) / n
        mu_a = sum(r.away_goals for r in rs) / n
        leagues[lid] = LeagueParams(mu_h, mu_a, strengths.get(lid, 1.0))

        ids = {r.home for r in rs} | {r.away for r in rs}
        att = dict.fromkeys(ids, 1.0)
        dfn = dict.fromkeys(ids, 1.0)
        games = defaultdict(int)
        for r in rs:
            games[r.home] += 1
            games[r.away] += 1
        for _ in range(iterations):
            gf, ga = defaultdict(float), defaultdict(float)
            exp_f, exp_a = defaultdict(float), defaultdict(float)
            for r in rs:
                gf[r.home] += r.home_goals
                gf[r.away] += r.away_goals
                ga[r.home] += r.away_goals
                ga[r.away] += r.home_goals
                exp_f[r.home] += mu_h * dfn[r.away]
                exp_f[r.away] += mu_a * dfn[r.home]
                exp_a[r.home] += mu_a * att[r.away]
                exp_a[r.away] += mu_h * att[r.home]
            att = {t: (gf[t] + prior_goals) / (exp_f[t] + prior_goals) for t in ids}
            dfn = {t: (ga[t] + prior_goals) / (exp_a[t] + prior_goals) for t in ids}
            # normalizar para que la media de la liga siga siendo 1
            ma, md = sum(att.values()) / len(att), sum(dfn.values()) / len(dfn)
            att = {t: v / ma for t, v in att.items()}
            dfn = {t: v / md for t, v in dfn.items()}
        for t in ids:
            # si un equipo aparece en dos ligas (raro), se queda con la de más partidos
            if t not in teams or games[t] > teams[t].games:
                teams[t] = TeamRating(lid, att[t], dfn[t], games[t])
    return Fit(teams, leagues)


def expected_goals(f: Fit, home: int, away: int, competition_league: int) -> tuple[float, float] | None:
    th, ta = f.teams.get(home), f.teams.get(away)
    if th is None or ta is None:
        return None
    if competition_league in f.leagues and th.league_id == ta.league_id == competition_league:
        lp = f.leagues[competition_league]
        return lp.mu_home * th.attack * ta.defense, lp.mu_away * ta.attack * th.defense
    # Cruce entre ligas: escalar por la fuerza de cada liga
    lh, la = f.leagues.get(th.league_id), f.leagues.get(ta.league_id)
    if lh is None or la is None:
        return None
    mu_h, mu_a = (lh.mu_home + la.mu_home) / 2, (lh.mu_away + la.mu_away) / 2
    att_h, def_h = th.attack * lh.strength, th.defense / lh.strength
    att_a, def_a = ta.attack * la.strength, ta.defense / la.strength
    return mu_h * att_h * def_a, mu_a * att_a * def_h


def _pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def outcome_probabilities(lam_home: float, lam_away: float, total_line: float = 2.5, max_goals: int = 10) -> dict[str, float]:
    ph = [_pmf(lam_home, i) for i in range(max_goals + 1)]
    pa = [_pmf(lam_away, j) for j in range(max_goals + 1)]
    home = draw = away = over = 0.0
    for i, a in enumerate(ph):
        for j, b in enumerate(pa):
            p = a * b
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
            if i + j > total_line:
                over += p
    s = home + draw + away  # la masa que queda fuera de la matriz es despreciable
    return {"home": home / s, "draw": draw / s, "away": away / s, "over": over / s, "under": 1 - over / s}


def no_vig(prices: dict[str, float]) -> dict[str, float]:
    """Probabilidades implícitas sin el margen de la casa (normalización proporcional)."""
    inv = {k: 1 / v for k, v in prices.items() if v and v > 1}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()} if total else {}
