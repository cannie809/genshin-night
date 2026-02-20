"""Game engine for the werewolf game.

This module contains the core game logic including:
- Game initialization
- Phase transitions
- Night/day action handling
- Memory system integration
"""

import logging
import random
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

from backend.config import DEFAULT_MODE, GAME_MODES
from backend.memory import KnowledgeManager, ProfileManager, RecordsManager
from backend.memory.event_index import EventIndexManager
from backend.memory.player_profiler import PlayerProfiler
from backend.memory.reflection import ReflectionPipeline
from backend.models import GameEvent, GamePhase, GameState, RoundSnapshot, Player

from backend.ai.personality import select_characters

from .roles import get_role
from .victory import check_victory, get_victory_message


class GameEngine:
    """Core game engine managing game state and flow.

    Args:
        memory_base: Path to memory storage directory
    """

    def __init__(self, memory_base: Path | str = None):
        if memory_base is None:
            memory_base = Path(__file__).parent.parent / ".memory"
        self.memory_base = Path(memory_base)

        # Memory managers are created per-game (see _get_memory_managers)

    def _get_memory_managers(self, game_id: str, memory_base: Path = None):
        """Get memory managers for a specific game.

        Args:
            game_id: Game ID to get managers for
            memory_base: Player-scoped memory base (falls back to self.memory_base)

        Returns:
            Tuple of (records_manager, knowledge_manager, profile_manager)
        """
        mb = memory_base or self.memory_base
        records_manager = RecordsManager(mb, game_id)
        knowledge_manager = KnowledgeManager(mb, game_id)
        profile_manager = ProfileManager(mb, game_id)
        return records_manager, knowledge_manager, profile_manager

    def _get_event_index(self, game_id: str, memory_base: Path = None) -> EventIndexManager:
        """Get event index manager for a specific game.

        Args:
            game_id: Game ID to get event index for
            memory_base: Player-scoped memory base (falls back to self.memory_base)

        Returns:
            EventIndexManager instance
        """
        mb = memory_base or self.memory_base
        return EventIndexManager(mb, game_id)

    def create_game(
        self, mode: str = DEFAULT_MODE, preferred_role: str | None = None, human_identity: str = "local"
    ) -> GameState:
        """Create a new game with specified mode.

        Args:
            mode: Game mode key (e.g., "classic_6_witch")
            preferred_role: Role the human player wants, or None for random
            human_identity: Player identity for per-user memory isolation

        Returns:
            Initialized game state

        Raises:
            ValueError: If mode is invalid
        """
        if mode not in GAME_MODES:
            raise ValueError(f"Invalid game mode: {mode}. Available: {list(GAME_MODES.keys())}")

        game_config = GAME_MODES[mode]
        game_id = str(uuid.uuid4())

        # Player-scoped memory base: .memory/{human_identity}/
        player_memory_base = self.memory_base / human_identity

        # Create game state
        game_state = GameState(
            game_id=game_id,
            mode=mode,
            phase=GamePhase.SETUP,
            round_number=0,
            day_number=0,
            memory_base=player_memory_base,
            human_identity=human_identity,
        )

        # Assign roles
        roles = game_config.roles.copy()
        random.shuffle(roles)

        # If player has a preferred role, ensure they get it
        if preferred_role and preferred_role in roles:
            roles.remove(preferred_role)
            human_role = preferred_role
        else:
            human_role = roles.pop(0)

        # Create players
        # Player 0 is always the human player
        game_state.players.append(
            Player(
                id="player_0",
                name="旅行者",
                role=human_role,
                personality="HUMAN",
                avatar_url="/avatars/UI_AvatarIcon_Traveler.png",
                is_human=True,
                alive=True,
            )
        )

        # Create AI players (roles list now has the remaining roles after human's was taken)
        ai_count = len(roles)
        characters = select_characters(ai_count)

        for i in range(ai_count):
            game_state.players.append(
                Player(
                    id=f"player_{i + 1}",
                    name=characters[i].name,
                    role=roles[i],
                    personality=characters[i].id,
                    avatar_url=characters[i].avatar_url,
                    is_human=False,
                    alive=True,
                )
            )

        # Clean up old game directories (keep most recent 10) — scoped to this player
        profiler = PlayerProfiler(player_memory_base)
        try:
            cleaned = profiler.cleanup_old_games()
            if cleaned:
                log.info(f"[PlayerProfiler] Cleaned {cleaned} old game directories for {human_identity}")
        finally:
            profiler.close()

        # Initialize memory system for all players
        self._initialize_memories(game_state)

        # Initialize event index
        event_index = EventIndexManager(player_memory_base, game_id)
        event_index.initialize()

        # Add setup event
        game_state.events.append(
            GameEvent(
                type="game_start",
                round=0,
                phase="SETUP",
                message=f"游戏开始！模式: {game_config.name}",
            )
        )

        # Move to first night phase (guard acts before werewolf if present)
        has_guard = "guard" in game_config.roles
        game_state.phase = GamePhase.NIGHT_GUARD if has_guard else GamePhase.NIGHT_WEREWOLF
        game_state.round_number = 1

        return game_state

    def _initialize_memories(self, game_state: GameState) -> None:
        """Initialize memory system for all players.

        Args:
            game_state: Game state with players
        """
        # Get memory managers for this specific game (player-scoped)
        _, knowledge_manager, profile_manager = self._get_memory_managers(game_state.game_id, game_state.memory_base)

        # Initialize individual player memories
        for player in game_state.players:
            # Create personality profile
            profile_manager.initialize_player_profile(
                player_id=player.id,
                player_name=player.name,
                personality_type=player.personality if not player.is_human else "HUMAN",
            )

            # Create knowledge layer
            knowledge_manager.initialize_player_knowledge(
                player_id=player.id,
                role=player.role,
                game_mode=game_state.mode,
            )

        # Initialize werewolf shared memory if there are werewolves
        werewolves = [p for p in game_state.players if p.role == "werewolf"]
        if werewolves:
            knowledge_manager.initialize_werewolf_shared()

    def next_phase(self, game_state: GameState) -> GamePhase:
        """Transition to the next game phase.

        Args:
            game_state: Current game state

        Returns:
            New game phase
        """
        phase_order = [
            GamePhase.NIGHT_GUARD,
            GamePhase.NIGHT_WEREWOLF,
            GamePhase.NIGHT_WITCH,
            GamePhase.NIGHT_SEER,
            GamePhase.DAY_DISCUSSION,
            GamePhase.DAY_VOTE,
            GamePhase.CHECK_VICTORY,
        ]

        current_phase = game_state.phase

        # Handle CHECK_VICTORY phase
        if current_phase == GamePhase.CHECK_VICTORY:
            winner = check_victory(game_state)
            if winner:
                game_state.phase = GamePhase.GAME_END
                game_state.winner = winner
                game_state.events.append(
                    GameEvent(
                        type="game_end",
                        round=game_state.round_number,
                        phase="GAME_END",
                        message=get_victory_message(winner),
                    )
                )
                # Distill human player behavior profile
                try:
                    profiler = PlayerProfiler(game_state.memory_base)
                    try:
                        human_role = next((p.role for p in game_state.players if p.is_human), None)
                        result = profiler.distill_game(
                            game_state.game_id,
                            game_state,
                            getattr(self, "_llm_call_fn", None),
                        )
                        if result["count"] > 0:
                            log.info(
                                f"[PlayerProfiler] Distilled {result['count']} observations from game {game_state.game_id[:8]}..."
                            )
                            rl = profiler.reinforce(
                                game_state.game_id, game_state.winner, human_role, matched_ids=result["matched_ids"]
                            )
                            if rl["reinforced"] or rl["penalized"]:
                                log.info(f"[PlayerProfiler] RL update: +{rl['reinforced']} -{rl['penalized']}")
                            profiler.decay_unreinforced(game_state.game_id, rl["adjusted_ids"])
                            profiler.prune_stale()
                        else:
                            log.info(
                                f"[PlayerProfiler] No observations to distill from game {game_state.game_id[:8]}..."
                            )
                    finally:
                        profiler.close()
                except Exception as e:
                    log.warning(f"[PlayerProfiler] Distillation failed: {e}")
                return GamePhase.GAME_END
            else:
                # Start new round
                game_state.round_number += 1
                game_state.reset_round_data()
                # Guard acts first if present in this mode
                has_guard = any(p.role == "guard" for p in game_state.players)
                first_phase = GamePhase.NIGHT_GUARD if has_guard else GamePhase.NIGHT_WEREWOLF
                game_state.phase = first_phase
                return first_phase

        # Handle HUNTER_SHOOT phase
        if current_phase == GamePhase.HUNTER_SHOOT:
            # After hunter shoots, check victory
            game_state.phase = GamePhase.CHECK_VICTORY
            return GamePhase.CHECK_VICTORY

        # Normal phase transitions
        try:
            current_index = phase_order.index(current_phase)
            next_phase = phase_order[current_index + 1]

            # Skip NIGHT_GUARD if guard is dead or mode has no guard
            if next_phase == GamePhase.NIGHT_GUARD:
                guard = next((p for p in game_state.alive_players if p.role == "guard"), None)
                has_guard_role = any(p.role == "guard" for p in game_state.players)
                if not guard or not has_guard_role:
                    next_phase = GamePhase.NIGHT_WEREWOLF

            # Skip NIGHT_WITCH if witch is dead or mode has no witch
            if next_phase == GamePhase.NIGHT_WITCH:
                witch = next((p for p in game_state.alive_players if p.role == "witch"), None)
                has_witch_role = any(p.role == "witch" for p in game_state.players)
                if not witch or not has_witch_role:
                    next_phase = GamePhase.NIGHT_SEER

            # Skip NIGHT_SEER if seer is dead
            if next_phase == GamePhase.NIGHT_SEER:
                seer = next((p for p in game_state.alive_players if p.role == "seer"), None)
                if not seer:
                    next_phase = GamePhase.DAY_DISCUSSION

            # After DAY_VOTE, check if hunter was eliminated
            if current_phase == GamePhase.DAY_VOTE:
                # Check if anyone was eliminated and if they were hunter
                if game_state.votes:
                    # This will be handled by process_vote method
                    pass

            game_state.phase = next_phase
            return next_phase

        except (ValueError, IndexError) as e:
            # If phase not in order or at end, stay in current phase
            log.error(f"[Engine] Phase transition error from {current_phase}: {e}")
            return current_phase

    def process_night_guard(self, game_state: GameState, target_id: str | None) -> dict[str, any]:
        """Process guard night protection.

        Args:
            game_state: Current game state
            target_id: ID of player to protect, or None to skip (空守)

        Returns:
            Result dict
        """
        # Guard chooses not to protect anyone (空守)
        if not target_id:
            game_state.guarded_player = None
            game_state.events.append(
                GameEvent(
                    type="guard_skip",
                    round=game_state.round_number,
                    phase="NIGHT_GUARD",
                    data={},
                    message="守卫选择不守护任何人",
                )
            )
            return {"success": True, "target": None, "skipped": True}

        target = game_state.get_player_by_id(target_id)
        if not target or not target.alive:
            return {"success": False, "error": "Invalid target"}

        # Check consecutive guard restriction
        if target_id == game_state.last_guarded_player:
            return {"success": False, "error": "Cannot guard same player two nights in a row"}

        game_state.guarded_player = target_id

        game_state.events.append(
            GameEvent(
                type="guard_protect",
                round=game_state.round_number,
                phase="NIGHT_GUARD",
                data={"target_id": target_id},
                message=f"守卫守护了 {target.name}",
            )
        )

        return {"success": True, "target": target.name}

    def process_night_werewolf(self, game_state: GameState, target_id: str) -> dict[str, any]:
        """Process werewolf night kill.

        Args:
            game_state: Current game state
            target_id: ID of player to kill

        Returns:
            Result dict
        """
        target = game_state.get_player_by_id(target_id)
        if not target or not target.alive:
            return {"success": False, "error": "Invalid target"}

        # Record the kill (actual death happens after witch phase)
        game_state.night_kills.append(target_id)

        # Add event
        game_state.events.append(
            GameEvent(
                type="werewolf_kill",
                round=game_state.round_number,
                phase="NIGHT_WEREWOLF",
                data={"target_id": target_id},
                message=f"狼人决定击杀 {target.name}",
            )
        )

        return {"success": True, "target": target.name}

    def process_night_witch(
        self,
        game_state: GameState,
        use_save: bool = False,
        use_poison: bool = False,
        poison_target_id: str | None = None,
    ) -> dict[str, any]:
        """Process witch night action.

        Args:
            game_state: Current game state
            use_save: Whether to use save potion
            use_poison: Whether to use poison potion
            poison_target_id: ID of player to poison

        Returns:
            Result dict
        """
        # Get victim from werewolf kill
        victim_id = game_state.night_kills[0] if game_state.night_kills else None

        log.info(
            f"[Witch] use_save={use_save} use_poison={use_poison} victim={victim_id} save_avail={game_state.witch_save_available} poison_avail={game_state.witch_poison_available}"
        )

        # Guard: only allow save if potion is still available
        if use_save and victim_id and game_state.witch_save_available:
            game_state.saved_player = victim_id
            game_state.witch_save_available = False
            game_state.events.append(
                GameEvent(
                    type="witch_save",
                    round=game_state.round_number,
                    phase="NIGHT_WITCH",
                    data={"saved_id": victim_id},
                    message="女巫使用了解药",
                )
            )

        # Guard: only allow poison if potion is still available
        if use_poison and poison_target_id and game_state.witch_poison_available:
            game_state.poisoned_player = poison_target_id
            game_state.witch_poison_available = False
            game_state.events.append(
                GameEvent(
                    type="witch_poison",
                    round=game_state.round_number,
                    phase="NIGHT_WITCH",
                    data={"poisoned_id": poison_target_id},
                    message="女巫使用了毒药",
                )
            )

        # Return actual result (not just requested action)
        actually_saved = game_state.saved_player is not None
        actually_poisoned = game_state.poisoned_player is not None
        return {"success": True, "saved": actually_saved, "poisoned": actually_poisoned}

    def process_night_seer(self, game_state: GameState, target_id: str) -> dict[str, any]:
        """Process seer night investigation.

        Args:
            game_state: Current game state
            target_id: ID of player to check

        Returns:
            Result dict with investigation result
        """
        target = game_state.get_player_by_id(target_id)
        if not target or not target.alive:
            return {"success": False, "error": "Invalid target"}

        result = "WEREWOLF" if target.is_werewolf() else "GOOD"

        game_state.seer_checked = target_id
        game_state.seer_checks[target_id] = result

        game_state.events.append(
            GameEvent(
                type="seer_check",
                round=game_state.round_number,
                phase="NIGHT_SEER",
                data={"target_id": target_id, "result": result},
                message=f"预言家查验了 {target.name}",
            )
        )

        return {"success": True, "target": target.name, "result": result}

    def process_morning(self, game_state: GameState) -> list[str]:
        """Process morning deaths after night phase.

        Args:
            game_state: Current game state

        Returns:
            List of player IDs who died
        """
        dead_ids = []

        log.info(
            f"[Morning] round={game_state.round_number} night_kills={game_state.night_kills} saved_player={game_state.saved_player}"
        )

        # Process werewolf kills
        for victim_id in game_state.night_kills:
            # Check if victim was saved by witch or protected by guard
            if victim_id == game_state.saved_player:
                log.info(f"[Morning] {victim_id} was saved by witch")
            elif victim_id == game_state.guarded_player:
                log.info(f"[Morning] {victim_id} was protected by guard")
            else:
                victim = game_state.get_player_by_id(victim_id)
                if victim:
                    victim.alive = False
                    dead_ids.append(victim_id)
                    log.info(f"[Morning] {victim.name} ({victim_id}) killed by werewolves")
                    # Record death in event index
                    event_index = self._get_event_index(game_state.game_id, game_state.memory_base)
                    event_index.record_death(
                        round_num=game_state.round_number,
                        player_name=victim.name,
                        cause="werewolf_kill",
                        is_night=True,
                    )

        # Process witch poison
        if game_state.poisoned_player:
            poisoned = game_state.get_player_by_id(game_state.poisoned_player)
            if poisoned and poisoned.alive:
                poisoned.alive = False
                dead_ids.append(game_state.poisoned_player)
                # Record death in event index
                event_index = self._get_event_index(game_state.game_id, game_state.memory_base)
                event_index.record_death(
                    round_num=game_state.round_number,
                    player_name=poisoned.name,
                    cause="witch_poison",
                    is_night=True,
                )

        # Check for hunter death trigger (night deaths)
        for dead_id in dead_ids:
            dead_player = game_state.get_player_by_id(dead_id)
            if dead_player and dead_player.role == "hunter":
                role = get_role("hunter")
                death_result = role.on_death(dead_player, game_state)
                if death_result:
                    # Hunter will shoot (handled by AI agent)
                    game_state.events.append(
                        GameEvent(
                            type="hunter_death_night",
                            round=game_state.round_number,
                            phase="DAY_DISCUSSION",
                            data=death_result,
                            message=f"猎人 {dead_player.name} 夜间死亡，发动技能",
                        )
                    )

        return dead_ids

    def add_speech(self, game_state: GameState, player_id: str, content: str) -> None:
        """Add a player speech during day discussion.

        Deduplicates: if the player already has a speech this round, skip silently.

        Args:
            game_state: Current game state
            player_id: Player ID
            content: Speech content
        """
        player = game_state.get_player_by_id(player_id)
        if not player:
            return

        # Prevent duplicate speeches from prefetch + background task race
        if any(s["player"] == player.name for s in game_state.speeches):
            log.info(f"[Speech] Skipping duplicate for {player.name} (already spoke this round)")
            return

        game_state.speeches.append({"player": player.name, "content": content})

    def add_vote(self, game_state: GameState, voter_id: str, target_id: str) -> None:
        """Add a vote during day voting.

        Args:
            game_state: Current game state
            voter_id: Voter player ID
            target_id: Target player ID or "ABSTAIN"
        """
        # Validate target is alive (skip for abstain)
        if target_id != "ABSTAIN":
            target = game_state.get_player_by_id(target_id)
            if not target or not target.alive:
                log.warning(f"[Vote] Rejected vote for dead/invalid target: {target_id}")
                return
        game_state.votes[voter_id] = target_id

    def process_vote(self, game_state: GameState) -> dict[str, any]:
        """Process voting results and eliminate player if needed.

        Args:
            game_state: Current game state

        Returns:
            Result dict with elimination info
        """
        if not game_state.votes:
            return {"success": False, "error": "No votes cast"}

        # Separate abstain votes from real votes
        real_votes: dict[str, str] = {}
        abstain_voters: list[str] = []
        for voter_id, target_id in game_state.votes.items():
            if target_id == "ABSTAIN":
                abstain_voters.append(voter_id)
            else:
                real_votes[voter_id] = target_id

        # Count real votes only
        vote_counts: dict[str, int] = {}
        for target_id in real_votes.values():
            vote_counts[target_id] = vote_counts.get(target_id, 0) + 1

        # Build detailed vote breakdown: [{voter, target}]
        vote_details = []
        for voter_id, target_id in real_votes.items():
            voter = game_state.get_player_by_id(voter_id)
            target = game_state.get_player_by_id(target_id)
            if voter and target:
                vote_details.append({"voter": voter.name, "target": target.name})

        # Add abstain entries to vote_details
        for voter_id in abstain_voters:
            voter = game_state.get_player_by_id(voter_id)
            if voter:
                vote_details.append({"voter": voter.name, "target": "弃权"})

        # Build vote summary: {target_name: {votes: n, voters: [...]}}
        vote_summary = {}
        for detail in vote_details:
            t = detail["target"]
            if t not in vote_summary:
                vote_summary[t] = {"votes": 0, "voters": []}
            vote_summary[t]["votes"] += 1
            vote_summary[t]["voters"].append(detail["voter"])

        # If everyone abstained, treat as tie (no elimination)
        if not vote_counts:
            vote_lines = []
            for target_name, info in sorted(vote_summary.items(), key=lambda x: x[1]["votes"], reverse=True):
                voters_str = "\u3001".join(info["voters"])
                vote_lines.append(f"{target_name}: {info['votes']}\u7968 \u2190 {voters_str}")
            vote_message = "\n".join(vote_lines)
            game_state.events.append(
                GameEvent(
                    type="vote_result",
                    round=game_state.round_number,
                    phase="DAY_VOTE",
                    data={"vote_details": vote_details, "vote_summary": vote_summary, "tie": True},
                    message=f"\u6295\u7968\u7ed3\u679c\uff08\u5168\u5458\u5f03\u6743\uff0c\u65e0\u4eba\u51fa\u5c40\uff09:\n{vote_message}",
                )
            )
            return {"success": True, "tie": True, "vote_details": vote_details, "vote_summary": vote_summary}

        # Find player(s) with most votes
        max_votes = max(vote_counts.values())
        top_voted = [pid for pid, count in vote_counts.items() if count == max_votes]

        # Build vote message lines
        vote_lines = []
        for target_name, info in sorted(vote_summary.items(), key=lambda x: x[1]["votes"], reverse=True):
            voters_str = "、".join(info["voters"])
            vote_lines.append(f"{target_name}: {info['votes']}票 ← {voters_str}")
        vote_message = "\n".join(vote_lines)

        # Check for tie
        if len(top_voted) > 1:
            game_state.events.append(
                GameEvent(
                    type="vote_result",
                    round=game_state.round_number,
                    phase="DAY_VOTE",
                    data={"vote_details": vote_details, "vote_summary": vote_summary, "tie": True},
                    message=f"投票结果（平票，无人出局）:\n{vote_message}",
                )
            )
            return {"success": True, "tie": True, "vote_details": vote_details, "vote_summary": vote_summary}

        # Eliminate top voted player
        eliminated_id = top_voted[0]
        eliminated = game_state.get_player_by_id(eliminated_id)

        if not eliminated:
            return {"success": False, "error": "Invalid elimination target"}

        eliminated.alive = False

        # Record death in event index
        event_index = self._get_event_index(game_state.game_id, game_state.memory_base)
        event_index.record_death(
            round_num=game_state.round_number,
            player_name=eliminated.name,
            cause="vote_elimination",
            is_night=False,
        )

        game_state.events.append(
            GameEvent(
                type="vote_result",
                round=game_state.round_number,
                phase="DAY_VOTE",
                data={
                    "eliminated_id": eliminated_id,
                    "eliminated_name": eliminated.name,
                    "votes": max_votes,
                    "vote_details": vote_details,
                    "vote_summary": vote_summary,
                },
                message=f"投票结果:\n{vote_message}\n\n{eliminated.name} 被投票出局（{max_votes}票）",
            )
        )

        # Check if eliminated player was hunter
        if eliminated.role == "hunter":
            role = get_role("hunter")
            death_result = role.on_death(eliminated, game_state)
            if death_result:
                # Trigger hunter shoot phase
                game_state.phase = GamePhase.HUNTER_SHOOT
                game_state.events.append(
                    GameEvent(
                        type="hunter_death_vote",
                        round=game_state.round_number,
                        phase="HUNTER_SHOOT",
                        data=death_result,
                        message=f"猎人 {eliminated.name} 被投票出局，发动技能",
                    )
                )

        return {
            "success": True,
            "eliminated": eliminated.name,
            "votes": max_votes,
            "vote_details": vote_details,
            "vote_summary": vote_summary,
        }

    def process_hunter_shoot(self, game_state: GameState, target_id: str | None) -> dict[str, any]:
        """Process hunter shooting after death.

        All 4 cases (human/AI × day/night) funnel through here.
        Hunter identity is PUBLIC info — recorded in event_index for all players.

        Args:
            game_state: Current game state
            target_id: ID of player to shoot, or None to hold fire (压枪)

        Returns:
            Result dict
        """
        # Find the hunter (the dead player with role=hunter)
        hunter = next((p for p in game_state.players if p.role == "hunter" and not p.alive), None)
        hunter_name = hunter.name if hunter else "猎人"

        event_index = self._get_event_index(game_state.game_id, game_state.memory_base)

        # Hunter chooses not to shoot (压枪) — identity stays hidden
        if not target_id:
            game_state.events.append(
                GameEvent(
                    type="hunter_hold_fire",
                    round=game_state.round_number,
                    phase="HUNTER_SHOOT",
                    data={"hunter_name": hunter_name},
                    message=f"猎人 {hunter_name} 选择不开枪",
                )
            )
            return {"success": True, "target": None, "held_fire": True, "hunter": hunter_name}

        target = game_state.get_player_by_id(target_id)
        if not target or not target.alive:
            return {"success": False, "error": "Invalid target"}

        target.alive = False

        # Hunter identity is revealed only when actually shooting (public info)
        event_index.record_claim(
            round_num=game_state.round_number,
            player_name=hunter_name,
            claimed_role="hunter",
            status="revealed",
        )

        # Record death in event index
        event_index.record_death(
            round_num=game_state.round_number,
            player_name=target.name,
            cause="hunter_shot",
            is_night=False,
        )

        game_state.events.append(
            GameEvent(
                type="hunter_shoot",
                round=game_state.round_number,
                phase="HUNTER_SHOOT",
                data={"target_id": target_id, "hunter_name": hunter_name, "hunter_id": hunter.id if hunter else None},
                message=f"猎人 {hunter_name} 开枪带走了 {target.name}",
            )
        )

        return {"success": True, "target": target.name, "hunter": hunter_name}

    def end_round(
        self,
        game_state: GameState,
        *,
        round_num: int | None = None,
        snapshot: RoundSnapshot | None = None,
        votes_snapshot: dict | None = None,
        speeches_snapshot: list | None = None,
    ) -> None:
        """End current round and create memory records.

        Args:
            game_state: Current game state
            round_num: Override round number (use when called from background task
                       where game_state.round_number may have already been incremented)
            snapshot: Full round snapshot captured before reset_round_data().
                      Preferred over votes_snapshot/speeches_snapshot.
            votes_snapshot: (Legacy) Pre-captured votes dict
            speeches_snapshot: (Legacy) Pre-captured speeches list
        """
        rn = round_num if round_num is not None else game_state.round_number

        # Build snapshot from explicit params or game_state if not provided
        if snapshot is None:
            snapshot = RoundSnapshot(
                night_kills=list(game_state.night_kills),
                saved_player=game_state.saved_player,
                poisoned_player=game_state.poisoned_player,
                seer_checked=game_state.seer_checked,
                guarded_player=game_state.guarded_player,
                wolf_collab_result=dict(game_state.wolf_collab_result),
                votes=votes_snapshot if votes_snapshot is not None else dict(game_state.votes),
                speeches=speeches_snapshot if speeches_snapshot is not None else list(game_state.speeches),
            )

        votes = snapshot["votes"]
        speeches = snapshot["speeches"]

        # Get memory managers for this specific game
        records_manager, _, _ = self._get_memory_managers(game_state.game_id, game_state.memory_base)

        # Create day record (shared) — only night deaths go in morning announcement
        event_index = self._get_event_index(game_state.game_id, game_state.memory_base)
        index_data = event_index._read_index()
        night_deaths = [d for d in index_data.get("deaths", []) if d["round"] == rn and d.get("night", False)]
        morning_deaths = [{"name": d["player"]} for d in night_deaths]

        # Get voting results
        votes_dict = {}
        for voter_id, target_id in votes.items():
            voter = game_state.get_player_by_id(voter_id)
            if voter:
                if target_id == "ABSTAIN":
                    votes_dict[voter.name] = "弃权"
                else:
                    target = game_state.get_player_by_id(target_id)
                    if target:
                        votes_dict[voter.name] = target.name

        # Find eliminated player
        eliminated = None
        for event in game_state.events:
            if event.type == "vote_result" and event.round == rn:
                eliminated_id = event.data.get("eliminated_id")
                if eliminated_id:
                    eliminated_player = game_state.get_player_by_id(eliminated_id)
                    if eliminated_player:
                        eliminated = {
                            "name": eliminated_player.name,
                            "votes": event.data.get("votes", 0),
                        }

        # Create day record
        records_manager.create_day_record(
            round_num=rn,
            morning_deaths=morning_deaths,
            speeches=speeches,
            votes=votes_dict,
            eliminated=eliminated,
        )

        # Create night records for each player
        self.write_night_records(game_state, round_num=rn, snapshot=snapshot)

        # Run reflection pipeline for alive AI players
        self._run_reflections(game_state, round_num=rn, snapshot=snapshot)

    def write_night_records(
        self, game_state: GameState, *, round_num: int | None = None, snapshot: RoundSnapshot | None = None
    ) -> None:
        """Write night action records for all players.

        Safe to call multiple times per round (overwrites existing files).
        Called early (at morning) so speech prompts can read them,
        and again in end_round with the full snapshot.
        """
        rn = round_num if round_num is not None else game_state.round_number
        records_manager, _, _ = self._get_memory_managers(game_state.game_id, game_state.memory_base)
        for player in game_state.players:
            action_data = self._get_player_night_action(player, game_state, snapshot=snapshot)
            records_manager.create_night_record(
                player_id=player.id,
                player_name=player.name,
                role=player.role,
                night_num=rn,
                action=action_data,
            )

    def _get_player_night_action(
        self, player: Player, game_state: GameState, snapshot: RoundSnapshot | None = None
    ) -> dict[str, any] | None:
        """Get night action data for a player's memory record.

        Args:
            player: Player
            game_state: Game state (for player lookup only)
            snapshot: Round snapshot with night_kills, seer_checked, etc.

        Returns:
            Action data dict or None
        """
        # Read from snapshot (preferred) or fall back to live game_state
        night_kills = snapshot["night_kills"] if snapshot else game_state.night_kills
        seer_checked = snapshot["seer_checked"] if snapshot else game_state.seer_checked
        saved_player = snapshot["saved_player"] if snapshot else game_state.saved_player
        poisoned_player = snapshot["poisoned_player"] if snapshot else game_state.poisoned_player
        guarded_player = snapshot["guarded_player"] if snapshot else game_state.guarded_player
        wolf_collab = snapshot["wolf_collab_result"] if snapshot else game_state.wolf_collab_result

        if player.role == "seer" and seer_checked:
            checked = game_state.get_player_by_id(seer_checked)
            if checked:
                result = "WEREWOLF" if checked.is_werewolf() else "GOOD"
                return {
                    "target": checked.name,
                    "result": result,
                    "thoughts": f"Checked {checked.name} - they are {result}",
                }

        elif player.role == "witch":
            victim = None
            if night_kills:
                victim = game_state.get_player_by_id(night_kills[0])

            saved = saved_player == night_kills[0] if night_kills else False
            poisoned = poisoned_player is not None

            poison_target = None
            if poisoned:
                poison_target = game_state.get_player_by_id(poisoned_player)

            return {
                "victim": victim.name if victim else "None",
                "save_used": saved,
                "poison_used": poisoned,
                "poison_target": poison_target.name if poison_target else None,
                "reasoning": "Strategic decision based on game state",
            }

        elif player.role == "guard" and guarded_player:
            guarded = game_state.get_player_by_id(guarded_player)
            if guarded:
                return {
                    "target": guarded.name,
                    "thoughts": f"守护了 {guarded.name}",
                }

        elif player.role == "werewolf" and night_kills:
            target = game_state.get_player_by_id(night_kills[0])
            collab = wolf_collab
            # Use actual LLM-generated reasoning if available
            if collab:
                # Find which wolf this player is (wolf1 or wolf2)
                is_wolf1 = collab.get("wolf1_name") == player.name
                my_reasoning = collab.get("wolf1_reasoning" if is_wolf1 else "wolf2_reasoning", "")
                my_role = collab.get("wolf1_daytime_role" if is_wolf1 else "wolf2_daytime_role", "")
                team_reasoning = collab.get("team_reasoning", "")
                strategy = collab.get("strategy_summary", "")
                discussion_parts = []
                if team_reasoning:
                    discussion_parts.append(f"决定击杀{target.name if target else '?'}：{team_reasoning}")
                if my_role:
                    discussion_parts.append(f"我的白天任务：{my_role}")
                if strategy:
                    discussion_parts.append(f"整体策略：{strategy}")
                discussion = "；".join(discussion_parts) if discussion_parts else "团队已协商目标"
                reasoning = my_reasoning if my_reasoning else "团队协商决定"
            else:
                discussion = "团队已协商目标"
                reasoning = "团队协商决定"
            return {
                "target": target.name if target else "Unknown",
                "discussion": discussion,
                "reasoning": reasoning,
            }

        return None

    def set_llm_call_fn(self, fn) -> None:
        """Set the LLM call function for reflections.

        Args:
            fn: Callable with signature (prompt: str, max_tokens: int, temperature: float) -> str
        """
        self._llm_call_fn = fn

    def _run_reflections(
        self, game_state: GameState, round_num: int | None = None, snapshot: RoundSnapshot | None = None
    ) -> None:
        """Run reflection pipeline for all alive AI players.

        Each AI player gets one LLM call for reflection.

        Args:
            game_state: Current game state
            round_num: Actual round number (before increment). If None, uses game_state.round_number.
            snapshot: Round data snapshot for accurate event generation.
        """
        # Need access to the agent's LLM call function
        # This will be set by the app layer when creating the engine
        if not hasattr(self, "_llm_call_fn") or not self._llm_call_fn:
            log.info("[Reflection] No LLM function set, skipping reflections")
            return

        pipeline = ReflectionPipeline(
            memory_base=game_state.memory_base or self.memory_base,
            game_id=game_state.game_id,
            llm_call_fn=self._llm_call_fn,
        )

        for player in game_state.alive_players:
            if not player.is_human:
                try:
                    pipeline.run(player, game_state, round_num=round_num, snapshot=snapshot)
                except Exception as e:
                    log.error(f"[Reflection] Failed for {player.name}: {e}")
