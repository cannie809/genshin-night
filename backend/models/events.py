"""Game event model for tracking game history."""

from typing import Any

from pydantic import BaseModel


class GameEvent(BaseModel):
    """Represents a game event.

    Attributes:
        type: Event type (e.g., "night_kill", "vote", "hunter_shoot")
        round: Round number
        phase: Game phase when event occurred
        data: Event-specific data
        message: Human-readable message
    """

    type: str
    round: int
    phase: str
    data: dict[str, Any] = {}
    message: str = ""
