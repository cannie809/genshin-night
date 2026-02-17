"""Game mode configurations for different role setups.

This module defines the available game modes and their role compositions.
"""

from typing import NamedTuple


class GameModeConfig(NamedTuple):
    """Configuration for a game mode.

    Attributes:
        name: Display name of the mode
        roles: List of role names (length must be 6 for MVP)
        description: Brief description of the mode
    """

    name: str
    roles: list[str]
    description: str


# Define available game modes
GAME_MODES: dict[str, GameModeConfig] = {
    "classic_6_witch": GameModeConfig(
        name="Classic 6 (Witch)",
        roles=["werewolf", "werewolf", "seer", "witch", "villager", "villager"],
        description="2 Werewolves, 1 Seer, 1 Witch, 2 Villagers",
    ),
    "classic_6_hunter": GameModeConfig(
        name="Classic 6 (Hunter)",
        roles=["werewolf", "werewolf", "seer", "hunter", "villager", "villager"],
        description="2 Werewolves, 1 Seer, 1 Hunter, 2 Villagers",
    ),
    "classic_6_guard": GameModeConfig(
        name="Classic 6 (Guard)",
        roles=["werewolf", "werewolf", "seer", "guard", "villager", "villager"],
        description="2 Werewolves, 1 Seer, 1 Guard, 2 Villagers",
    ),
}

# Default mode
DEFAULT_MODE = "classic_6_witch"
