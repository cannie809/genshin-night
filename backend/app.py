"""FastAPI backend server for the werewolf game.

This module provides the REST API endpoints for the game client.
"""

import asyncio
import json
import logging
import os
import random
from pathlib import Path
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("werewolf")

from backend.ai import UnifiedGameAgent
from backend.ai.output_sanitizer import parse_json_response
from backend.ai.personality import CHARACTER_PRESETS
from backend.config import DEFAULT_MODE
from backend.game import GameEngine
from backend.game.victory import check_victory, get_victory_message
from backend.models import GameEvent, GamePhase, GameState, RoundSnapshot, Player
from backend.prefetch import PrefetchManager

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env.local")

# Initialize FastAPI app
app = FastAPI(title="Werewolf Game API", version="1.0.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global game state storage (in-memory for MVP)
# In production, use Redis or database
games: dict[str, GameState] = {}

# Initialize game engine and AI agent
MEMORY_BASE = Path(__file__).parent / ".memory"
game_engine = GameEngine(MEMORY_BASE)

# Get LLM provider from env
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter")
ai_agent = UnifiedGameAgent(MEMORY_BASE, llm_provider=LLM_PROVIDER)

# Wire engine's reflection pipeline to agent's LLM
game_engine.set_llm_call_fn(ai_agent.get_reflection_llm_fn())

# Initialize prefetch manager for LLM call pre-computation
prefetch = PrefetchManager()

# Deferred end_round snapshots: when hunter is vote-eliminated, end_round must
# wait until after the hunter shoots so that event_index and day_record include
# the hunter_shot event.  Keyed by game_id.
_pending_end_round: dict[str, tuple[int, RoundSnapshot]] = {}


# === Request/Response Models ===


class GameConfig(BaseModel):
    """Game configuration for starting a new game."""

    mode: str = DEFAULT_MODE
    preferred_role: str | None = None  # Role player wants, or None for random


class NightActionRequest(BaseModel):
    """Request for night action."""

    game_id: str
    player_id: str
    action_type: str  # "werewolf_kill", "seer_check", "witch_action", "hunter_shoot", "guard_action"
    target_id: Optional[str] = None
    use_save: bool = False  # For witch
    use_poison: bool = False  # For witch
    poison_target_id: Optional[str] = None  # For witch


class VoteRequest(BaseModel):
    """Request for voting."""

    game_id: str
    player_id: str
    target_id: str | None = None  # None = abstain


class SpeechRequest(BaseModel):
    """Request for AI speech generation."""

    game_id: str
    player_id: str


class ActionResult(BaseModel):
    """Generic action result."""

    success: bool
    message: str
    data: dict = {}


class PlayerResponse(BaseModel):
    """Player data for API response."""

    id: str
    name: str
    role: str  # Hidden for other players
    alive: bool
    is_human: bool
    avatar_url: str | None = None
    seer_result: str | None = None  # "WEREWOLF" or "GOOD" if checked by seer


class GameStateResponse(BaseModel):
    """Game state response."""

    game_id: str
    mode: str
    phase: str
    round_number: int
    day_number: int
    players: list[PlayerResponse]
    events: list[dict]
    winner: Optional[str]
    ai_speaking: bool = False


# === Utility Functions ===


def _add_wolf_collab_events(game: GameState, collab: dict) -> None:
    """Add werewolf collaboration chat events from a collab result dict."""
    if not collab.get("wolf1_name"):
        return
    # Each wolf's kill suggestion
    for name_key, sug_key, reason_key in [
        ("wolf1_name", "wolf1_suggestion", "wolf1_reasoning"),
        ("wolf2_name", "wolf2_suggestion", "wolf2_reasoning"),
    ]:
        wname = collab.get(name_key)
        wsug = collab.get(sug_key)
        wreason = collab.get(reason_key)
        if wname and wsug:
            msg = f"🐺 {wname}: 我建议击杀{wsug}"
            if wreason:
                msg += f"，{wreason}"
            game.events.append(
                GameEvent(type="werewolf_chat", round=game.round_number, phase="NIGHT_WEREWOLF", message=msg)
            )
    # Cooperation strategy
    wolf1_role = collab.get("wolf1_daytime_role", "")
    wolf2_role = collab.get("wolf2_daytime_role", "")
    strategy = collab.get("strategy_summary", "")
    if wolf1_role or wolf2_role:
        parts = []
        if wolf1_role:
            parts.append(f"{collab.get('wolf1_name')}: {wolf1_role}")
        if wolf2_role:
            parts.append(f"{collab.get('wolf2_name')}: {wolf2_role}")
        game.events.append(
            GameEvent(
                type="werewolf_chat", round=game.round_number, phase="NIGHT_WEREWOLF",
                message=f"🐺 白天分工: {'；'.join(parts)}",
            )
        )
    if strategy:
        game.events.append(
            GameEvent(
                type="werewolf_chat", round=game.round_number, phase="NIGHT_WEREWOLF",
                message=f"🐺 本轮策略: {strategy}",
            )
        )
    # Final kill decision
    target_name = collab.get("target")
    team_reasoning = collab.get("team_reasoning", "")
    if target_name:
        msg = f"🐺 最终决定: 击杀{target_name}"
        if team_reasoning:
            msg += f"。{team_reasoning}"
        game.events.append(
            GameEvent(type="werewolf_chat", round=game.round_number, phase="NIGHT_WEREWOLF", message=msg)
        )


def get_game(game_id: str) -> GameState:
    """Get game by ID or raise 404."""
    game = games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")
    return game


def player_response(
    player: Player,
    is_self: bool = False,
    requesting_role: str = "",
    seer_result: str | None = None,
) -> PlayerResponse:
    """Convert Player to PlayerResponse (hide role for other players).

    Werewolves can see fellow werewolves' roles.
    Seer sees check results for investigated players.
    """
    show_role = is_self
    # Werewolves see each other
    if requesting_role == "werewolf" and player.role == "werewolf":
        show_role = True
    return PlayerResponse(
        id=player.id,
        name=player.name,
        role=player.role if show_role else "???",
        alive=player.alive,
        is_human=player.is_human,
        avatar_url=player.avatar_url,
        seer_result=seer_result,
    )


def filter_events_for_player(events: list, player: Player) -> list[dict]:
    """Filter events based on what this player should see."""
    # Night events only visible to the acting role
    private_event_types = {
        "werewolf_kill": "werewolf",
        "werewolf_chat": "werewolf",
        "seer_check": "seer",
        "witch_save": "witch",
        "witch_poison": "witch",
        "guard_protect": "guard",
    }
    # Hunter identity: hide when hunter held fire; show when hunter shot
    hunter_held_fire = any(e.type == "hunter_hold_fire" for e in events)
    _hunter_identity_types = {"hunter_death_night", "hunter_death_vote"}
    filtered = []
    for e in events:
        if e.type in private_event_types:
            if player.role != private_event_types[e.type]:
                continue
        # After hold fire: hide events that reveal hunter identity
        if hunter_held_fire and e.type in _hunter_identity_types:
            continue
        # Always hide hunter_hold_fire from game log
        if e.type == "hunter_hold_fire":
            continue
        # Localize seer_check message with check result
        message = e.message
        if e.type == "seer_check" and e.data:
            result = e.data.get("result", "")
            result_label = "狼人" if result == "WEREWOLF" else "好人"
            target_name = e.data.get("target_id", "")
            # Resolve name from data or parse from message
            if "预言家查验了 " in e.message:
                target_name = e.message.split("预言家查验了 ")[-1]
            elif "Seer checked " in e.message:
                target_name = e.message.split("Seer checked ")[-1]
            message = f"查验结果: {target_name} 是 {result_label}"
        entry = {
            "type": e.type,
            "round": e.round,
            "phase": e.phase,
            "message": message,
        }
        # Include data for vote results (public info), speech stickers, and hunter events
        if e.data and e.type in ("vote_result", "speech", "hunter_death_vote", "hunter_death_night"):
            entry["data"] = e.data
        filtered.append(entry)
    return filtered


def game_state_response(game: GameState, requesting_player_id: str = "player_0") -> GameStateResponse:
    """Convert GameState to GameStateResponse."""
    requesting_player = game.get_player_by_id(requesting_player_id)
    requesting_role = requesting_player.role if requesting_player else ""
    game_over = game.winner is not None

    # Seer check results (only visible to the seer)
    seer_checks = game.seer_checks if requesting_role == "seer" else {}

    # Publicly revealed roles (e.g., hunter who actually shot)
    revealed_ids: set[str] = set()
    if not game_over:
        hunter_shoot_evt = next((e for e in game.events if e.type == "hunter_shoot"), None)
        if hunter_shoot_evt and hunter_shoot_evt.data:
            hid = hunter_shoot_evt.data.get("hunter_id")
            if hid:
                revealed_ids.add(hid)

    return GameStateResponse(
        game_id=game.game_id,
        mode=game.mode,
        phase=game.phase.value,
        round_number=game.round_number,
        day_number=game.day_number,
        players=[
            # Reveal all roles when game is over
            PlayerResponse(id=p.id, name=p.name, role=p.role, alive=p.alive, is_human=p.is_human, avatar_url=p.avatar_url)
            if game_over
            # Publicly revealed identity (hunter who shot)
            else PlayerResponse(id=p.id, name=p.name, role=p.role, alive=p.alive, is_human=p.is_human, avatar_url=p.avatar_url)
            if p.id in revealed_ids
            else player_response(
                p,
                is_self=(p.id == requesting_player_id),
                requesting_role=requesting_role,
                seer_result=seer_checks.get(p.id),
            )
            for p in game.players
        ],
        events=filter_events_for_player(game.events, requesting_player) if requesting_player else [],
        winner=game.winner,
        ai_speaking=game.ai_speaking,
    )


# === API Endpoints ===


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "Werewolf Game API"}


