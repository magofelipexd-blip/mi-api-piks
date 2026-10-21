from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.etl.api_football import clean_referee, map_status, parse_fixture, season_year
from app.main import app

SAMPLE = {
    "fixture": {"id": 1208123, "referee": "Anthony Taylor, England", "date": "2026-10-24T11:30:00+00:00",
                "status": {"long": "Not Started", "short": "NS"}},
    "league": {"id": 39, "name": "Premier League", "season": 2026, "round": "Regular Season - 9"},
    "teams": {"home": {"id": 50, "name": "Manchester City", "logo": "https://x/50.png"},
              "away": {"id": 33, "name": "Manchester United", "logo": "https://x/33.png"}},
    "goals": {"home": None, "away": None},
}


class TestImportador:
    def test_parse_fixture(self):
        f = parse_fixture(SAMPLE)
        assert f["provider_id"] == 1208123
        assert f["kickoff_at"] == datetime(2026, 10, 24, 11, 30, tzinfo=timezone.utc)
        assert f["status"] == "scheduled"
        assert f["referee"] == "Anthony Taylor"
        assert f["home"]["name"] == "Manchester City"
        assert f["league_provider_id"] == 39

    @pytest.mark.parametrize("short, esperado", [("NS", "scheduled"), ("2H", "live"), ("FT", "finished"),
                                                 ("PST", "postponed"), ("CANC", "cancelled"), (None, "scheduled")])
    def test_estados(self, short, esperado):
        assert map_status(short) == esperado

    def test_temporada(self):
        assert season_year(date(2026, 10, 20)) == 2026
        assert season_year(date(2027, 3, 1)) == 2026

    def test_arbitro_vacio(self):
        assert clean_referee(None) is None and clean_referee("") is None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_calendario_semana(client):
    start = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    matches = client.get("/matches/week", params={"from": start, "days": 7}).json()
    assert matches, "el worker debe haber importado partidos reales"
    kickoffs = [m["kickoff"] for m in matches]
    assert kickoffs == sorted(kickoffs)
    for m in matches:
        if m["probabilities"]:
            assert sum(m["probabilities"].values()) == pytest.approx(1, abs=0.01)
            assert m["probabilitySource"] in ("model", "market")
        if m["odds"]:
            assert all(v > 1 for v in m["odds"].values())
        assert all(p["tier"] in ("favorito", "segura") for p in m["picks"])


def test_calendario_filtro_liga(client):
    matches = client.get("/matches/week", params={"league": 6, "days": 7}).json()
    assert all(m["leagueId"] == 6 for m in matches)
