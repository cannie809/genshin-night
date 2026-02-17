"""Role system for the werewolf game.

This module defines the abstract Role class and concrete implementations
for all game roles: Werewolf, Seer, Witch, Hunter, Villager.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.models import GameState, Player


class Role(ABC):
    """Abstract base class for all roles.

    Each role defines:
    - role_name: Unique role identifier
    - team: "werewolf" or "villager"
    - night_action: What the role does at night (if anything)
    - can_act_at_night: Whether the role has a night action
    - on_death: Special ability triggered on death (e.g., Hunter)
    """

    role_name: str = "unknown"
    team: str = "villager"

    @abstractmethod
    def night_action(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Execute this role's night action.

        Args:
            player: The player with this role
            game_state: Current game state

        Returns:
            Dict with action results
        """
        pass

    def can_act_at_night(self) -> bool:
        """Whether this role has an active night action.

        Returns:
            True if role can act at night
        """
        return False

    def on_death(self, player: "Player", game_state: "GameState") -> dict[str, Any] | None:
        """Special ability triggered when this role dies.

        Args:
            player: The player who died
            game_state: Current game state

        Returns:
            Dict with death trigger results, or None if no special ability
        """
        return None


class Werewolf(Role):
    """Werewolf role - kills villagers at night."""

    role_name = "werewolf"
    team = "werewolf"

    def night_action(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Werewolves collaborate to choose a target.

        This is handled by the AI agent (werewolf_collaborate method).
        The actual kill is recorded in game_state.night_kills.

        Returns:
            Empty dict (action is handled externally)
        """
        return {}

    def can_act_at_night(self) -> bool:
        return True


class Seer(Role):
    """Seer role - can check one player's identity each night."""

    role_name = "seer"
    team = "villager"

    def night_action(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Check a player's identity.

        This is handled by the AI agent (seer_investigate method).
        The result is recorded in the player's memory.

        Returns:
            Empty dict (action is handled externally)
        """
        return {}

    def can_act_at_night(self) -> bool:
        return True


class Witch(Role):
    """Witch role - has one save potion and one poison potion."""

    role_name = "witch"
    team = "villager"

    def night_action(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Use save or poison potion.

        This is handled by the AI agent (witch_action method).
        The action is recorded in game_state.saved_player or poisoned_player.

        Returns:
            Empty dict (action is handled externally)
        """
        return {}

    def can_act_at_night(self) -> bool:
        return True


class Hunter(Role):
    """Hunter role - can shoot someone when they die."""

    role_name = "hunter"
    team = "villager"

    def night_action(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Hunter has no active night action."""
        return {}

    def can_act_at_night(self) -> bool:
        return False

    def on_death(self, player: "Player", game_state: "GameState") -> dict[str, Any] | None:
        """Shoot someone when hunter dies.

        This is handled by the AI agent (hunter_shoot method).

        Returns:
            Dict with shoot target info
        """
        # Return a marker that hunter death was triggered
        # The actual shooting is handled by GameEngine
        return {"trigger": "hunter_shoot", "hunter_id": player.id}


class Guard(Role):
    """Guard role - can protect one player each night."""

    role_name = "guard"
    team = "villager"

    def night_action(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Guard protects a player.

        This is handled by the AI agent (guard_protect method).
        The action is recorded in game_state.guarded_player.

        Returns:
            Empty dict (action is handled externally)
        """
        return {}

    def can_act_at_night(self) -> bool:
        return True


class Villager(Role):
    """Villager role - no special abilities."""

    role_name = "villager"
    team = "villager"

    def night_action(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Villagers have no night action."""
        return {}

    def can_act_at_night(self) -> bool:
        return False


# Role registry for easy instantiation
ROLE_REGISTRY: dict[str, type[Role]] = {
    "werewolf": Werewolf,
    "seer": Seer,
    "witch": Witch,
    "hunter": Hunter,
    "guard": Guard,
    "villager": Villager,
}


def get_role(role_name: str) -> Role:
    """Get role instance by name.

    Args:
        role_name: Role name (e.g., "werewolf")

    Returns:
        Role instance

    Raises:
        ValueError: If role name is unknown
    """
    role_class = ROLE_REGISTRY.get(role_name)
    if not role_class:
        raise ValueError(f"Unknown role: {role_name}")
    return role_class()