@app.get("/api/characters")
async def list_characters():
    """Return the character pool for the landing page carousel."""
    return [
        {"id": c.id, "name": c.name, "avatar_url": c.avatar_url}
        for c in CHARACTER_PRESETS
    ]


@app.post("/api/game/start", response_model=GameStateResponse)
async def start_game(config: GameConfig):
    """Start a new game.

    Args:
        config: Game configuration

    Returns:
        Initial game state
    """
    try:
        # Invalidate any prefetch from previous game
        for gid in list(games.keys()):
            prefetch.invalidate(gid)

        # Create new game
        game = game_engine.create_game(mode=config.mode, preferred_role=config.preferred_role)

        # Store in memory
        games[game.game_id] = game

        human = game.get_player_by_id("player_0")
        roles_summary = ", ".join(f"{p.name}={p.role}" for p in game.players)
        log.info(f"=== NEW GAME === mode={config.mode} preferred_role={config.preferred_role}")
        log.info(f"  Human role: {human.role if human else '?'}")
        log.info(f"  All roles: {roles_summary}")

        return game_state_response(game)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create game: {str(e)}")


@app.get("/api/game/state/{game_id}", response_model=GameStateResponse)
async def get_game_state(game_id: str, player_id: str = "player_0"):
    """Get current game state.

    Args:
        game_id: Game ID
        player_id: Requesting player ID (for role visibility)

    Returns:
        Current game state
    """
    game = get_game(game_id)
    return game_state_response(game, requesting_player_id=player_id)


@app.post("/api/game/night-action", response_model=ActionResult)
async def night_action(req: NightActionRequest):
    """Process night action.

    Args:
        req: Night action request

    Returns:
        Action result
    """
    game = get_game(req.game_id)
    player = game.get_player_by_id(req.player_id)

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    log.info(f"[Night] action={req.action_type} player={player.name}({player.role}) target={req.target_id}")

    try:
        if req.action_type == "werewolf_kill":
            # Werewolf kill - use human's target if provided, otherwise AI
            if player.is_human and req.target_id:
                # Human werewolf: resolve target by majority vote with AI partner
                human_target = game.get_player_by_id(req.target_id)
                ai_suggestion_name = game.wolf_ai_suggestion
                if ai_suggestion_name and human_target:
                    ai_target = game.get_player_by_name(ai_suggestion_name)
                    if ai_target and ai_target.id != human_target.id:
                        # Disagreement: random choice, but show both picks
                        target = random.choice([human_target, ai_target])
                        # Find AI wolf name
                        ai_wolf = next(
                            (w for w in game.alive_werewolves if not w.is_human), None
                        )
                        ai_name = ai_wolf.name if ai_wolf else "队友"
                        game.events.append(
                            GameEvent(
                                type="werewolf_chat",
                                round=game.round_number,
                                phase="NIGHT_WEREWOLF",
                                message=(
                                    f"🐺 你选择了{human_target.name}，"
                                    f"{ai_name}选择了{ai_target.name}，"
                                    f"最终随机决定击杀{target.name}"
                                ),
                            )
                        )
                    else:
                        # Agreement
                        target = human_target
                        game.events.append(
                            GameEvent(
                                type="werewolf_chat",
                                round=game.round_number,
                                phase="NIGHT_WEREWOLF",
                                message=f"🐺 意见一致，击杀{target.name}",
                            )
                        )
                else:
                    # Solo wolf or no AI suggestion
                    target = human_target
            else:
                collab = ai_agent.werewolf_collaborate(game)
                target_name = collab.get("target")
                target = game.get_player_by_name(target_name) if target_name else None
                _add_wolf_collab_events(game, collab)
                game.wolf_collab_result = collab

            if not target:
                raise HTTPException(status_code=400, detail="No valid target")

            log.info(f"  Werewolf kills: {target.name}")
            result = game_engine.process_night_werewolf(game, target.id)
            game_engine.next_phase(game)
            log.info(f"  -> next phase: {game.phase.value}")

            return ActionResult(
                success=True,
                message=f"狼人决定击杀 {target.name}",
                data=result,
            )

        elif req.action_type == "seer_check":
            # Seer investigation - use human's target if provided, otherwise AI
            if player.is_human and req.target_id:
                target = game.get_player_by_id(req.target_id)
            else:
                target_name = ai_agent.seer_investigate(player, game)
                target = game.get_player_by_name(target_name) if target_name else None

            if not target:
                raise HTTPException(status_code=400, detail="No valid target")

            result = game_engine.process_night_seer(game, target.id)
            log.info(f"  Seer checked {target.name}: {'WEREWOLF' if target.role == 'werewolf' else 'GOOD'}")
            game_engine.next_phase(game)
            log.info(f"  -> next phase: {game.phase.value}")

            result_label = "狼人" if result["result"] == "WEREWOLF" else "好人"
            return ActionResult(
                success=True,
                message=f"查验结果: {target.name} 是 {result_label}",
                data=result,
            )

        elif req.action_type == "witch_action":
            # Witch action
            killed_player = None
            if game.night_kills:
                killed_player = game.get_player_by_id(game.night_kills[0])

            if player.is_human:
                # Human witch: use request fields directly
                result = game_engine.process_night_witch(
                    game,
                    use_save=req.use_save,
                    use_poison=req.use_poison,
                    poison_target_id=req.poison_target_id,
                )
                # Build response message based on actual result (not request)
                if result.get("saved") and killed_player:
                    msg = f"你使用解药救了 {killed_player.name}"
                elif result.get("poisoned") and req.poison_target_id:
                    poison_target = game.get_player_by_id(req.poison_target_id)
                    msg = f"你对 {poison_target.name} 使用了毒药" if poison_target else "毒药已使用"
                elif req.use_save and not result.get("saved"):
                    msg = "解药已经用过了，无法再次使用"
                elif req.use_poison and not result.get("poisoned"):
                    msg = "毒药已经用过了，无法再次使用"
                else:
                    msg = "你选择不使用药水"
            else:
                # AI witch: delegate to AI agent
                action = ai_agent.witch_action(player, killed_player, game)
                use_save = action.get("action") == "save"
                use_poison = action.get("action") == "poison"
                poison_target_id = None
                if use_poison:
                    poison_target = game.get_player_by_name(action.get("target"))
                    if poison_target:
                        poison_target_id = poison_target.id
                result = game_engine.process_night_witch(
                    game,
                    use_save=use_save,
                    use_poison=use_poison,
                    poison_target_id=poison_target_id,
                )
                msg = "Witch action processed"

            # Move to next phase
            game_engine.next_phase(game)

            return ActionResult(
                success=True,
                message=msg,
                data=result,
            )

        elif req.action_type == "guard_action":
            # Guard protection (target_id=None means skip / 空守)
            if player.is_human:
                if req.target_id:
                    target = game.get_player_by_id(req.target_id)
                    # Validate consecutive guard restriction
                    if target and target.id == game.last_guarded_player:
                        raise HTTPException(status_code=400, detail="不能连续两晚守护同一人")
                else:
                    target = None  # Human guard chose to skip
            else:
                # AI guard: delegate to AI agent (may return None = skip)
                target_name = ai_agent.guard_protect(player, game)
                target = game.get_player_by_name(target_name) if target_name else None

            result = game_engine.process_night_guard(game, target.id if target else None)
            log.info(f"  Guard protects: {target.name if target else 'nobody (skip)'}")
            game_engine.next_phase(game)
            log.info(f"  -> next phase: {game.phase.value}")

            msg = "守卫选择不守护任何人" if result.get("skipped") else f"守卫守护了 {target.name}"
            return ActionResult(
                success=True,
                message=msg,
                data=result,
            )

        elif req.action_type == "morning":
            # Process morning deaths
            dead_ids = game_engine.process_morning(game)
            dead_names = [game.get_player_by_id(did).name for did in dead_ids]

            # Move to day discussion
            game.phase = GamePhase.DAY_DISCUSSION
            game.day_number += 1

            return ActionResult(
                success=True,
                message=f"Morning processed. Deaths: {dead_names if dead_names else 'None (平安夜)'}",
                data={"deaths": dead_names},
            )

        else:
            raise HTTPException(status_code=400, detail=f"Unknown action type: {req.action_type}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Action failed: {str(e)}")


