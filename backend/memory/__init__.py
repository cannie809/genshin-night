"""Memory system for the werewolf game.

This module implements a three-layer memory architecture:
- Layer 1: Records (事件记录层) - Public day records + Private night records
- Layer 2: Knowledge (知识层) - Role-specific strategies and facts
- Layer 3: Profile (个性档案层) - Static personality traits
"""

from .storage import MemoryStorage
from .generators import (
    generate_day_record,
    generate_night_record,
    generate_personality_profile,
    generate_initial_knowledge_summary,
    generate_initial_knowledge_json,
)
from .records import RecordsManager
from .knowledge import KnowledgeManager
from .profile import ProfileManager
from .event_index import EventIndexManager
from .strategy_tracker import StrategyTracker
from .player_profiler import PlayerProfiler
from .reflection import ReflectionPipeline

__all__ = [
    # Core storage
    "MemoryStorage",
    # Generators
    "generate_day_record",
    "generate_night_record",
    "generate_personality_profile",
    "generate_initial_knowledge_summary",
    "generate_initial_knowledge_json",
    # Managers
    "RecordsManager",
    "KnowledgeManager",
    "ProfileManager",
    # New modules
    "EventIndexManager",
    "StrategyTracker",
    "PlayerProfiler",
    "ReflectionPipeline",
]
