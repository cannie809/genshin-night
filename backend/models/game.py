"""Game state model for the werewolf game."""

from enum import Enum
from pathlib import Path
from typing import TypedDict

from pydantic import BaseModel, Field

from .events import GameEvent
from .player import Player


class RoundSnapshot(TypedDict):
    """Snapshot of all transient round data captured before reset_round_data().

    Passed through end_round → _get_player_night_action → reflection pipeline
    so that background tasks see correct state even after the game has moved on.
    """

    night_kills: list[str]
    saved_player: str | None
    poisoned_player: str | None
    seer_checked: str | None
    guarded_player: str | None
    wolf_collab_result: dict[str, str]
    votes: dict[str, str]
    speeches: list[dict[str, str]]


class GamePhase(str, Enum):
    """Game phase enumeration."""

    SETUP = "SETUP"
    NIGHT_GUARD = "NIGHT_GUARD"
    NIGHT_WEREWOLF = "NIGHT_WEREWOLF"
    NIGHT_WITCH = "NIGHT_WITCH"
    NIGHT_SEER = "NIGHT_SEER"
    DAY_DISCUSSION = "DAY_DISCUSSION"
    DAY_VOTE = "DAY_VOTE"
    HUNTER_SHOOT = "HUNTER_SHOOT"
    CHECK_VICTORY = "CHECK_VICTORY"
    GAME_END = "GAME_END"


class GameState(BaseModel):
    """Represents the complete game state.

    Attributes:
        game_id: Unique game identifier
        mode: Game mode key (e.g., "classic_6_witch")
        phase: Current game phase
        round_number: Current round number (day/night cycle)
        day_number: Current day number
        players: List of all players
        events: Game event history
        memory_base: Path to memory storage directory
        winner: Winning team (None if game ongoing)
    """

    game_id: str
    mode: str = "classic_6_witch"
    phase: GamePhase = GamePhase.SETUP
    round_number: int = 0
    day_number: int = 0
    players: list[Player] = Field(default_factory=list)
    events: list[GameEvent] = Field(default_factory=list)
    memory_base: Path | None = Field(default=None, exclude=True)
    winner: str | None = None
    human_identity: str = Field(default="local", exclude=True)

    # Persistent cross-round state
    seer_checks: dict[str, str] = Field(default_factory=dict)  # Player ID -> "WEREWOLF" | "GOOD" (persists across rounds)
    witch_save_available: bool = True
    witch_poison_available: bool = True

    # Guard state
    guarded_player: str | None = None  # Player ID guarded this round (temporary)
    last_guarded_player: str | None = None  # Player ID guarded last round (cross-round)

    # Temporary storage for current round actions
    night_kills: list[str] = Field(default_factory=list)  # Player IDs killed by werewolves
    saved_player: str | None = None  # Player ID saved by witch
    poisoned_player: str | None = None  # Player ID poisoned by witch
    seer_checked: str | None = None  # Player ID checked by seer
    wolf_ai_suggestion: str | None = None  # AI wolf partner's suggested target name
    wolf_collab_result: dict[str, str] = Field(default_factory=dict)  # Full collaboration result from LLM
    votes: dict[str, str] = Field(default_factory=dict)  # Voter ID -> Target ID
    speeches: list[dict[str, str]] = Field(default_factory=list)  # Player speeches
    speech_order: list[str] = Field(default_factory=list)  # Player IDs in speaking order
    ai_speaking: bool = False  # True = background task is generating AI speeches

    # Deferred winner: set by prefetch when game ends inside background task.
    # NOT exposed in API responses (game_state_response builds response manually).
    # Revealed when the dead player reaches advance-night, preserving suspense.
    deferred_winner: str | None = None
    deferred_events: list[GameEvent] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def get_player_by_id(self, player_id: str) -> Player | None:
        """Get player by ID."""
        for player in self.players:
            if player.id == player_id:
                return player
        return None

    def get_player_by_name(self, name: str) -> Player | None:
        """Get player by name."""
        for player in self.players:
            if player.name == name:
                return player
        return None

    @property
    def alive_players(self) -> list[Player]:
        """Get list of alive players."""
        return [p for p in self.players if p.alive]

    @property
    def alive_werewolves(self) -> list[Player]:
        """Get list of alive werewolves."""
        return [p for p in self.alive_players if p.is_werewolf()]

    @property
    def alive_villagers(self) -> list[Player]:
        """Get list of alive villagers (non-werewolves)."""
        return [p for p in self.alive_players if p.is_villager_team()]

    @property
    def alive_gods(self) -> list[Player]:
        """Get list of alive god roles."""
        return [p for p in self.alive_players if p.is_god_role()]

    def is_game_over(self) -> bool:
        """Check if game has ended."""
        return self.phase == GamePhase.GAME_END

    def snapshot_round_data(self) -> RoundSnapshot:
        """Capture all transient round data into an immutable snapshot.

        Must be called BEFORE reset_round_data() so that background tasks
        (end_round, reflections) see the correct state.
        """
        return RoundSnapshot(
            night_kills=list(self.night_kills),
            saved_player=self.saved_player,
            poisoned_player=self.poisoned_player,
            seer_checked=self.seer_checked,
            guarded_player=self.guarded_player,
            wolf_collab_result=dict(self.wolf_collab_result),
            votes=dict(self.votes),
            speeches=list(self.speeches),
        )

    def reset_round_data(self) -> None:
        """Reset temporary round data."""
        self.last_guarded_player = self.guarded_player
        self.guarded_player = None
        self.night_kills = []
        self.saved_player = None
        self.poisoned_player = None
        self.seer_checked = None
        self.wolf_ai_suggestion = None
        self.wolf_collab_result = {}
        self.votes = {}
        self.speeches = []
        self.speech_order = []
