"""Victory condition checker for the werewolf game."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.models import GameState


def check_victory(game_state: "GameState") -> str | None:
    """Check if any team has won the game.

    Victory conditions:
    - Good team wins: All werewolves eliminated
    - Werewolf team wins: Werewolf count >= good-side count (parity)

    Args:
        game_state: Current game state

    Returns:
        "good" if good team won, "werewolf" if werewolves won, None if game ongoing
    """
    alive_werewolves = game_state.alive_werewolves
    alive_villagers = game_state.alive_villagers  # All non-werewolves

    # Good team win: All werewolves eliminated
    if len(alive_werewolves) == 0:
        return "good"

    # Werewolf win: Parity - werewolf count >= all good-side count
    if len(alive_werewolves) >= len(alive_villagers):
        return "werewolf"

    # Game continues
    return None


def get_victory_message(winner: str) -> str:
    """Get victory message for winning team.

    Args:
        winner: Winning team ("good" or "werewolf")

    Returns:
        Victory message string
    """
    if winner == "good":
        return "🏘️ 好人阵营获胜！所有狼人已被消灭！"
    elif winner == "werewolf":
        return "🐺 狼人阵营获胜！村庄已经沦陷！"
    else:
        return "Game ongoing..."
