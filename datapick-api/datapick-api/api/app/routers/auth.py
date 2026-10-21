import hashlib
import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator

from ..db import get_conn
from ..schemas import Schema

router = APIRouter(prefix="/auth", tags=["auth"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DeviceRegistration(Schema):
    birth_date: date
    country: str = Field(min_length=2, max_length=2)
    accepted_terms: bool

    @field_validator("country")
    @classmethod
    def upper(cls, v: str) -> str:
        if not v.isalpha():
            raise ValueError("Código de país de 2 letras, por ejemplo ES o VE")
        return v.upper()


class DeviceToken(Schema):
    token: str
    user_id: str


@router.post("/device", response_model=DeviceToken, status_code=201)
async def register_device(body: DeviceRegistration, conn=Depends(get_conn)):
    """Crea una cuenta anónima para esta instalación de la app y devuelve su token.

    Exige mayoría de edad (lo comprueba también un trigger en la base) y aceptar
    las condiciones de juego responsable.
    """
    if not body.accepted_terms:
        raise HTTPException(422, "Hay que aceptar las condiciones de uso y juego responsable")
    today = date.today()
    age = today.year - body.birth_date.year - ((today.month, today.day) < (body.birth_date.month, body.birth_date.day))
    if age < 18:
        raise HTTPException(403, "La app es solo para mayores de 18 años")

    token = secrets.token_urlsafe(32)
    async with conn.transaction():
        user_id = await conn.fetchval(
            """
            INSERT INTO users (email, birth_date, country, is_anonymous)
            VALUES ('device-' || gen_random_uuid() || '@datapick.local', $1, $2, TRUE)
            RETURNING id
            """,
            body.birth_date, body.country,
        )
        await conn.execute("INSERT INTO bankrolls (user_id, initial_amount, current_amount) VALUES ($1, 1000, 1000)", user_id)
        await conn.execute(
            """
            INSERT INTO notification_preferences (user_id, kind, enabled)
            SELECT $1, k, k <> 'pick_settled' FROM unnest(enum_range(NULL::alert_kind)) k
            """,
            user_id,
        )
        await conn.execute("INSERT INTO auth_tokens (token_hash, user_id) VALUES ($1, $2)", hash_token(token), user_id)
    return {"token": token, "user_id": str(user_id)}
