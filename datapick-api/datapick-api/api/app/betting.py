"""Lógica de negocio pura: EV, Kelly, clasificación y optimizador de combinadas.

Mismo comportamiento que mobile/src/lib/betting.ts, para que la app y el
backend den siempre los mismos números.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Tier = Literal["favorito", "segura"]


def expected_value(p: float, odds: float) -> float:
    return p * odds - 1


def kelly_fraction(p: float, odds: float) -> float:
    b = odds - 1
    return 0.0 if b <= 0 else (b * p - (1 - p)) / b


def stake_pct(p: float, odds: float, cap: float, multiplier: float = 0.25) -> float:
    return max(0.0, min(cap, multiplier * kelly_fraction(p, odds)))


def classify(p: float, odds: float, min_ev: float = 0.0) -> Tier | None:
    if expected_value(p, odds) <= min_ev:
        return None
    if p >= 0.80:
        return "segura"
    if p >= 0.70 and odds >= 1.50:
        return "favorito"
    return None


@dataclass(frozen=True)
class Leg:
    key: str
    match_id: int
    title: str
    selections: tuple[str, ...]
    selection_ids: tuple[int, ...]
    odds: float
    probability: float
    is_bet_builder: bool = False
    pick_ids: tuple[int | None, ...] = ()
    joint_prediction_id: int | None = None


@dataclass
class ParlayResult:
    legs: list[Leg]
    total_odds: float
    probability: float
    ev: float = field(init=False)
    stake_pct: float = 0.0

    def __post_init__(self) -> None:
        self.ev = expected_value(self.probability, self.total_odds)


def optimize_parlays(
    legs: list[Leg],
    min_odds: float = 5.0,
    max_odds: float = 7.0,
    top: int = 3,
    max_parlay_pct: float = 0.01,
    kelly_multiplier: float = 0.25,
    max_candidates: int = 40,
) -> list[ParlayResult]:
    """Maximiza la probabilidad conjunta con cuota total en [min_odds, max_odds].

    - Como máximo un leg por partido. Un bloque Bet Builder cuenta como un leg.
    - Solo combinadas con EV positivo.
    - Las alternativas comparten como mucho la mitad de sus legs con las ya elegidas.

    Búsqueda en profundidad con poda por cuota. Con más de unas decenas de
    candidatos conviene sustituirla por CP-SAT (OR-Tools) en espacio logarítmico.
    """
    candidates = sorted(
        (l for l in legs if expected_value(l.probability, l.odds) > 0),
        key=lambda l: l.probability,
        reverse=True,
    )[:max_candidates]
    found: list[tuple[float, float, list[Leg]]] = []
    log_max = math.log(max_odds) + 1e-12
    log_min = math.log(min_odds) - 1e-12

    def dfs(start: int, chosen: list[Leg], log_o: float, log_p: float, used: set[int]) -> None:
        if log_min <= log_o <= log_max and len(chosen) >= 2:
            found.append((log_p, log_o, list(chosen)))
        for i in range(start, len(candidates)):
            leg = candidates[i]
            if leg.match_id in used:
                continue
            nxt = log_o + math.log(leg.odds)
            if nxt > log_max:
                continue
            used.add(leg.match_id)
            chosen.append(leg)
            dfs(i + 1, chosen, nxt, log_p + math.log(leg.probability), used)
            chosen.pop()
            used.discard(leg.match_id)

    dfs(0, [], 0.0, 0.0, set())
    found.sort(key=lambda x: x[0], reverse=True)

    picked: list[ParlayResult] = []
    for log_p, log_o, combo in found:
        p, o = math.exp(log_p), math.exp(log_o)
        if p * o <= 1:
            continue
        keys = {l.key for l in combo}
        too_similar = any(
            sum(1 for l in q.legs if l.key in keys) / max(len(q.legs), len(combo)) > 0.5 for q in picked
        )
        if too_similar:
            continue
        picked.append(
            ParlayResult(
                legs=combo,
                total_odds=o,
                probability=p,
                stake_pct=stake_pct(p, o, max_parlay_pct, kelly_multiplier),
            )
        )
        if len(picked) == top:
            break
    return picked


def settle_profit(status: str, odds: float) -> float:
    """Beneficio por unidad apostada."""
    return {
        "won": odds - 1,
        "lost": -1.0,
        "push": 0.0,
        "void": 0.0,
        "half_won": (odds - 1) / 2,
        "half_lost": -0.5,
    }[status]