@app.get("/api/game/witch-info/{game_id}")
async def witch_info(game_id: str, player_id: str = "player_0"):
    """Get witch night info: who was killed and potion availability."""
    game = get_game(game_id)
    player = game.get_player_by_id(player_id)
    if not player or player.role != "witch":
        raise HTTPException(status_code=400, detail="Not a witch")

    # Witch only sees victim when save potion is available
    victim_name = None
    if game.witch_save_available and game.night_kills:
        victim = game.get_player_by_id(game.night_kills[0])
        if victim:
            victim_name = victim.name

    return {
        "victim_name": victim_name,
        "save_available": game.witch_save_available,
        "poison_available": game.witch_poison_available,
    }


@app.get("/api/game/guard-info/{game_id}")
async def guard_info(game_id: str, player_id: str = "player_0"):
    """Get guard night info: last guarded player name."""
    game = get_game(game_id)
    player = game.get_player_by_id(player_id)
    if not player or player.role != "guard":
        raise HTTPException(status_code=400, detail="Not a guard")

    last_guarded_name = None
    if game.last_guarded_player:
        last_guarded = game.get_player_by_id(game.last_guarded_player)
        if last_guarded:
            last_guarded_name = last_guarded.name

    return {
        "last_guarded_name": last_guarded_name,
    }


@app.post("/api/game/wolf-discuss", response_model=GameStateResponse)
async def wolf_discuss(req: SpeechRequest):
    """Generate AI wolf partner's full suggestion for human werewolf.

    Shows kill suggestion with reasoning + daytime cooperation plan.
    """
    game = get_game(req.game_id)
    human = game.get_player_by_id(req.player_id)

    if not human or human.role != "werewolf":
        raise HTTPException(status_code=400, detail="Not a werewolf")

    wolves = [p for p in game.alive_players if p.role == "werewolf"]
    ai_wolf = next((w for w in wolves if not w.is_human), None)

    if ai_wolf:
        targets = [p.name for p in game.alive_players if not p.is_werewolf()]
        if targets:
            # Gap 4: Try prefetched wolf discuss result first
            cached = await prefetch.get_or_wait(
                game.game_id, "wolf_discuss", game.round_number, timeout=0.5
            )
            if cached:
                suggestion, kill_reason, my_plan, teammate_suggestion = cached
            else:
                # Fallback: compute synchronously
                computed = _compute_wolf_discuss_sync(game)
                if computed:
                    suggestion, kill_reason, my_plan, teammate_suggestion = computed
                else:
                    return game_state_response(game, requesting_player_id=req.player_id)

            # Store AI suggestion for later comparison
            game.wolf_ai_suggestion = suggestion

            # Strip character name prefix if LLM echoed it (e.g., "万叶：...")
            import re as _re

            def _strip_name_prefix(text: str, name: str) -> str:
                """Remove character name prefix that LLM may echo."""
                text = _re.sub(rf"^{_re.escape(name)}[：:，,]?\s*", "", text)
                return text.strip()

            kill_reason = _strip_name_prefix(kill_reason, ai_wolf.name)
            my_plan = _strip_name_prefix(my_plan, ai_wolf.name)
            teammate_suggestion = _strip_name_prefix(teammate_suggestion, ai_wolf.name)

            # Event 1: Kill suggestion with reasoning
            game.events.append(
                GameEvent(
                    type="werewolf_chat", round=game.round_number, phase="NIGHT_WEREWOLF",
                    message=f"🐺 {ai_wolf.name}【击杀建议】{suggestion}——{kill_reason}",
                )
            )

            # Event 2: AI's own daytime plan
            if my_plan:
                game.events.append(
                    GameEvent(
                        type="werewolf_chat", round=game.round_number, phase="NIGHT_WEREWOLF",
                        message=f"🐺 {ai_wolf.name}【白天计划】{my_plan}",
                    )
                )

            # Event 3: Suggestion for human teammate
            if teammate_suggestion:
                game.events.append(
                    GameEvent(
                        type="werewolf_chat", round=game.round_number, phase="NIGHT_WEREWOLF",
                        message=f"🐺 {ai_wolf.name}【对你说】{teammate_suggestion}",
                    )
                )

            # Store collab-like result for night record
            game.wolf_collab_result = {
                "wolf1_name": ai_wolf.name,
                "wolf1_suggestion": suggestion,
                "wolf1_reasoning": kill_reason,
                "wolf1_daytime_role": my_plan,
                "wolf2_name": human.name,
                "wolf2_suggestion": "",
                "wolf2_reasoning": "",
                "wolf2_daytime_role": "",
                "target": suggestion,
                "team_reasoning": kill_reason,
                "strategy_summary": teammate_suggestion,
            }

            # Update shared memory
            from backend.memory import KnowledgeManager
            km = KnowledgeManager(MEMORY_BASE, game.game_id)
            strategy_entry = f"\n### 第{game.round_number}轮\n"
            strategy_entry += f"- **{ai_wolf.name}建议击杀**: {suggestion}（{kill_reason}）\n"
            if my_plan:
                strategy_entry += f"- **{ai_wolf.name}计划**: {my_plan}\n"
            if teammate_suggestion:
                strategy_entry += f"- **对{human.name}的建议**: {teammate_suggestion}\n"
            km.update_werewolf_strategy(strategy_entry)

            # Update AI wolf's individual knowledge summary
            if my_plan:
                try:
                    division_content = f"- 第{game.round_number}轮: 建议击杀{suggestion}（{kill_reason}）\n- **我的分工**: {my_plan}"
                    km.update_knowledge_summary(
                        player_id=ai_wolf.id,
                        role="werewolf",
                        operation="append",
                        section="### 团队分工",
                        content=division_content,
                    )
                    km.update_knowledge_summary(
                        player_id=ai_wolf.id,
                        role="werewolf",
                        operation="append",
                        section="### 伪装计划",
                        content=f"- 第{game.round_number}轮: {my_plan}",
                    )
                except Exception as e:
                    log.warning(f"[WolfDiscuss] Failed to update {ai_wolf.name} knowledge: {e}")

    return game_state_response(game, requesting_player_id=req.player_id)


class WolfSpeakRequest(BaseModel):
    """Request for human wolf's night message."""
    game_id: str
    player_id: str
    content: str


@app.post("/api/game/wolf-human-speak", response_model=GameStateResponse)
async def wolf_human_speak(req: WolfSpeakRequest):
    """Record human werewolf's night message to the team.

    Adds the message as a werewolf_chat event and updates shared memory.
    """
    game = get_game(req.game_id)
    human = game.get_player_by_id(req.player_id)

    if not human or human.role != "werewolf":
        raise HTTPException(status_code=400, detail="Not a werewolf")

    content = req.content.strip()
    if not content:
        return game_state_response(game, requesting_player_id=req.player_id)

    # Add human's message as werewolf_chat event
    game.events.append(
        GameEvent(
            type="werewolf_chat",
            round=game.round_number,
            phase="NIGHT_WEREWOLF",
            message=f"🐺 {human.name}: {content}",
        )
    )

    # Update shared memory with human's instructions
    from backend.memory import KnowledgeManager, MemoryStorage
    km = KnowledgeManager(MEMORY_BASE, game.game_id)
    km.update_werewolf_strategy(f"- **{human.name}指示**: {content}\n")

    # AI wolf reconsiders after hearing human's input
    wolves = [p for p in game.alive_players if p.role == "werewolf"]
    ai_wolf = next((w for w in wolves if not w.is_human), None)
    if ai_wolf and game.wolf_ai_suggestion:
        targets = [p.name for p in game.alive_players if not p.is_werewolf()]
        old_suggestion = game.wolf_ai_suggestion

        reconsider = ai_agent.wolf_reconsider(
            ai_wolf=ai_wolf,
            human_name=human.name,
            human_message=content,
            old_suggestion=old_suggestion,
            targets=targets,
            game_state=game,
        )
        new_target = reconsider["final_target"]
        reply = reconsider.get("reply")

        game.wolf_ai_suggestion = new_target

        if reply:
            game.events.append(
                GameEvent(
                    type="werewolf_chat",
                    round=game.round_number,
                    phase="NIGHT_WEREWOLF",
                    message=f"🐺 {ai_wolf.name}: {reply}",
                )
            )
        elif new_target != old_suggestion:
            # Fallback succeeded but no reply — show acknowledgment
            game.events.append(
                GameEvent(
                    type="werewolf_chat",
                    round=game.round_number,
                    phase="NIGHT_WEREWOLF",
                    message=f"🐺 {ai_wolf.name}: 好，听你的。",
                )
            )

        if new_target != old_suggestion:
            log.info(f"[WolfDiscuss] AI changed suggestion: {old_suggestion} → {new_target}")
            km.update_werewolf_strategy(
                f"- **{ai_wolf.name}重新考虑后**: 改为建议击杀{new_target}\n"
            )

    return game_state_response(game, requesting_player_id=req.player_id)


