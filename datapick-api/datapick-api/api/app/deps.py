import hmac
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from .config import settings
from .db import get_conn
from .routers.auth import hash_token


async def current_user(
    authorization: str | None = Header(None, description="Bearer <token> devuelto por POST /auth/device"),
    x_user_id: UUID | None = Header(None, description="Solo desarrollo, desactivado en producción"),
    conn=Depends(get_conn),
) -> UUID:
    """Identifica al usuario por el token de su dispositivo."""
    user_id = None
    if authorization and authorization.lower().startswith("bearer "):
        user_id = await conn.fetchval(
            """
            UPDATE auth_tokens SET last_used_at = now()
            WHERE token_hash = $1 AND revoked_at IS NULL
            RETURNING user_id
            """,
            hash_token(authorization[7:].strip()),
        )
    elif x_user_id is not None and settings.allow_dev_user_header:
        user_id = x_user_id

    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token no válido o ausente")
    row = await conn.fetchrow("SELECT id, self_excluded_until FROM users WHERE id = $1", user_id)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario desconocido")
    excluded = row["self_excluded_until"]
    if excluded is not None and excluded > datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cuenta en autoexclusión de juego responsable")
    return row["id"]


async def require_admin(x_admin_key: str = Header(...)) -> None:
    if not hmac.compare_digest(x_admin_key, settings.admin_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Clave de administración no válida")
