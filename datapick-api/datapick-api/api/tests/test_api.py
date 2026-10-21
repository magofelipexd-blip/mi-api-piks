"""Tests de integración contra la base con datos reales importados por el worker.

Los datos cambian cada día, así que se comprueban reglas y formas, no valores concretos.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

DEMO_USER = {"X-User-Id": "00000000-0000-4000-8000-000000000001"}
ADMIN = {"X-Admin-Key": settings.admin_key}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_picks_activos_cumplen_reglas(client):
    for p in client.get("/picks/active").json():
        assert p["probability"] * p["publishedOdds"] > 1
        if p["tier"] == "favorito":
            assert 0.70 <= p["probability"] < 0.80 and p["publishedOdds"] >= 1.50
        else:
            assert p["probability"] >= 0.80
        assert len(p["oddsHistory"]) >= 1
        assert isinstance(p["reasons"], list)


def test_detalle_pick(client):
    picks = client.get("/picks/active").json()
    if picks:
        d = client.get(f"/picks/{picks[0]['id']}").json()
        assert len(d["ledgerHash"]) == 64
    assert client.get("/picks/999999999").status_code == 404


def test_transparencia(client):
    t = client.get("/transparency").json()
    assert "total" in {r["tier"] for r in t["rows"]}
    assert t["ledger"]["broken"] == []
    assert t["profitCurve"][0] == 0


def test_combinadas_en_rango(client):
    for p in client.get("/parlays/today").json():
        assert 5 <= p["totalOdds"] <= 7
        assert p["ev"] > 0
        assert len({l["matchId"] for l in p["legs"]}) == len(p["legs"])


def test_admin_requiere_clave(client):
    assert client.post("/admin/transparency/refresh", headers={"X-Admin-Key": "mala"}).status_code == 403


def test_registro_de_dispositivo(client, monkeypatch):
    ok = {"birthDate": "1990-05-01", "country": "ve", "acceptedTerms": True}
    r = client.post("/auth/device", json=ok)
    assert r.status_code == 201
    token = r.json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    b = client.get("/me/bankroll", headers=auth).json()
    assert b["current"] == 1000 and b["risk"]["kellyMultiplier"] == 0.25
    assert client.get("/me/bankroll", headers={"Authorization": "Bearer falso"}).status_code == 401
    # menores y sin aceptar condiciones: rechazado
    assert client.post("/auth/device", json={**ok, "birthDate": "2015-01-01"}).status_code == 403
    assert client.post("/auth/device", json={**ok, "acceptedTerms": False}).status_code == 422
    # en producción la cabecera de desarrollo no sirve
    from app import deps
    monkeypatch.setattr(deps.settings, "allow_dev_user_header", False)
    assert client.get("/me/bankroll", headers=DEMO_USER).status_code == 401
    assert client.get("/me/bankroll", headers=auth).status_code == 200


def test_usuario(client):
    assert client.get("/me/bankroll").status_code == 401
    assert client.get("/me/bankroll", headers={"X-User-Id": "00000000-0000-4000-8000-000000000099"}).status_code == 401
    b = client.get("/me/bankroll", headers=DEMO_USER).json()
    assert 0.05 <= b["risk"]["kellyMultiplier"] <= 0.5
    assert isinstance(client.get("/me/alerts", headers=DEMO_USER).json(), list)


def test_riesgo_validado(client):
    body = {"kellyMultiplier": 0.9, "maxSinglePct": 0.03, "maxParlayPct": 0.01, "maxDailyExposure": 0.1}
    assert client.put("/me/risk", headers=DEMO_USER, json=body).status_code == 422
    body["kellyMultiplier"] = 0.25
    assert client.put("/me/risk", headers=DEMO_USER, json=body).status_code == 200


def test_preferencias_y_dispositivo(client):
    r = client.put("/me/notification-preferences", headers=DEMO_USER, json=[{"kind": "steam_move", "enabled": False}])
    assert {"kind": "steam_move", "enabled": False} in r.json()
    client.put("/me/notification-preferences", headers=DEMO_USER, json=[{"kind": "steam_move", "enabled": True}])
    assert client.put("/me/notification-preferences", headers=DEMO_USER, json=[{"kind": "x", "enabled": True}]).status_code == 422
    r = client.post("/me/devices", headers=DEMO_USER, json={"pushToken": "ExponentPushToken[test-123456]", "platform": "android"})
    assert r.status_code == 201
