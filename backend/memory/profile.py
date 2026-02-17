"""Profile layer manager - wrapper for personality profile operations.

This module provides a high-level interface for managing player personality profiles.
"""

from pathlib import Path

from .generators import generate_personality_profile
from .storage import MemoryStorage


class ProfileManager:
    """Manages personality profiles for players.

    Args:
        memory_base: Base directory for all memory files
        game_id: Game ID to isolate memories per game
    """

    def __init__(self, memory_base: Path | str, game_id: str = "default"):
        self.memory_base = Path(memory_base)
        self.game_id = game_id

    def initialize_player_profile(
        self,
        player_id: str,
        player_name: str,
        personality_type: str,
    ) -> None:
        """Initialize personality profile for a player.

        Args:
            player_id: Player ID (e.g., "player_1")
            player_name: Player display name
            personality_type: One of LOGICAL, INTUITIVE, SOPHIST, CONTRARIAN, AGGRESSIVE, CONSERVATIVE
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)

        # Generate profile content
        content = generate_personality_profile(personality_type, player_name)

        # Create profile file
        storage.create_profile_personality(content)

    def read_player_profile(self, player_id: str) -> str:
        """Read personality profile for a player.

        Args:
            player_id: Player ID

        Returns:
            Markdown content of personality profile
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)
        return storage.read_profile_personality()
