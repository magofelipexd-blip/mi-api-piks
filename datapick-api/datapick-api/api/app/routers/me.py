from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn
from ..deps import current_user
from ..schemas import Alert, Bankroll, DeviceIn, NotificationPref, Risk

router = APIRouter(prefix="/me", tags=["usuario"])


@router.get("/bankroll", response_model=Bankroll)
async def get_bankroll(user: UUID = Depends(current_user), conn=Depends(get_conn)):
    row = await conn.fetchrow(
        """
        SELECT b.user_id, b.currency, b.initial_amount AS initial, b.current_amount AS current,
               b.kelly_fraction, b.max_single_pct, b.max_parlay_pct, b.max_daily_exposure,
               COALESCE((SELECT sum(stake) FROM user_bets ub
                         WHERE ub.user_id = b.user_id AND ub.placed_at::date = CURRENT_DATE), 0)
               / b.current_amount AS exposure_today
        FROM bankrolls b WHERE b.user_id = $1
        """,
        user,
    )
    if row is None:
        raise HTTPException(404, "El usuario no tiene bankroll configurado")
    return {
        **dict(row),
        "risk": {
            "kelly_multiplier": row["kelly_fraction"],
            "max_single_pct": row["max_single_pct"],
            "max_parlay_pct": row["max_parlay_pct"],
            "max_daily_exposure": row["max_daily_exposure"],
        },
    }


@router.put("/risk", response_model=Risk)
async def update_risk(risk: Risk, user: UUID = Depends(current_user), conn=Depends(get_conn)):
    await conn.execute(
        """
        UPDATE bankrolls SET kelly_fraction = $2, max_single_pct = $3, max_parlay_pct = $4,
               max_daily_exposure = $5, updated_at = now()
        WHERE user_id = $1
        """,
        user, risk.kelly_multiplier, risk.max_single_pct, risk.max_parlay_pct, risk.max_daily_exposure,
    )
    return risk


@router.get("/alerts", response_model=list[Alert])
async def alerts(limit: int = 30, user: UUID = Depends(current_user), conn=Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT id, kind::text, title, body,
               (extract(epoch FROM now() - created_at) / 60)::int AS minutes_ago,
               (payload ->> 'pick_id')::bigint AS pick_id,
               read_at IS NOT NULL AS read
        FROM notifications WHERE user_id = $1
        ORDER BY created_at DESC LIMIT $2
        """,
        user, min(limit, 100),
    )
    return [dict(r) for r in rows]


@router.post("/alerts/read", status_code=204)
async def mark_read(user: UUID = Depends(current_user), conn=Depends(get_conn)):
    await conn.execute("UPDATE notifications SET read_at = now() WHERE user_id = $1 AND read_at IS NULL", user)


@router.get("/notification-preferences", response_model=list[NotificationPref])
async def get_prefs(user: UUID = Depends(current_user), conn=Depends(get_conn)):
    rows = await conn.fetch(
        "SELECT kind::text, enabled FROM notification_preferences WHERE user_id = $1 ORDER BY kind", user
    )
    return [dict(r) for r in rows]


@router.put("/notification-preferences", response_model=list[NotificationPref])
async def put_prefs(prefs: list[NotificationPref], user: UUID = Depends(current_user), conn=Depends(get_conn)):
    valid = {r["k"] for r in await conn.fetch("SELECT unnest(enum_range(NULL::alert_kind))::text AS k")}
    bad = [p.kind for p in prefs if p.kind not in valid]
    if bad:
        raise HTTPException(422, f"Tipos de alerta desconocidos: {', '.join(bad)}")
    await conn.executemany(
        """
        INSERT INTO notification_preferences (user_id, kind, enabled) VALUES ($1, $2::alert_kind, $3)
        ON CONFLICT (user_id, kind) DO UPDATE SET enabled = EXCLUDED.enabled
        """,
        [(user, p.kind, p.enabled) for p in prefs],
    )
    return await get_prefs(user, conn)


@router.post("/devices", status_code=201)
async def register_device(device: DeviceIn, user: UUID = Depends(current_user), conn=Depends(get_conn)):
    """La app llama aquí con el token de Expo Push al arrancar."""
    await conn.execute(
        """
        INSERT INTO devices (user_id, push_token, platform) VALUES ($1, $2, $3)
        ON CONFLICT (push_token) DO UPDATE SET user_id = EXCLUDED.user_id, last_seen_at = now()
        """,
        user, device.push_token, device.platform,
    )
    return {"ok": True}
