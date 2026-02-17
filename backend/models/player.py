"""Player model for the werewolf game."""

from pydantic import BaseModel, Field


class Player(BaseModel):
    """Represents a player in the game.

    Attributes:
        id: Unique player identifier (e.g., "player_1")
        name: Display name
        role: Role name (werewolf, seer, witch, hunter, villager)
        personality: Personality type for AI players
        is_human: Whether this is the human player
        alive: Whether the player is still alive
    """

    id: str
    name: str
    role: str
    personality: str = "LOGICAL"
    avatar_url: str | None = None
    is_human: bool = False
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
