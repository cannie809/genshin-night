"""Event records manager - wrapper for day/night record operations.

This module provides a high-level interface for managing event records.
"""

from pathlib import Path
from typing import Any

from .generators import generate_day_record, generate_night_record
from .storage import MemoryStorage


class RecordsManager:
    """Manages event records (day and night) for the game.

    Args:
        memory_base: Base directory for all memory files
        game_id: Game ID to isolate memories per game
    """

    def __init__(self, memory_base: Path | str, game_id: str = "default"):
        self.memory_base = Path(memory_base)
        self.game_id = game_id

    def create_day_record(
        self,
        round_num: int,
        morning_deaths: list[dict[str, str]],
        speeches: list[dict[str, str]],
        votes: dict[str, str],
        eliminated: dict[str, Any] | None,
    ) -> None:
        """Create a day record for a round.

        Args:
            round_num: Round number
            morning_deaths: List of dicts with 'name' of players who died last night
            speeches: List of dicts with 'player' and 'content' keys
            votes: Dict mapping voter name to target name
            eliminated: Dict with 'name' and 'votes' of eliminated player
        """
        content = generate_day_record(
            round_num=round_num,
            morning_deaths=morning_deaths,
            speeches=speeches,
            votes=votes,
            eliminated=eliminated,
        )

        # Day records are shared across all players
        storage = MemoryStorage("day_record_shared", self.memory_base, self.game_id)
        storage.create_day_record_shared(round_num, content)

    def create_night_record(
        self,
        player_id: str,
        player_name: str,
        role: str,
        night_num: int,
        action: dict[str, Any] | None = None,
    ) -> None:
        """Create a night record for a specific player.

        Args:
            player_id: Player ID (e.g., "player_1")
            player_name: Player display name
            role: Player role
            night_num: Night number
            action: Dict with action details (varies by role)
        """
        content = generate_night_record(
            player_name=player_name,
            role=role,
            night_num=night_num,
            action=action,
        )

        storage = MemoryStorage(player_id, self.memory_base, self.game_id)
        storage.create_night_record(night_num, content)

    def read_day_records(self, num_rounds: int = 3) -> list[str]:
        """Read recent day records.

        Args:
            num_rounds: Number of recent rounds to read

        Returns:
            List of day record markdown content
        """
        storage = MemoryStorage("day_record_shared", self.memory_base, self.game_id)
        return storage.read_day_record_shared(num_rounds)

    def read_night_records(self, player_id: str, num_nights: int = 3) -> list[str]:
        """Read recent night records for a player.

        Args:
            player_id: Player ID
            num_nights: Number of recent nights to read

        Returns:
            List of night record markdown content
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)
        return storage.read_night_record(num_nights)
