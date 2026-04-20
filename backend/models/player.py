"""Player model for the werewolf game."""

from pydantic import BaseModel


class Player(BaseModel):
    """Represents a player in the game.

    Attributes:
        id: Unique player slot identifier inside this game (e.g., "player_1").
            Stable for the game's lifetime; unrelated to human identity.
        name: Display name (usually the character name, e.g., "温迪").
        role: Role name (werewolf, seer, witch, hunter, villager).
        personality: Personality type for AI players.
        is_human: Whether this slot is controlled by a human.
        identity: For human slots, the stable UUID identifying the human
            across sessions (used for auth + ack bookkeeping). None for AI.
        display_name: Optional user-chosen nickname for human slots; falls
            back to `name` when empty.
        alive: Whether the player is still alive.
    """

    id: str
    name: str
    role: str
    personality: str = "LOGICAL"
    avatar_url: str | None = None
    is_human: bool = False
    identity: str | None = None
    display_name: str | None = None
    alive: bool = True

    def is_werewolf(self) -> bool:
        """Check if player is a werewolf."""
        return self.role == "werewolf"

    def is_villager_team(self) -> bool:
        """Check if player is on the villager team."""
        return self.role != "werewolf"

    def is_god_role(self) -> bool:
        """Check if player has a special ability (神牌)."""
        return self.role in ["seer", "witch", "hunter", "guard"]
