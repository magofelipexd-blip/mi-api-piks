import math

import pytest

from app.betting import Leg, classify, expected_value, kelly_fraction, optimize_parlays, settle_profit, stake_pct


def leg(key: str, match: int, odds: float, p: float, bb: bool = False) -> Leg:
    return Leg(key=key, match_id=match, title=key, selections=(key,), selection_ids=(match,), odds=odds,
               probability=p, is_bet_builder=bb, pick_ids=(None,))


class TestReglas:
    def test_ev_y_kelly(self):
        assert expected_value(0.5, 2.0) == pytest.approx(0)
        assert kelly_fraction(0.6, 2.0) == pytest.approx(0.2)
        assert kelly_fraction(0.4, 2.0) < 0

    def test_stake_respeta_tope_y_no_negativo(self):
        assert stake_pct(0.9, 2.0, cap=0.03) == 0.03
        assert stake_pct(0.4, 2.0, cap=0.03) == 0.0

    @pytest.mark.parametrize(
        "p, odds, esperado",
        [
            (0.85, 1.25, "segura"),     # 80%+ admite cuota < 1.50
            (0.72, 1.55, "favorito"),
            (0.72, 1.45, None),         # favorito exige cuota >= 1.50
            (0.65, 1.80, None),         # por debajo del 70%
            (0.82, 1.20, None),         # EV negativo
        ],
    )
    def test_clasificacion(self, p, odds, esperado):
        assert classify(p, odds) == esperado

    def test_liquidacion(self):
        assert settle_profit("won", 1.8) == pytest.approx(0.8)
        assert settle_profit("lost", 1.8) == -1
        assert settle_profit("half_won", 1.8) == pytest.approx(0.4)


class TestOptimizador:
    LEGS = [
        leg("a", 1, 1.52, 0.71), leg("b", 1, 1.24, 0.81), leg("c", 2, 1.58, 0.70),
        leg("d", 3, 1.27, 0.80), leg("e", 4, 1.50, 0.72), leg("f", 5, 1.52, 0.71),
        leg("g", 6, 1.53, 0.70), leg("h", 7, 1.25, 0.84), leg("i", 8, 1.21, 0.86),
        leg("bb", 2, 1.85, 0.60, bb=True),
    ]

    def test_rango_de_cuota_y_un_leg_por_partido(self):
        res = optimize_parlays(self.LEGS)
        assert res, "debe encontrar combinadas"
        for r in res:
            assert 5.0 <= r.total_odds <= 7.0
            matches = [l.match_id for l in r.legs]
            assert len(matches) == len(set(matches))
            assert r.ev > 0
            assert 0 < r.stake_pct <= 0.01

    def test_maximiza_probabilidad(self):
        """La primera combinada es la de mayor probabilidad entre todas las válidas (fuerza bruta)."""
        from itertools import combinations

        best = 0.0
        for n in range(2, 9):
            for combo in combinations(self.LEGS, n):
                if len({l.match_id for l in combo}) < n:
                    continue
                o = math.prod(l.odds for l in combo)
                p = math.prod(l.probability for l in combo)
                if 5 <= o <= 7 and p * o > 1:
                    best = max(best, p)
        assert optimize_parlays(self.LEGS)[0].probability == pytest.approx(best)

    def test_alternativas_diversas(self):
        res = optimize_parlays(self.LEGS, top=3)
        for i, a in enumerate(res):
            for b in res[i + 1:]:
                shared = len({l.key for l in a.legs} & {l.key for l in b.legs})
                assert shared / max(len(a.legs), len(b.legs)) <= 0.5

    def test_sin_candidatos_suficientes(self):
        assert optimize_parlays([leg("x", 1, 1.2, 0.9), leg("y", 2, 1.3, 0.85)]) == []
