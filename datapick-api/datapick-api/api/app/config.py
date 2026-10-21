from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://pronosticos:pronosticos_dev@localhost:5432/pronosticos"
    admin_key: str = "dev-admin-key"
    # Solo desarrollo: permite identificarse con la cabecera X-User-Id. En producción, false.
    allow_dev_user_header: bool = True
    cors_origins: list[str] = ["*"]

    # Reglas de negocio
    parlay_min_odds: float = 5.00
    parlay_max_odds: float = 7.00
    parlay_top_n: int = 3
    min_ev: float = 0.0
    min_publish_ev: float = 0.02      # EV mínimo para publicar un pick
    worker_interval_seconds: int = 600


settings = Settings()