@app.post("/api/game/ai-speak", response_model=ActionResult)
async def ai_speak(req: SpeechRequest):
    """Generate AI player speech.

    Args:
        req: Speech request

    Returns:
        Speech result
    """
    game = get_game(req.game_id)
    player = game.get_player_by_id(req.player_id)

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if player.is_human:
        raise HTTPException(status_code=400, detail="Cannot generate speech for human player")

    try:
        # Generate speech
        speech, stickers = ai_agent.generate_speech(player, game)

        # Add to game state
        game_engine.add_speech(game, player.id, speech)

        return ActionResult(
            success=True,
            message=f"{player.name} spoke",
            data={"player": player.name, "speech": speech, **_build_sticker_data(player, stickers)},
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Speech generation failed: {str(e)}")


class HumanSpeechRequest(BaseModel):
    """Request for human player speech."""
    game_id: str
    player_id: str
    content: str


@app.post("/api/game/human-speak", response_model=ActionResult)
async def human_speak(req: HumanSpeechRequest):
    """Submit human player's speech during discussion."""
    game = get_game(req.game_id)
    player = game.get_player_by_id(req.player_id)

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    game_engine.add_speech(game, req.player_id, req.content)
    return ActionResult(success=True, message="发言已记录", data={})


def _build_sticker_data(player: Player, stickers: list[str]) -> dict:
    """Build event data dict with a randomly chosen sticker URL."""
    if not stickers:
        return {}
    chosen = random.choice(stickers)
    return {
        "sticker": f"/stickers/{player.personality}/{chosen}.png",
        "speaker_name": player.name,
    }


# === Prefetch Helpers ===


def _ensure_speech_order(game: GameState) -> None:
    """Determine random speaking order if not already set."""
    if not game.speech_order:
        alive = game.alive_players
        if alive:
            ids = [p.id for p in alive]
            start = random.randint(0, len(ids) - 1)
            game.speech_order = ids[start:] + ids[:start]
        log.info(f"[SpeechOrder] order={[game.get_player_by_id(pid).name for pid in game.speech_order]}")


def _get_pre_human_ids(game: GameState) -> list[str]:
    """Get AI player IDs that speak before the human in speech_order."""
    pre_human_ids = []
    for pid in game.speech_order:
        player = game.get_player_by_id(pid)
        if not player or not player.alive:
            continue
        if player.is_human:
            break
        pre_human_ids.append(pid)
    return pre_human_ids


def _compute_wolf_discuss_sync(game: GameState) -> tuple[str, str, str, str] | None:
    """Compute wolf discuss LLM result synchronously.

    Returns (suggestion, kill_reason, my_plan, teammate_suggestion) or None.
    """
    wolves = [p for p in game.alive_players if p.role == "werewolf"]
    ai_wolf = next((w for w in wolves if not w.is_human), None)
    human = next((w for w in wolves if w.is_human), None)

    if not ai_wolf or not human:
        return None

    targets = [p.name for p in game.alive_players if not p.is_werewolf()]
    if not targets:
        return None

    from backend.memory import KnowledgeManager, MemoryStorage
    km = KnowledgeManager(MEMORY_BASE, game.game_id)
    shared_mem = km.read_werewolf_shared()
    strategy_ctx = shared_mem.get("strategy", "暂无")[:400]
    threats_ctx = json.dumps(shared_mem.get("threats", {}).get("threats", {}), ensure_ascii=False)

    target_list = "、".join(targets)
    alive_all = "、".join(p.name for p in game.alive_players)

    wolf_storage = MemoryStorage(ai_wolf.id, MEMORY_BASE, game.game_id)
    wolf_profile = wolf_storage.read_profile_personality()
    profile_snippet = wolf_profile[:300] if wolf_profile else ""

    from backend.ai.personality import get_character_by_id
    wolf_preset = get_character_by_id(ai_wolf.personality)
    voice_hint = ""
    if wolf_preset and wolf_preset.voice_anchors:
        anchors = "、".join(f"「{a}」" for a in wolf_preset.voice_anchors)
        voice_hint = (
            f"\n\n## 语气要求\n你必须用{ai_wolf.name}的语气说话。声音标记: {anchors}\n"
            f"所有回复内容都必须自然融入角色人设，不要用分析师口吻。"
        )

    prompt = (
        f"你是{ai_wolf.name}，一名狼人。你的狼队队友是{human.name}（人类玩家）。\n\n"
        f"## 你的角色档案\n{profile_snippet}\n\n"
        f"## 之前的队伍策略\n{strategy_ctx}\n\n"
        f"## 威胁评分\n{threats_ctx}\n\n"
        f"## 当前局势\n- 第{game.round_number}轮夜晚\n- 存活玩家: {alive_all}\n"
        f"- 可击杀目标（非狼人）: {target_list}"
        f"{voice_hint}\n\n"
        f"## 你的任务\n"
        f"作为{human.name}的狼队搭档，你需要用你的角色语气向队友提出完整的建议：\n\n"
        f"1. **击杀建议**: 今晚杀谁？为什么这个人威胁最大？（说明具体理由）\n"
        f"2. **你的白天计划**: 你明天白天打算扮演什么角色？（如：跳预言家、假装好人带节奏、潜水不出头等）\n"
        f"3. **对队友的建议**: 你建议队友{human.name}明天白天怎么配合？\n\n"
        f"请用中文回复，用JSON格式（所有文字必须符合{ai_wolf.name}的人设语气，不要加角色名前缀）：\n"
        f'{{"kill_target": "从{target_list}中选一个",'
        f' "kill_reason": "为什么要杀这个人，用角色语气说一句理由（20字以内，不要重复目标名）",'
        f' "my_plan": "白天打算怎么做（20字以内，如：低调潜水、跳预言家等）",'
        f' "teammate_suggestion": "直接对队友说一句话（20字以内，如：你明天配合投票）"}}'
    )

    suggestion = None
    kill_reason = ""
    my_plan = ""
    teammate_suggestion = ""
    for attempt in range(2):
        ai_agent._save_debug_log(game.game_id, ai_wolf.id, game.round_number,
                                 "wolf_discuss", attempt + 1, "prompt", prompt)
        response = ai_agent._llm_call(prompt, max_tokens=300)
        ai_agent._save_debug_log(game.game_id, ai_wolf.id, game.round_number,
                                 "wolf_discuss", attempt + 1, "response", response)
        log.info(f"[WolfDiscuss] AI response (attempt {attempt+1}): {response[:200]}")
        result = parse_json_response(
            response,
            required_fields=["kill_target"],
            field_validators={"kill_target": targets},
        )
        if result:
            suggestion = result["kill_target"]
            kill_reason = result.get("kill_reason", "威胁较大")
            my_plan = result.get("my_plan", "")
            teammate_suggestion = result.get("teammate_suggestion", "")
            break
        log.warning(f"[WolfDiscuss] Attempt {attempt+1} JSON parse failed")

    # Simplified non-JSON fallback
    if not suggestion:
        simple_prompt = (
            f"你是{ai_wolf.name}，狼人。今晚要杀谁？\n"
            f"可选: {target_list}\n"
            f"只说名字和一句理由。"
        )
        ai_agent._save_debug_log(game.game_id, ai_wolf.id, game.round_number,
                                 "wolf_discuss_simple", 1, "prompt", simple_prompt)
        simple_resp = ai_agent._llm_call(simple_prompt, max_tokens=60)
        ai_agent._save_debug_log(game.game_id, ai_wolf.id, game.round_number,
                                 "wolf_discuss_simple", 1, "response", simple_resp)
        if simple_resp.strip():
            for t in targets:
                if t in simple_resp:
                    suggestion = t
                    idx = simple_resp.index(t) + len(t)
                    reason_part = simple_resp[idx:].strip().strip("，。、：:").strip()
                    kill_reason = reason_part[:30] if reason_part else "局势需要"
                    break

    if not suggestion:
        suggestion = random.choice(targets)
        kill_reason = "局势需要"

    if suggestion not in targets:
        suggestion = random.choice(targets)

    return (suggestion, kill_reason, my_plan, teammate_suggestion)


# === Prefetch Coroutines ===


async def _prefetch_pre_speeches(game: GameState, pre_human_ids: list[str]):
    """Pre-generate speeches for AI players before the human (Gap 1)."""
    # Write night records now (before end_round) so speech prompts include them
    game_engine.write_night_records(game)
    loop = asyncio.get_event_loop()
    results = []
    for pid in pre_human_ids:
        player = game.get_player_by_id(pid)
        if not player or not player.alive:
            continue
        try:
            speech, stickers = await loop.run_in_executor(
                None, ai_agent.generate_speech, player, game
            )
            game_engine.add_speech(game, player.id, speech)
            results.append((pid, speech, stickers))
        except Exception as e:
            log.error(f"[Prefetch] Speech failed for {player.name}: {e}")
    return results


async def _adopt_prefetch_speeches(game: GameState, pre_human_ids: list[str]):
    """Wait on the existing prefetch task instead of launching a competing generator."""
    try:
        cached = await prefetch.get_or_wait(
            game.game_id, "pre_speeches", game.round_number, timeout=60.0
        )
        if cached:
            # Prefetch succeeded — speeches already in game state, just add events
            for pid, speech, stickers in cached:
                player = game.get_player_by_id(pid)
                if not player:
                    continue
                game.events.append(
                    GameEvent(
                        type="speech",
                        round=game.round_number,
                        phase="DAY_DISCUSSION",
                        message=f"【{player.name}】: {speech}",
                        data=_build_sticker_data(player, stickers),
                    )
                )
        else:
            # Prefetch truly failed (exception / no cache entry) — regenerate
            log.info("[AdoptPrefetch] Prefetch unavailable, falling back to fresh generation")
            await _generate_speeches_background(game, pre_human_ids)
            return  # _generate_speeches_background handles ai_speaking
    finally:
        game.ai_speaking = False


async def _prefetch_pre_speeches_with_votes(game: GameState, speaker_ids: list[str]):
    """Pre-generate all speeches then chain vote prefetch (dead player flow)."""
    results = await _prefetch_pre_speeches(game, speaker_ids)
    # Chain: launch vote prefetch now that all speeches are in game state
    await prefetch.launch(
        game.game_id, "ai_votes", game.round_number,
        _prefetch_votes(game),
    )
    return results


async def _prefetch_night_actions(game: GameState, human: Player):
    """Pre-compute all AI night actions, modifying game state directly (Gap 2)."""
    loop = asyncio.get_event_loop()
    phase_role_map = {
        GamePhase.NIGHT_GUARD: "guard",
        GamePhase.NIGHT_WEREWOLF: "werewolf",
        GamePhase.NIGHT_SEER: "seer",
        GamePhase.NIGHT_WITCH: "witch",
    }

    for _ in range(10):
        phase = game.phase
        if phase not in phase_role_map:
            break
        acting_role = phase_role_map[phase]
        if human.role == acting_role and human.alive:
            # Gap 4: chain wolf discuss prefetch for human werewolf
            if acting_role == "werewolf":
                await prefetch.launch(
                    game.game_id, "wolf_discuss", game.round_number,
                    _prefetch_wolf_discuss(game),
                )
            break

        actor = next(
            (p for p in game.alive_players if p.role == acting_role and not p.is_human),
            None,
        )
        if not actor:
            game_engine.next_phase(game)
            continue

        if phase == GamePhase.NIGHT_GUARD:
            target_name = await loop.run_in_executor(None, ai_agent.guard_protect, actor, game)
            target = game.get_player_by_name(target_name) if target_name else None
            game_engine.process_night_guard(game, target.id if target else None)

        elif phase == GamePhase.NIGHT_WEREWOLF:
            collab = await loop.run_in_executor(None, ai_agent.werewolf_collaborate, game)
            target_name = collab.get("target")
            _add_wolf_collab_events(game, collab)
            game.wolf_collab_result = collab
            if target_name:
                target = game.get_player_by_name(target_name)
                if target:
                    game_engine.process_night_werewolf(game, target.id)

        elif phase == GamePhase.NIGHT_SEER:
            target_name = await loop.run_in_executor(None, ai_agent.seer_investigate, actor, game)
            if target_name:
                target = game.get_player_by_name(target_name)
                if target:
                    game_engine.process_night_seer(game, target.id)

        elif phase == GamePhase.NIGHT_WITCH:
            if not game.witch_save_available and not game.witch_poison_available:
                log.info(f"[Prefetch] Witch has no potions, skipping AI call")
                game_engine.process_night_witch(game)
            else:
                killed_player = None
                if game.night_kills:
                    killed_player = game.get_player_by_id(game.night_kills[0])
                action = await loop.run_in_executor(
                    None, ai_agent.witch_action, actor, killed_player, game
                )
                log.info(f"[Prefetch] AI witch action: {action}")
                use_save = action.get("action") == "save"
                use_poison = action.get("action") == "poison"
                poison_target_id = None
                if use_poison:
                    poison_target = game.get_player_by_name(action.get("target"))
                    if poison_target:
                        poison_target_id = poison_target.id
                game_engine.process_night_witch(
                    game, use_save=use_save, use_poison=use_poison,
                    poison_target_id=poison_target_id,
                )

        game_engine.next_phase(game)

    # Dead player: handle morning + chain speech/vote prefetch
    if not human.alive and game.phase == GamePhase.DAY_DISCUSSION:
        dead_ids = game_engine.process_morning(game)
        dead_names = [game.get_player_by_id(did).name for did in dead_ids if game.get_player_by_id(did)]
        if dead_names:
            game.events.append(
                GameEvent(
                    type="morning_death", round=game.round_number, phase="DAY_DISCUSSION",
                    message=f"昨晚倒牌: {', '.join(dead_names)}",
                )
            )
        else:
            game.events.append(
                GameEvent(
                    type="morning_safe", round=game.round_number, phase="DAY_DISCUSSION",
                    message="昨晚是平安夜，无人倒牌",
                )
            )
        game.day_number += 1

        # Handle hunter death at night (must be AI since human is already dead)
        hunter_dead = next(
            (game.get_player_by_id(did) for did in dead_ids
             if game.get_player_by_id(did) and game.get_player_by_id(did).role == "hunter"),
            None,
        )
        if hunter_dead and not hunter_dead.is_human:
            # NOTE: Do NOT set game.phase = HUNTER_SHOOT here.
            # This runs inside a prefetch coroutine while polling can read game state.
            # Setting HUNTER_SHOOT would let the frontend auto-trigger /hunter-shoot,
            # causing a race (double LLM call, prefetch invalidation → CancelledError → 500).
            target_name = await loop.run_in_executor(None, ai_agent.hunter_shoot, hunter_dead, game)
            target = game.get_player_by_name(target_name) if target_name else None
            game_engine.process_hunter_shoot(game, target.id if target else None)
            winner = check_victory(game)
            if winner:
                # Defer: don't set GAME_END now — let advance-night reveal it
                # so dead player experiences night→morning→game_end naturally.
                game.deferred_winner = winner
                game.deferred_events.append(
                    GameEvent(type="game_end", round=game.round_number, phase="GAME_END",
                              message=get_victory_message(winner))
                )

        # Check victory after night deaths
        if not game.deferred_winner and not game.winner:
            winner = check_victory(game)
            if winner:
                # Defer: same as above
                game.deferred_winner = winner
                game.deferred_events.append(
                    GameEvent(type="game_end", round=game.round_number, phase="GAME_END",
                              message=get_victory_message(winner))
                )

        # Chain speech + vote prefetch (only if game continues)
        if not game.winner and not game.deferred_winner and game.phase == GamePhase.DAY_DISCUSSION:
            _ensure_speech_order(game)
            all_speaker_ids = _get_pre_human_ids(game)
            if all_speaker_ids:
                await prefetch.launch(
                    game.game_id, "pre_speeches", game.round_number,
                    _prefetch_pre_speeches_with_votes(game, all_speaker_ids),
                )
                log.info(f"[Prefetch] Chained speech+vote for dead player, {len(all_speaker_ids)} speakers")

    return True


async def _prefetch_votes(game: GameState):
    """Pre-compute AI vote decisions in parallel (Gap 3).

    All AI players vote simultaneously and independently — no AI can see
    another AI's vote.  Uses asyncio.gather for true parallel execution.
    """
    loop = asyncio.get_event_loop()
    ai_players = [p for p in game.alive_players if not p.is_human]

    async def _single_vote(player):
        try:
            return player.id, await loop.run_in_executor(
                None, ai_agent.vote_decision, player, game
            )
        except Exception as e:
            log.error(f"[Prefetch] Vote failed for {player.name}: {e}")
            return player.id, None

    results = await asyncio.gather(*[_single_vote(p) for p in ai_players])
    return {pid: name for pid, name in results if name is not None}


async def _prefetch_wolf_discuss(game: GameState):
    """Pre-compute wolf discuss LLM result (Gap 4)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _compute_wolf_discuss_sync, game)


async def _background_end_round(game: GameState, round_num: int,
                                snapshot: RoundSnapshot):
    """Run end_round in background (writes memory, doesn't affect game flow).

    Snapshot must be captured at the call site BEFORE next_phase/reset_round_data
    mutate the game object.
    """
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None, lambda: game_engine.end_round(
                game, round_num=round_num,
                snapshot=snapshot,
            ),
        )
    except Exception as e:
        log.error(f"[EndRound] Background end_round failed: {e}")


async def _end_round_then_prefetch_night(
    game: GameState, round_num: int, snapshot: RoundSnapshot,
    human: "Player",
):
    """Chain end_round → night prefetch so reflections/records are ready for night prompts.

    end_round writes day_record, night_record, and runs reflections (LLM calls).
    Night action prompts read these via _load_player_memories, so they must
    be on disk before night prefetch starts.
    """
    await _background_end_round(game, round_num, snapshot)
    return await _prefetch_night_actions(game, human)


async def _generate_speeches_background(
    game: GameState,
    player_ids: list[str],
    transition_phase: GamePhase | None = None,
):
    """Generate AI speeches in background, appending each to game.events as it completes."""
    loop = asyncio.get_event_loop()
    try:
        for pid in player_ids:
            player = game.get_player_by_id(pid)
            if not player or not player.alive:
                continue
            try:
                speech, stickers = await loop.run_in_executor(
                    None, ai_agent.generate_speech, player, game
                )
                game_engine.add_speech(game, player.id, speech)
                game.events.append(
                    GameEvent(
                        type="speech",
                        round=game.round_number,
                        phase="DAY_DISCUSSION",
                        message=f"【{player.name}】: {speech}",
                        data=_build_sticker_data(player, stickers),
                    )
                )
            except Exception as e:
                log.error(f"[BackgroundSpeech] {player.name} failed: {e}")
        if transition_phase:
            game.phase = transition_phase
            # Gap 3: Pre-compute AI votes while user thinks about their vote
            if transition_phase == GamePhase.DAY_VOTE:
                await prefetch.launch(
                    game.game_id, "ai_votes", game.round_number,
                    _prefetch_votes(game),
                )
    finally:
        game.ai_speaking = False


@app.post("/api/game/pre-discussion", response_model=GameStateResponse)
async def pre_discussion(req: SpeechRequest):
    """Phase 1: Determine speaking order, launch background task for pre-human speeches.

    Returns immediately with ai_speaking=true while speeches generate in background.
    """
    game = get_game(req.game_id)

    if game.phase != GamePhase.DAY_DISCUSSION:
        raise HTTPException(status_code=400, detail="Not in discussion phase")

    # If already generating, just return current state
    if game.ai_speaking:
        return game_state_response(game, requesting_player_id=req.player_id)

    # Determine speaking order (may have been set by prefetch trigger)
    _ensure_speech_order(game)

    # Find human's position in the order
    human_pos = -1
    for i, pid in enumerate(game.speech_order):
        p = game.get_player_by_id(pid)
        if p and p.is_human:
            human_pos = i
            break
    total = len(game.speech_order)
    log.info(f"[PreDiscussion] human at position {human_pos + 1}/{total}")

    _existing_speech_names = {
        ev.message.split("】")[0][1:]
        for ev in game.events
        if ev.type == "speech" and ev.round == game.round_number
    }
    pre_human_ids = [
        pid for pid in _get_pre_human_ids(game)
        if game.get_player_by_id(pid)
        and game.get_player_by_id(pid).name not in _existing_speech_names
    ]

    if pre_human_ids:
        # Gap 1: Try to use prefetched speeches
        # Dead player: wait longer since all speeches are pre-human
        _human = game.get_player_by_id(req.player_id)
        _speech_timeout = 30.0 if (_human and not _human.alive) else 0.5
        cached = await prefetch.get_or_wait(
            game.game_id, "pre_speeches", game.round_number, timeout=_speech_timeout
        )
        if cached:
            # Speeches already generated — add events instantly
            for pid, speech, stickers in cached:
                player = game.get_player_by_id(pid)
                if not player:
                    continue
                # add_speech may already be done by prefetch, check first
                if not any(s["player"] == player.name for s in game.speeches):
                    game_engine.add_speech(game, player.id, speech)
                # Skip if speech event already exists for this player+round
                if player.name not in _existing_speech_names:
                    game.events.append(
                        GameEvent(
                            type="speech",
                            round=game.round_number,
                            phase="DAY_DISCUSSION",
                            message=f"【{player.name}】: {speech}",
                            data=_build_sticker_data(player, stickers),
                        )
                    )
            # ai_speaking stays False — speeches are ready
        else:
            # Prefetch still running — adopt it instead of launching a competitor
            game.ai_speaking = True
            asyncio.create_task(_adopt_prefetch_speeches(game, pre_human_ids))

    return game_state_response(game, requesting_player_id=req.player_id)


@app.post("/api/game/finish-discussion", response_model=GameStateResponse)
async def finish_discussion(req: SpeechRequest):
    """Phase 2: After human speaks, launch background task for remaining speeches, then transition to voting."""
    game = get_game(req.game_id)

    if game.phase != GamePhase.DAY_DISCUSSION:
        raise HTTPException(status_code=400, detail="Not in discussion phase")

    # If already generating, just return current state
    if game.ai_speaking:
        return game_state_response(game, requesting_player_id=req.player_id)

    # Add human's speech to events and collect post-human AI player IDs
    human_found = False
    post_human_ids = []
    for pid in game.speech_order:
        player = game.get_player_by_id(pid)
        if not player or not player.alive:
            continue
        if player.is_human:
            existing = [s for s in game.speeches if s["player"] == player.name]
            if existing:
                game.events.append(
                    GameEvent(
                        type="speech",
                        round=game.round_number,
                        phase="DAY_DISCUSSION",
                        message=f"【{player.name}】: {existing[-1]['content']}",
                    )
                )
            human_found = True
            continue
        if not human_found:
            continue  # Already spoke in pre-discussion
        post_human_ids.append(pid)

    if post_human_ids:
        game.ai_speaking = True
        asyncio.create_task(
            _generate_speeches_background(game, post_human_ids, transition_phase=GamePhase.DAY_VOTE)
        )
    else:
        game.phase = GamePhase.DAY_VOTE
        # Dead player: launch vote prefetch since no background task handles it
        _req_player = game.get_player_by_id(req.player_id)
        if _req_player and not _req_player.alive:
            await prefetch.launch(
                game.game_id, "ai_votes", game.round_number,
                _prefetch_votes(game),
            )

    return game_state_response(game, requesting_player_id=req.player_id)


@app.post("/api/game/run-discussion", response_model=GameStateResponse)
async def run_discussion(req: SpeechRequest):
    """Legacy: Run full discussion in one shot (kept for compatibility)."""
    game = get_game(req.game_id)

    if game.phase != GamePhase.DAY_DISCUSSION:
        raise HTTPException(status_code=400, detail="Not in discussion phase")

    try:
        # Determine order if not set
        if not game.speech_order:
            alive = game.alive_players
            if alive:
                ids = [p.id for p in alive]
                start = random.randint(0, len(ids) - 1)
                game.speech_order = ids[start:] + ids[:start]

        for pid in game.speech_order:
            player = game.get_player_by_id(pid)
            if not player or not player.alive:
                continue
            if player.is_human:
                existing = [s for s in game.speeches if s["player"] == player.name]
                if existing:
                    game.events.append(
                        GameEvent(
                            type="speech",
                            round=game.round_number,
                            phase="DAY_DISCUSSION",
                            message=f"【{player.name}】: {existing[-1]['content']}",
                        )
                    )
                continue
            speech, stickers = ai_agent.generate_speech(player, game)
            game_engine.add_speech(game, player.id, speech)
            game.events.append(
                GameEvent(
                    type="speech",
                    round=game.round_number,
                    phase="DAY_DISCUSSION",
                    message=f"【{player.name}】: {speech}",
                    data=_build_sticker_data(player, stickers),
                )
            )

        game.phase = GamePhase.DAY_VOTE
        return game_state_response(game, requesting_player_id=req.player_id)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discussion failed: {str(e)}")


@app.post("/api/game/vote", response_model=ActionResult)
async def vote(req: VoteRequest):
    """Process vote.

    Args:
        req: Vote request

    Returns:
        Vote result
    """
    game = get_game(req.game_id)
    player = game.get_player_by_id(req.player_id)

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        # Add human vote (None target_id → "ABSTAIN")
        vote_target = req.target_id if req.target_id else "ABSTAIN"
        game_engine.add_vote(game, req.player_id, vote_target)

        # Gap 3: Use prefetched AI votes if available
        cached_votes = await prefetch.get_or_wait(
            game.game_id, "ai_votes", game.round_number, timeout=15.0
        )

        # Auto-trigger AI votes (use cached or compute fresh in parallel)
        uncached_players = [
            p for p in game.alive_players
            if not p.is_human and p.id not in game.votes
            and not (cached_votes and p.id in cached_votes)
        ]

        # Apply cached votes immediately
        if cached_votes:
            for ai_player in game.alive_players:
                if not ai_player.is_human and ai_player.id not in game.votes and ai_player.id in cached_votes:
                    target_name = cached_votes[ai_player.id]
                    if target_name == "ABSTAIN":
                        game_engine.add_vote(game, ai_player.id, "ABSTAIN")
                    else:
                        target = game.get_player_by_name(target_name)
                        if target:
                            game_engine.add_vote(game, ai_player.id, target.id)

        # Compute uncached votes in parallel
        if uncached_players:
            loop = asyncio.get_event_loop()
            tasks = [
                loop.run_in_executor(None, ai_agent.vote_decision, p, game)
                for p in uncached_players
            ]
            fresh_votes = await asyncio.gather(*tasks, return_exceptions=True)
            for ai_player, target_name in zip(uncached_players, fresh_votes):
                if isinstance(target_name, Exception):
                    log.error(f"[Vote] Fallback vote failed for {ai_player.name}: {target_name}")
                    continue
                if target_name == "ABSTAIN":
                    game_engine.add_vote(game, ai_player.id, "ABSTAIN")
                else:
                    target = game.get_player_by_name(target_name)
                    if target:
                        game_engine.add_vote(game, ai_player.id, target.id)

        # Process voting results
        result = game_engine.process_vote(game)

        # Snapshot ALL transient round data BEFORE next_phase/reset_round_data clears state
        _snap_rn = game.round_number
        _snap = game.snapshot_round_data()

        # Check if hunter needs to shoot
        if game.phase != GamePhase.HUNTER_SHOOT:
            # Advance through CHECK_VICTORY to next actionable phase
            game_engine.next_phase(game)  # DAY_VOTE → CHECK_VICTORY
            if game.phase == GamePhase.CHECK_VICTORY:
                game_engine.next_phase(game)  # CHECK_VICTORY → NIGHT_WEREWOLF or GAME_END

        # Defer end_round when hunter needs to shoot — otherwise hunter_shot
        # won't appear in day_record or reflections (event_index not yet written).
        if game.phase == GamePhase.HUNTER_SHOOT:
            _pending_end_round[game.game_id] = (_snap_rn, _snap)
            log.info(f"[Vote] Hunter shoot pending — deferring end_round for round {_snap_rn}")
        else:
            # Gap 2: Pre-compute night actions while user views vote results
            # Chain: end_round (memory writes + reflections) → night prefetch
            # so that day_record, night_record, and reflection strategy shifts
            # are on disk before night action prompts read them.
            night_phases = {GamePhase.NIGHT_GUARD, GamePhase.NIGHT_WEREWOLF, GamePhase.NIGHT_SEER, GamePhase.NIGHT_WITCH}
            if game.phase in night_phases and not game.winner:
                human = game.get_player_by_id(req.player_id)
                if human:
                    await prefetch.launch(
                        game.game_id, "night_actions", game.round_number,
                        _end_round_then_prefetch_night(
                            game, _snap_rn, _snap, human,
                        ),
                    )
                else:
                    asyncio.create_task(_background_end_round(game, _snap_rn, _snap))
            else:
                asyncio.create_task(_background_end_round(game, _snap_rn, _snap))

        return ActionResult(
            success=True,
            message="Vote processed",
            data=result,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vote failed: {str(e)}")


@app.post("/api/game/ai-vote", response_model=ActionResult)
async def ai_vote(req: SpeechRequest):
    """Generate AI player votes for all AI players.

    Args:
        req: Contains game_id

    Returns:
        Voting result
    """
    game = get_game(req.game_id)

    try:
        # Gap 3: Use prefetched AI votes if available
        cached_votes = await prefetch.get_or_wait(
            game.game_id, "ai_votes", game.round_number, timeout=15.0
        )

        # Apply cached votes + compute uncached in parallel (same as /vote)
        uncached_players = [
            p for p in game.alive_players
            if not p.is_human and p.id not in game.votes
            and not (cached_votes and p.id in cached_votes)
        ]
        if cached_votes:
            for player in game.alive_players:
                if not player.is_human and player.id not in game.votes and player.id in cached_votes:
                    target_name = cached_votes[player.id]
                    if target_name == "ABSTAIN":
                        game_engine.add_vote(game, player.id, "ABSTAIN")
                    else:
                        target = game.get_player_by_name(target_name)
                        if target:
                            game_engine.add_vote(game, player.id, target.id)
        if uncached_players:
            loop = asyncio.get_event_loop()
            tasks = [
                loop.run_in_executor(None, ai_agent.vote_decision, p, game)
                for p in uncached_players
            ]
            fresh_votes = await asyncio.gather(*tasks, return_exceptions=True)
            for player, target_name in zip(uncached_players, fresh_votes):
                if isinstance(target_name, Exception):
                    log.error(f"[AIVote] Fallback vote failed for {player.name}: {target_name}")
                    continue
                if target_name == "ABSTAIN":
                    game_engine.add_vote(game, player.id, "ABSTAIN")
                else:
                    target = game.get_player_by_name(target_name)
                    if target:
                        game_engine.add_vote(game, player.id, target.id)

        # Process voting results
        result = game_engine.process_vote(game)

        # Snapshot ALL transient round data BEFORE next_phase/reset_round_data clears state
        _snap_rn = game.round_number
        _snap = game.snapshot_round_data()

        # Check if hunter needs to shoot
        if game.phase != GamePhase.HUNTER_SHOOT:
            # Advance through CHECK_VICTORY to next actionable phase
            game_engine.next_phase(game)  # DAY_VOTE → CHECK_VICTORY
            if game.phase == GamePhase.CHECK_VICTORY:
                game_engine.next_phase(game)  # CHECK_VICTORY → NIGHT_WEREWOLF or GAME_END

        # Defer end_round when hunter needs to shoot — otherwise hunter_shot
        # won't appear in day_record or reflections (event_index not yet written).
        if game.phase == GamePhase.HUNTER_SHOOT:
            _pending_end_round[game.game_id] = (_snap_rn, _snap)
            log.info(f"[AIVote] Hunter shoot pending — deferring end_round for round {_snap_rn}")
        else:
            # Gap 2: Pre-compute night actions while user views vote results
            # Chain: end_round (memory writes + reflections) → night prefetch
            night_phases = {GamePhase.NIGHT_GUARD, GamePhase.NIGHT_WEREWOLF, GamePhase.NIGHT_SEER, GamePhase.NIGHT_WITCH}
            if game.phase in night_phases and not game.winner:
                human = game.get_player_by_id(req.player_id)
                if human:
                    await prefetch.launch(
                        game.game_id, "night_actions", game.round_number,
                        _end_round_then_prefetch_night(
                            game, _snap_rn, _snap, human,
                        ),
                    )
                else:
                    asyncio.create_task(_background_end_round(game, _snap_rn, _snap))
            else:
                asyncio.create_task(_background_end_round(game, _snap_rn, _snap))

        return ActionResult(
            success=True,
            message="All AI votes processed",
            data=result,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI voting failed: {str(e)}")


@app.post("/api/game/hunter-shoot", response_model=ActionResult)
async def hunter_shoot(req: NightActionRequest):
    """Process hunter shooting.

    Args:
        req: Contains game_id, hunter player_id, and optional target_id (for human hunter)

    Returns:
        Shoot result
    """
    game = get_game(req.game_id)
    hunter = game.get_player_by_id(req.player_id)

    if not hunter or hunter.role != "hunter":
        raise HTTPException(status_code=400, detail="Not a hunter")

    try:
        # Hunter shooting changes alive status — invalidate all prefetches
        prefetch.invalidate(game.game_id)

        if hunter.is_human:
            # Human hunter: target_id is optional (None = hold fire / 压枪)
            if req.target_id:
                target = game.get_player_by_id(req.target_id)
                log.info(f"[HunterShoot] Human hunter {hunter.name} chose target {target.name if target else 'None'}")
            else:
                target = None
                log.info(f"[HunterShoot] Human hunter {hunter.name} chose not to shoot")
        else:
            # AI decides target (may return None = hold fire)
            target_name = ai_agent.hunter_shoot(hunter, game)
            target = game.get_player_by_name(target_name) if target_name else None
            log.info(f"[HunterShoot] AI hunter {hunter.name} chose target {target_name}")

        result = game_engine.process_hunter_shoot(game, target.id if target else None)
        log.info(f"[HunterShoot] Shot processed: {result}")

        # Determine context: night death or vote death
        night_death = any(
            e.type == "hunter_death_night" for e in game.events
        )

        # Check victory after hunter's shot
        winner = check_victory(game)
        log.info(f"[HunterShoot] check_victory={winner} night_death={night_death} phase_before={game.phase.value}")

        if winner:
            game.phase = GamePhase.GAME_END
            game.winner = winner
            game.events.append(
                GameEvent(
                    type="game_end",
                    round=game.round_number,
                    phase="GAME_END",
                    message=get_victory_message(winner),
                )
            )
        elif night_death:
            # Hunter died at night — morning already processed, continue to day
            game.phase = GamePhase.DAY_DISCUSSION
        else:
            # Hunter died from vote — advance to next round (night)
            game_engine.next_phase(game)  # HUNTER_SHOOT → CHECK_VICTORY
            log.info(f"[HunterShoot] After first next_phase: {game.phase.value}")
            if game.phase == GamePhase.CHECK_VICTORY:
                game_engine.next_phase(game)  # CHECK_VICTORY → NIGHT_WEREWOLF or GAME_END

        log.info(f"[HunterShoot] Final phase={game.phase.value} winner={game.winner}")

        # Resume deferred end_round now that hunter_shot is in event_index.
        # This ensures day_record and reflections include the hunter's action.
        pending = _pending_end_round.pop(game.game_id, None)
        if pending:
            _snap_rn, _snap = pending
            night_phases = {GamePhase.NIGHT_GUARD, GamePhase.NIGHT_WEREWOLF, GamePhase.NIGHT_SEER, GamePhase.NIGHT_WITCH}
            if game.phase in night_phases and not game.winner:
                human_player = game.get_player_by_id(req.player_id)
                if human_player:
                    await prefetch.launch(
                        game.game_id, "night_actions", game.round_number,
                        _end_round_then_prefetch_night(
                            game, _snap_rn, _snap, human_player,
                        ),
                    )
                else:
                    asyncio.create_task(_background_end_round(game, _snap_rn, _snap))
            else:
                asyncio.create_task(_background_end_round(game, _snap_rn, _snap))
            log.info(f"[HunterShoot] Resumed deferred end_round for round {_snap_rn}")

        msg = "猎人选择不开枪" if result.get("held_fire") else f"猎人开枪带走了 {target.name}"
        return ActionResult(
            success=True,
            message=msg,
            data=result,
        )

    except Exception as e:
        log.error(f"[HunterShoot] Exception: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Hunter shoot failed: {str(e)}")


@app.post("/api/game/advance-night", response_model=GameStateResponse)
async def advance_night(req: SpeechRequest):
    """Auto-process AI night phases until human's turn or morning.

    This runs all night sub-phases where the human player doesn't act,
    then stops when it reaches a phase requiring human input or morning.
    """
    game = get_game(req.game_id)
    human = game.get_player_by_id(req.player_id)

    if not human:
        raise HTTPException(status_code=404, detail="Player not found")

    # Map phases to the role that acts in them
    phase_role_map = {
        GamePhase.NIGHT_GUARD: "guard",
        GamePhase.NIGHT_WEREWOLF: "werewolf",
        GamePhase.NIGHT_SEER: "seer",
        GamePhase.NIGHT_WITCH: "witch",
    }

    log.info(f"[AdvanceNight] round={game.round_number} phase={game.phase.value} human_role={human.role}")

    try:
        # Gap 2: Try to use prefetched night actions (launched after vote)
        cached_night = await prefetch.get_or_wait(
            game.game_id, "night_actions", game.round_number, timeout=60.0
        )
        if cached_night is not None:
            log.info("[AdvanceNight] Using prefetched night actions — game state already updated")
        else:
            # Cancel any still-running night prefetch to prevent concurrent state modification
            prefetch.invalidate(game.game_id)

            # Fallback: run night actions synchronously (original logic)
            max_iterations = 10
            for _ in range(max_iterations):
                phase = game.phase

                if phase not in phase_role_map:
                    break

                acting_role = phase_role_map[phase]

                if human.role == acting_role and human.alive:
                    break

                actor = next(
                    (p for p in game.alive_players if p.role == acting_role and not p.is_human),
                    None,
                )
                if not actor:
                    game_engine.next_phase(game)
                    continue

                if phase == GamePhase.NIGHT_GUARD:
                    target_name = ai_agent.guard_protect(actor, game)
                    target = game.get_player_by_name(target_name) if target_name else None
                    game_engine.process_night_guard(game, target.id if target else None)

                elif phase == GamePhase.NIGHT_WEREWOLF:
                    collab = ai_agent.werewolf_collaborate(game)
                    target_name = collab.get("target")
                    _add_wolf_collab_events(game, collab)
                    game.wolf_collab_result = collab
                    if target_name:
                        target = game.get_player_by_name(target_name)
                        if target:
                            game_engine.process_night_werewolf(game, target.id)

                elif phase == GamePhase.NIGHT_SEER:
                    target_name = ai_agent.seer_investigate(actor, game)
                    if target_name:
                        target = game.get_player_by_name(target_name)
                        if target:
                            game_engine.process_night_seer(game, target.id)

                elif phase == GamePhase.NIGHT_WITCH:
                    if not game.witch_save_available and not game.witch_poison_available:
                        log.info(f"[AdvanceNight] Witch has no potions, skipping AI call")
                        game_engine.process_night_witch(game)
                    else:
                        killed_player = None
                        if game.night_kills:
                            killed_player = game.get_player_by_id(game.night_kills[0])
                        action = ai_agent.witch_action(actor, killed_player, game)
                        log.info(f"[AdvanceNight] AI witch action: {action}, save_available={game.witch_save_available}, poison_available={game.witch_poison_available}")
                        use_save = action.get("action") == "save"
                        use_poison = action.get("action") == "poison"
                        poison_target_id = None
                        if use_poison:
                            poison_target = game.get_player_by_name(action.get("target"))
                            if poison_target:
                                poison_target_id = poison_target.id
                        game_engine.process_night_witch(
                            game, use_save=use_save, use_poison=use_poison,
                            poison_target_id=poison_target_id,
                        )

                game_engine.next_phase(game)

        # Gap 4: If stopped at NIGHT_WEREWOLF for human wolf, prefetch wolf discuss
        if game.phase == GamePhase.NIGHT_WEREWOLF and human.role == "werewolf" and human.alive:
            await prefetch.launch(
                game.game_id, "wolf_discuss", game.round_number,
                _prefetch_wolf_discuss(game),
            )

        # If all night phases done and now at DAY_DISCUSSION, process morning
        # Guard: skip if already processed by night_actions prefetch (dead player flow)
        morning_already = any(
            e.type in ("morning_death", "morning_safe") and e.round == game.round_number
            for e in game.events
        )

        # Reveal deferred winner from prefetch (dead player reaching advance-night)
        if morning_already and game.deferred_winner:
            game.phase = GamePhase.GAME_END
            game.winner = game.deferred_winner
            game.events.extend(game.deferred_events)
            game.deferred_winner = None
            game.deferred_events = []
            log.info(f"[AdvanceNight] Revealed deferred winner: {game.winner}")
            return game_state_response(game, human.id)

        if game.phase == GamePhase.DAY_DISCUSSION and not morning_already:
            dead_ids = game_engine.process_morning(game)
            dead_names = [game.get_player_by_id(did).name for did in dead_ids if game.get_player_by_id(did)]
            if dead_names:
                game.events.append(
                    GameEvent(
                        type="morning_death",
                        round=game.round_number,
                        phase="DAY_DISCUSSION",
                        message=f"昨晚倒牌: {', '.join(dead_names)}",
                    )
                )
            else:
                game.events.append(
                    GameEvent(
                        type="morning_safe",
                        round=game.round_number,
                        phase="DAY_DISCUSSION",
                        message="昨晚是平安夜，无人倒牌",
                    )
                )
            game.day_number += 1

            # Check if hunter died at night → trigger HUNTER_SHOOT
            hunter_dead = next(
                (game.get_player_by_id(did) for did in dead_ids
                 if game.get_player_by_id(did) and game.get_player_by_id(did).role == "hunter"),
                None,
            )
            if hunter_dead:
                game.phase = GamePhase.HUNTER_SHOOT
                if not hunter_dead.is_human:
                    # AI hunter: auto-shoot (may return None = hold fire)
                    target_name = ai_agent.hunter_shoot(hunter_dead, game)
                    target = game.get_player_by_name(target_name) if target_name else None
                    game_engine.process_hunter_shoot(game, target.id if target else None)
                    # Check victory after hunter shot
                    winner = check_victory(game)
                    if winner:
                        game.phase = GamePhase.GAME_END
                        game.winner = winner
                        game.events.append(
                            GameEvent(
                                type="game_end",
                                round=game.round_number,
                                phase="GAME_END",
                                message=get_victory_message(winner),
                            )
                        )
                    else:
                        game.phase = GamePhase.DAY_DISCUSSION
                # Human hunter: leave phase as HUNTER_SHOOT, frontend handles it
                return game_state_response(game, human.id)

            # Check victory after night deaths
            winner = check_victory(game)
            if winner:
                game.phase = GamePhase.GAME_END
                game.winner = winner
                game.events.append(
                    GameEvent(
                        type="game_end",
                        round=game.round_number,
                        phase="GAME_END",
                        message=get_victory_message(winner),
                    )
                )
                # Distill human player behavior profile
                try:
                    from backend.memory.player_profiler import PlayerProfiler
                    profiler = PlayerProfiler(MEMORY_BASE)
                    try:
                        human_role = next((p.role for p in game.players if p.is_human), None)
                        result = profiler.distill_game(
                            game.game_id, game,
                            getattr(ai_agent, '_llm_call', None),
                        )
                        if result["count"] > 0:
                            log.info(f"[PlayerProfiler] Distilled {result['count']} observations from game {game.game_id[:8]}...")
                            rl = profiler.reinforce(game.game_id, game.winner, human_role, matched_ids=result["matched_ids"])
                            if rl["reinforced"] or rl["penalized"]:
                                log.info(f"[PlayerProfiler] RL update: +{rl['reinforced']} -{rl['penalized']}")
                            profiler.decay_unreinforced(game.game_id, rl["adjusted_ids"])
                            profiler.prune_stale()
                        else:
                            log.info(f"[PlayerProfiler] No observations from game {game.game_id[:8]}...")
                    finally:
                        profiler.close()
                except Exception as e:
                    log.warning(f"[PlayerProfiler] Distillation failed: {e}")

            # Gap 1: Pre-generate speeches while user views morning results
            if not game.winner:
                _ensure_speech_order(game)
                pre_human_ids = _get_pre_human_ids(game)
                if pre_human_ids:
                    await prefetch.launch(
                        game.game_id, "pre_speeches", game.round_number,
                        _prefetch_pre_speeches(game, pre_human_ids),
                    )

        return game_state_response(game, requesting_player_id=req.player_id)

    except (Exception, asyncio.CancelledError) as e:
        raise HTTPException(status_code=500, detail=f"Advance night failed: {str(e)}")


@app.post("/api/game/end-round", response_model=ActionResult)
async def end_round(req: SpeechRequest):
    """End current round and create memory records.

    Args:
        req: Contains game_id

    Returns:
        Success message
    """
    game = get_game(req.game_id)

    try:
        # Create memory records
        game_engine.end_round(game)

        return ActionResult(
            success=True,
            message=f"Round {game.round_number} ended, memory records created",
            data={},
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"End round failed: {str(e)}")


# === Serve Frontend Static Files ===

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

# Serve sticker images from frontend/public/stickers (available before build too)
STICKERS_DIR = Path(__file__).parent.parent / "frontend" / "public" / "stickers"
if STICKERS_DIR.exists():
    app.mount("/stickers", StaticFiles(directory=STICKERS_DIR), name="stickers")

if FRONTEND_DIST.exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="static")

    # Catch-all: serve index.html for SPA routing
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve frontend SPA - returns index.html for all non-API routes."""
        file_path = FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")


# === Main ===


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )
