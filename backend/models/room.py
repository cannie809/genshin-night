"""Room/lobby data models.

Rooms are pre-allocated in a fixed pool by RoomManager. A Room holds the
pre-game state (who's in, what role each wants, what mode is selected, who
is the host). Once the host clicks start, the room spawns a GameState and
stores its id; the Room itself does not participate in the in-game phase
machinery.
"""

from __future__ import annotations

from enum import Enum
from time import time

from pydantic import BaseModel, Field


class RoomStatus(str, Enum):
    """Lifecycle of a room.

    `waiting`  — accepting joins; host may adjust mode and start.
    `starting` — host has clicked start; transient state while we build
                 the GameState and decide slot/role assignment.
    `in_progress` — a GameState is live; room displays "进行中".
    `finished` — game ended; host can restart to return to `waiting`
                 (in-place restart keeps the same room and members).
    """

    WAITING = "waiting"
    STARTING = "starting"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"


class RoomPlayer(BaseModel):
    """A human participant in a room before/after the game runs."""

    identity: str
    display_name: str
    # Self-only field: the role the user prefers. NEVER expose to other
    # players via the API (see router's `scrub_for_viewer` helper). This is
    # enforced at the HTTP boundary, not at the model level, so tests can
    # still inspect full state directly.
    preferred_role: str | None = None
    # Non-host "ready" flag. Hosts don't need this — they just click start.
    # Purely advisory right now: host sees "N/M 已准备" but isn't blocked
    # from starting. Reset to False on restart/start.
    ready: bool = False
    joined_at: float = Field(default_factory=time)


class Room(BaseModel):
    """Aggregate room state."""

    id: str
    name: str
    status: RoomStatus = RoomStatus.WAITING
    host_identity: str | None = None
    players: list[RoomPlayer] = Field(default_factory=list)
    # Game mode key (matches GAME_MODES). The host can change this while
    # status == WAITING.
    mode: str = "classic_6_witch"
    # Present only while status in {IN_PROGRESS, FINISHED} — points at the
    # GameState the room spawned. `restart_room` clears it.
    game_id: str | None = None
    created_at: float = Field(default_factory=time)
    started_at: float | None = None

    def find_player(self, identity: str) -> RoomPlayer | None:
        """Return the RoomPlayer for this identity or None."""
        for p in self.players:
            if p.identity == identity:
                return p
        return None

    def is_host(self, identity: str) -> bool:
        """Whether the given identity is the current host."""
        return self.host_identity == identity
