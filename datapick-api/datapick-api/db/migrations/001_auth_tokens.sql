-- Para bases creadas antes de añadir los tokens de dispositivo
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_anonymous BOOLEAN NOT NULL DEFAULT FALSE;

-- Tokens de dispositivo: cada instalación de la app recibe uno al registrarse.
-- Solo se guarda el hash SHA-256; el token en claro solo lo conoce el teléfono.
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash   TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS auth_tokens_user_idx ON auth_tokens (user_id);
