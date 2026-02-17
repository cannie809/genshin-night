"""AI agent module for the werewolf game."""

from .agent import UnifiedGameAgent
from .personality import (
    CHARACTER_PRESETS,
    CharacterPreset,
    PERSONALITY_TYPES,
    assign_personalities,
    select_characters,
)

__all__ = [
    "UnifiedGameAgent",
    "CHARACTER_PRESETS",
    "CharacterPreset",
    "PERSONALITY_TYPES",
    "assign_personalities",
    "select_characters",
]
