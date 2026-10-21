from datetime import datetime, timezone

import pytest

from app.etl.espn import american_to_decimal, map_status, parse_event
from app.model.poisson import Result, expected_goals, fit, no_vig, outcome_probabilities
from app.pipeline import selection_result

EVENT = {
    "id": "401915312",
    "date": "2026-10-21T19:00Z",
    "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}},
    "competitions": [{
        "venue": {"fullName": "Allianz Arena"},
        "competitors": [
            {"homeAway": "home", "score": "0", "team": {"id": "132", "displayName": "Bayern Munich", "abbreviation": "MUN"}},
            {"homeAway": "away", "score": "0", "team": {"id": "359", "displayName": "Arsenal", "abbreviation": "ARS"}},
        ],
        "odds": [{
            "provider": {"name": "DraftKings"}, "overUnder": 2.5,
            "moneyline": {"home": {"open": {"odds": "+115"}, "close": {"odds": "+105"}},
                          "away": {"open": {"odds": "+205"}, "close": {"odds": "+225"}},
                          "draw": {"open": {"odds": "+275"}, "close": {"odds": "+290"}}},
            "total": {"over": {"open": {"line": "o2.5", "odds": "-165"}, "close": {"line": "o2.5", "odds": "-175"}},
                      "under": {"open": {"line": "u2.5", "odds": "+125"}, "close": {"line": "u2.5", "odds": "+140"}}},
        }],
    }],
}


class TestESPN:
    @pytest.mark.parametrize("raw, dec", [("+105", 2.05), ("-175", 1.571), ("EVEN", 2.0), ("+290", 3.9),
                                          (None, None), ("OFF", None)])
    def test_cuota_americana(self, raw, dec):
        assert american_to_decimal(raw) == dec

    def test_parse_event(self):
        e = parse_event(EVENT)
        assert e["espn_id"] == 401915312
        assert e["kickoff_at"] == datetime(2026, 10, 21, 19, 0, tzinfo=timezone.utc)
        assert (e["home"]["name"], e["away"]["name"]) == ("Bayern Munich", "Arsenal")
        assert e["home_goals"] is None  # partido sin empezar: el marcador "0" no cuenta
        o = e["odds"]
        assert o["home"] == {"open": 2.15, "close": 2.05}
        assert o["draw"]["close"] == 3.9 and o["away"]["close"] == 3.25
        assert o["total_line"] == 2.5 and o["over"]["close"] == 1.571 and o["under"]["close"] == 2.4

    def test_partido_jugado_con_cuotas_vacias(self):
        import copy
        e = copy.deepcopy(EVENT)
        e["status"]["type"] = {"name": "STATUS_FULL_TIME", "state": "post", "completed": True}
        e["competitions"][0]["odds"] = [None]
        e["competitions"][0]["competitors"][0]["score"] = "2"
        p = parse_event(e)
        assert p["odds"] is None and p["status"] == "finished" and p["home_goals"] == 2

    def test_estados(self):
        assert map_status({"name": "STATUS_FULL_TIME", "state": "post", "completed": True}) == "finished"
        assert map_status({"name": "STATUS_SECOND_HALF", "state": "in", "completed": False}) == "live"
        assert map_status({"name": "STATUS_POSTPONED", "state": "post", "completed": False}) == "postponed"


class TestModelo:
    def test_probabilidades_suman_uno(self):
        p = outcome_probabilities(1.6, 1.1)
        assert p["home"] + p["draw"] + p["away"] == pytest.approx(1)
        assert p["over"] + p["under"] == pytest.approx(1)
        assert p["home"] > p["away"]

    def test_sin_margen(self):
        p = no_vig({"home": 2.05, "draw": 3.9, "away": 3.25})
        assert sum(p.values()) == pytest.approx(1)
        assert p["home"] > p["away"] > p["draw"]

    def test_ratings_detectan_al_mejor_equipo(self):
        # el equipo 1 gana a todos 3-0, el 4 pierde siempre
        results = []
        for h in range(1, 5):
            for a in range(1, 5):
                if h != a:
                    results.append(Result(1, h, a, 3 if h == 1 else 1, 0 if h == 1 else (2 if a == 1 else 1)))
        f = fit(results)
        assert f.teams[1].attack > 1 > f.teams[4].attack or f.teams[1].attack > f.teams[4].attack
        lh, la = expected_goals(f, 1, 4, 1)
        assert lh > la

    def test_sin_rating_no_hay_modelo(self):
        f = fit([Result(1, 1, 2, 1, 0)] * 12)
        assert expected_goals(f, 1, 99, 1) is None


class TestLiquidacion:
    @pytest.mark.parametrize("market, outcome, line, hg, ag, esperado", [
        (1, "home", None, 2, 1, "won"), (1, "draw", None, 2, 1, "lost"), (1, "draw", None, 1, 1, "won"),
        (3, "over", 2.5, 2, 1, "won"), (3, "under", 2.5, 2, 1, "lost"), (3, "over", 3.0, 2, 1, "push"),
    ])
    def test_resultado(self, market, outcome, line, hg, ag, esperado):
        assert selection_result(market, outcome, line, hg, ag) == esperado
