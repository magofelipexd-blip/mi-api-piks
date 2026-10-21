"""Modelos de respuesta. Se serializan en camelCase para que coincidan con los tipos de la app."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Schema(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Pick(Schema):
    id: int
    match_id: int
    league: str
    home: str
    away: str
    kickoff: datetime
    category: str
    market: str
    selection: str
    bookmaker: str
    tier: Literal["favorito", "segura"]
    probability: float
    model_probability: float
    market_probability: float | None
    published_odds: float
    current_odds: float
    opening_odds: float
    odds_history: list[float]
    lineups_confirmed: bool
    p_start: float | None = None
    reasons: list[str]
    stake_pct: float
    current_ev: float


class OddsPoint(Schema):
    ts: datetime
    price: float


class OddsAlert(Schema):
    id: int
    kind: str
    bookmaker: str
    price_before: float
    price_after: float
    pct_change: float
    window_minutes: int
    detected_at: datetime


class PickDetail(Pick):
    odds_series: list[OddsPoint]
    alerts: list[OddsAlert]
    ledger_hash: str


class ParlayLeg(Schema):
    key: str
    match_id: int
    title: str
    selections: list[str]
    odds: float
    probability: float
    is_bet_builder: bool
    pick_ids: list[int]


class Parlay(Schema):
    id: str
    legs: list[ParlayLeg]
    total_odds: float
    probability: float
    ev: float
    stake_pct: float


class TransparencyRow(Schema):
    tier: Literal["favorito", "segura", "total"]
    picks: int
    won: int
    win_rate: float
    yield_pct: float
    profit_units: float
    avg_odds: float
    avg_probability: float
    avg_clv: float | None
    brier: float | None


class CalibrationBucket(Schema):
    promised: float
    observed: float
    n: int


class ParlayHistory(Schema):
    played: int
    won: int
    yield_pct: float


class LedgerStatus(Schema):
    picks: int
    broken: list[int]
    last_hash: str | None


class Transparency(Schema):
    rows: list[TransparencyRow]
    parlays: ParlayHistory
    profit_curve: list[float]
    calibration: list[CalibrationBucket]
    ledger: LedgerStatus


class MatchProbabilities(Schema):
    home: float
    draw: float
    away: float


class MatchPick(Schema):
    id: int
    tier: Literal["favorito", "segura"]
    selection: str
    odds: float
    probability: float
    status: str = "published"


class ScheduleMatch(Schema):
    id: int
    league_id: int
    league: str
    round: str | None
    kickoff: datetime
    status: str
    home: str
    away: str
    home_short: str | None
    away_short: str | None
    home_goals: int | None
    away_goals: int | None
    lineups_confirmed: bool
    referee: str | None
    importance: float | None
    probabilities: MatchProbabilities | None
    probability_source: Literal["model", "market"] | None = None
    odds: MatchProbabilities | None = None  # cuotas 1X2 de DraftKings
    venue: str | None = None
    picks: list[MatchPick]


class Alert(Schema):
    id: int
    kind: str
    title: str
    body: str
    minutes_ago: int
    pick_id: int | None = None
    read: bool


class Risk(Schema):
    kelly_multiplier: float = Field(ge=0.05, le=0.5)
    max_single_pct: float = Field(gt=0, le=0.05)
    max_parlay_pct: float = Field(gt=0, le=0.02)
    max_daily_exposure: float = Field(gt=0, le=0.25)


class Bankroll(Schema):
    user_id: UUID
    currency: str
    initial: float
    current: float
    risk: Risk
    exposure_today: float


class DeviceIn(Schema):
    push_token: str = Field(min_length=10, max_length=300)
    platform: Literal["ios", "android", "web"]


class NotificationPref(Schema):
    kind: str
    enabled: bool


class SettleIn(Schema):
    result: Literal["won", "lost", "push", "void", "half_won", "half_lost"]
    closing_odds: float | None = Field(default=None, gt=1)
