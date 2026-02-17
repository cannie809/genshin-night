"""Unified game agent for handling all AI player actions.

This module implements the UnifiedGameAgent which uses a single LLM instance
to control all AI players through memory-based context injection.
"""

import json
import logging
import os
import re
import random
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

log = logging.getLogger(__name__)

from dotenv import load_dotenv
from openai import OpenAI

from backend.memory import KnowledgeManager, MemoryStorage
from backend.memory.event_index import EventIndexManager
from backend.memory.player_profiler import PlayerProfiler
from backend.memory.strategy_tracker import StrategyTracker

from .output_sanitizer import parse_json_response, sanitize_speech
from .personality import get_character_by_id
from .sticker_registry import get_sticker_list
from .prompts import (
    build_base_prompt,
    get_guard_action_prompt,
    get_hunter_shoot_prompt,
    get_seer_check_prompt,
    get_speech_prompt,
    get_vote_prompt,
    get_werewolf_collab_prompt,
    get_witch_action_prompt,
)

if TYPE_CHECKING:
    from backend.models import GameState, Player

# Load environment variables
load_dotenv(Path(__file__).parent.parent.parent / ".env.local")


class UnifiedGameAgent:
    """Unified AI agent that handles all player actions through memory injection.

    This agent uses a single LLM instance to control all AI players. Player isolation
    is achieved by only loading the relevant player's memory files before each action.

    Args:
        memory_base: Path to memory storage directory
        llm_provider: LLM provider ("openrouter", "modelscope", or "openai")
    """

    def __init__(
        self,
        memory_base: Optional[Path] = None,
        llm_provider: str = "openrouter",
    ):
        if memory_base is None:
            memory_base = Path(__file__).parent.parent / ".memory"
        self.memory_base = Path(memory_base)

        # Initialize LLM client
        self.llm_provider = llm_provider
        self.client = self._initialize_llm_client()

        # Set model based on provider
        if llm_provider == "openrouter":
            self.model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
            # Optional: prefer a specific OpenRouter provider (e.g. "Baseten")
            self.openrouter_provider = os.getenv("OPENROUTER_PROVIDER", "")
        elif llm_provider == "modelscope":
            self.model = os.getenv("LLM_MODEL", "Qwen/Qwen3-8B")
        else:  # openai
            self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    def _initialize_llm_client(self) -> OpenAI:
        """Initialize OpenAI-compatible LLM client.

        Returns:
            OpenAI client instance
        """
        if self.llm_provider == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY", "dummy-key")
            return OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
        elif self.llm_provider == "modelscope":
            api_key = os.getenv("MODELSCOPE_API_KEY", "dummy-key")
            return OpenAI(
                base_url="https://api-inference.modelscope.cn/v1",
                api_key=api_key,
            )
        else:  # openai
            api_key = os.getenv("OPENAI_API_KEY", "dummy-key")
            return OpenAI(api_key=api_key)

    def _llm_call(
        self,
        prompt: str,
        max_tokens: int = 150,
        temperature: float = 0.7,
        max_retries: int = 2,
        tier: str = "gameplay",
        system_prompt: str | None = None,
    ) -> str:
        """Make LLM API call with retry on transient errors.

        Args:
            prompt: Prompt text (user message)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            max_retries: Max retry attempts on connection errors
            tier: "gameplay" (speech/vote/night actions) | "reflection" (round-end reflection)
                  Currently same model for both; reserved for future model tier split
            system_prompt: Optional system message for stronger role anchoring

        Returns:
            Generated text
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        # Qwen3 on ModelScope requires enable_thinking=False for non-streaming
        if self.llm_provider == "modelscope":
            kwargs["extra_body"] = {"enable_thinking": False}
        # OpenRouter: optionally pin to a specific provider for lower latency
        elif self.llm_provider == "openrouter" and getattr(self, "openrouter_provider", ""):
            kwargs["extra_body"] = {
                "provider": {
                    "order": [self.openrouter_provider],
                    "allow_fallbacks": True,
                }
            }

        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(**kwargs)
                raw_content = response.choices[0].message.content or ""

                # Strip thinking tags if present (some models wrap response in <think>)
                content = raw_content
                if "</think>" in content:
                    after_think = content.split("</think>")[-1].strip()
                    if after_think:
                        content = after_think
                    else:
                        # Model put everything in <think>; extract from inside
                        import re as _re
                        think_match = _re.search(r"<think>(.*?)</think>", raw_content, _re.DOTALL)
                        if think_match:
                            content = think_match.group(1).strip()
                            log.info(f"[LLM] Extracted content from <think> block ({len(content)} chars)")

                result = content.strip()
                if not result and raw_content:
                    log.warning(f"[LLM] Content became empty after processing. Raw ({len(raw_content)} chars): {raw_content[:200]}")
                elif not result:
                    log.warning(f"[LLM] Empty response from API. Model={self.model}, tokens={max_tokens}")

                return result

            except (BrokenPipeError, ConnectionError, OSError) as e:
                if attempt < max_retries:
                    log.warning(f"LLM connection error (attempt {attempt + 1}/{max_retries + 1}): {e}, retrying...")
                    continue
                log.error(f"LLM call failed after {max_retries + 1} attempts: {e}")
                return ""

            except Exception as e:
                log.error(f"LLM call error: {e}")
                return ""

    def _load_player_memories(self, player: "Player", game_state: "GameState") -> dict[str, Any]:
        """Load all memories for a specific player.

        Args:
            player: Player to load memories for
            game_state: Current game state

        Returns:
            Dict with all memory types
        """
        # Get game-specific managers
        knowledge_manager = KnowledgeManager(self.memory_base, game_state.game_id)
        storage = MemoryStorage(player.id, self.memory_base, game_state.game_id)

        # Read player's own memories
        memories = {
            "profile": storage.read_profile_personality(),
            "knowledge_summary": knowledge_manager.read_knowledge_summary(player.id, player.role),
            "knowledge_json": knowledge_manager.read_knowledge_json(player.id, player.role),
            "day_record": storage.read_day_record_shared(num_rounds=3),
            "night_record": storage.read_night_record(num_nights=3),
        }

        # If werewolf, load shared memory
        if player.role == "werewolf":
            memories["shared"] = knowledge_manager.read_werewolf_shared()

        # Load event index key facts
        event_index = EventIndexManager(self.memory_base, game_state.game_id)
        memories["event_index_facts"] = event_index.get_key_facts(viewer_role=player.role)

        # Load high-value strategy items
        meta_path = self.memory_base / game_state.game_id / player.id / "knowledge" / "role" / player.role / "strategy_meta.json"
        if meta_path.exists():
            tracker = StrategyTracker(meta_path)
            high_items = tracker.get_high_value_items(min_helpful=2)
            if high_items:
                # Read summary to extract the actual content of high-value items
                summary = knowledge_manager.read_knowledge_summary(player.id, player.role)
                insights = []
                for item_id in high_items[:5]:  # Max 5 high-value items
                    # Search for the item in summary by ID tag
                    pattern = rf'\[#{re.escape(item_id)}\].*?(?=\n|$)'
                    match = re.search(pattern, summary)
                    if match:
                        insights.append(match.group(0))
                memories["high_value_insights"] = "\n".join(insights) if insights else ""
            else:
                memories["high_value_insights"] = ""
        else:
            memories["high_value_insights"] = ""

        # Load last reflection strategy shift
        reflection_dir = self.memory_base / game_state.game_id / player.id / "reflections"
        if reflection_dir.exists():
            reflection_files = sorted(reflection_dir.glob("round_*_reflection.md"))
            if reflection_files:
                last_reflection = reflection_files[-1].read_text(encoding="utf-8")
                # Extract strategy shift section
                if "## 策略调整" in last_reflection:
                    shift_start = last_reflection.index("## 策略调整") + len("## 策略调整")
                    shift_end = last_reflection.index("##", shift_start) if "##" in last_reflection[shift_start:] else len(last_reflection)
                    memories["last_reflection_shift"] = last_reflection[shift_start:shift_end].strip()
                else:
                    memories["last_reflection_shift"] = ""
            else:
                memories["last_reflection_shift"] = ""
        else:
            memories["last_reflection_shift"] = ""

        # Load human player behavior profile (PlayerProfiler)
        profiler = PlayerProfiler(self.memory_base)
        try:
            phase = {1: "early", 2: "mid"}.get(game_state.round_number, "late")
            observations = profiler.query_observations(
                ai_role=player.role, current_phase=phase, limit=5,
                game_id=game_state.game_id,
            )
            memories["player_behavior_profile"] = profiler.format_for_prompt(observations, player.role)
        finally:
            profiler.close()

        return memories

    def get_reflection_llm_fn(self):
        """Return a callable for reflection pipeline LLM calls.

        Returns:
            Callable with signature (prompt, max_tokens, temperature) -> str
        """
        def reflection_call(prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:
            return self._llm_call(prompt, max_tokens=max_tokens, temperature=temperature, tier="reflection")
        return reflection_call

    # === Debug Logging ===

    def _save_debug_log(
        self,
        game_id: str,
        player_id: str,
        round_num: int,
        action: str,
        attempt: int,
        log_type: str,
        content: str,
        system_prompt: str = "",
    ) -> None:
        """Save prompt/response debug log to .memory/{game_id}/{player_id}/debug/.

        Args:
            game_id: Current game ID
            player_id: Player ID (folder owner)
            round_num: Current round number
            action: Action type (speech, vote, wolf_collab, seer, witch, hunter)
            attempt: Attempt number (1-indexed)
            log_type: "prompt" or "response"
            content: User prompt or raw LLM response
            system_prompt: System prompt (only for log_type="prompt")
        """
        try:
            debug_dir = self.memory_base / game_id / player_id / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{action}_round{round_num}_attempt{attempt}_{log_type}.txt"
            if log_type == "prompt" and system_prompt:
                text = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{content}"
            else:
                text = content
            (debug_dir / filename).write_text(text, encoding="utf-8")
        except Exception as e:
            log.debug(f"[DebugLog] Failed to save {log_type}: {e}")

    # === Helper Methods ===

    def _fill_wolf_collab_fallbacks(
        self,
        result: dict[str, Any],
        wolf1: "Player",
        wolf2: "Player",
        target: str,
    ) -> dict[str, Any]:
        """Fill empty collaboration fields with in-character fallback reasoning.

        When the LLM returns minimal JSON (just final_target), this generates
        personality-aware default reasoning for each wolf based on their preset.

        Args:
            result: Parsed collab result dict
            wolf1: First wolf player
            wolf2: Second wolf player
            target: Final kill target name

        Returns:
            Updated result dict with fallbacks filled
        """
        # Build in-character fallback reasoning for each wolf
        for wolf, reason_key, role_key in [
            (wolf1, "wolf1_reasoning", "wolf1_daytime_role"),
            (wolf2, "wolf2_reasoning", "wolf2_daytime_role"),
        ]:
            preset = get_character_by_id(wolf.personality)
            if not preset:
                continue

            # Fill reasoning if empty
            if not result.get(reason_key):
                # Generate character-appropriate reasoning
                style_map = {
                    "hutao": f"嘿嘿，{target}看起来最碍事呢～先把麻烦解决掉",
                    "xingqiu": f"以{target}目前的言行判断，此人不宜留之",
                    "fischl": f"以本皇女之慧眼观之，{target}乃当务之急",
                    "kazuha": f"风向所指……{target}，是时候了",
                    "zhongli": f"综合考量，{target}对我方威胁最大，当先除之",
                    "bennett": f"那个……我觉得先对付{target}比较好吧！",
                    "chongyun": f"按照当前局势分析，{target}必须优先处理",
                    "venti": f"嗯～{target}的存在让人不太安心呢",
                    "yoimiya": f"那就先解决{target}吧！交给我们！",
                }
                fallback = style_map.get(preset.id, f"综合判断，{target}威胁最大")
                result[reason_key] = fallback

            # Fill daytime role if empty
            if not result.get(role_key):
                role_map = {
                    "hutao": "装作活泼无辜，暗中带节奏",
                    "xingqiu": "以理性分析之名引导投票方向",
                    "fischl": "以高傲姿态掩饰，观察局势",
                    "kazuha": "低调观察，适时附和",
                    "zhongli": "稳重发言，引导舆论方向",
                    "bennett": "装作热心好人，跟随多数意见",
                    "chongyun": "冷静分析，不轻易暴露立场",
                    "venti": "轻松自在地混入讨论，转移注意力",
                    "yoimiya": "积极讨论带气氛，分散怀疑",
                }
                result[role_key] = role_map.get(preset.id, "低调观察，不轻易暴露")

            # Fill suggestion if empty (match final_target)
            sug_key = reason_key.replace("reasoning", "suggestion")
            if not result.get(sug_key):
                result[sug_key] = target

        # Fill team reasoning if empty
        if not result.get("team_reasoning"):
            result["team_reasoning"] = f"优先消除{target}的威胁"

        # Fill strategy summary if empty
        if not result.get("strategy_summary"):
            wolf1_role = result.get("wolf1_daytime_role", "低调")
            wolf2_role = result.get("wolf2_daytime_role", "低调")
            result["strategy_summary"] = f"击杀{target}，{wolf1.name}{wolf1_role[:6]}、{wolf2.name}{wolf2_role[:6]}"

        return result

    # === Core Action Methods ===

    def werewolf_collaborate(self, game_state: "GameState") -> dict[str, Any]:
        """Werewolves collaborate to choose kill target.

        Args:
            game_state: Current game state

        Returns:
            Dict with target and collaboration details:
            - "target": target player name
            - Optional collaboration fields when two wolves discuss
        """
        wolves = [p for p in game_state.alive_players if p.role == "werewolf"]

        if len(wolves) == 0:
            return {"target": None}
        elif len(wolves) == 1:
            # Single werewolf - choose randomly
            targets = [p.name for p in game_state.alive_players if not p.is_werewolf()]
            return {"target": random.choice(targets) if targets else None}

        # Two werewolves - collaborate
        wolf1, wolf2 = wolves[0], wolves[1]

        # Get game-specific knowledge manager
        knowledge_manager = KnowledgeManager(self.memory_base, game_state.game_id)

        wolf1_mem = self._load_player_memories(wolf1, game_state)
        wolf2_mem = self._load_player_memories(wolf2, game_state)
        shared_mem = knowledge_manager.read_werewolf_shared()

        system_prompt, user_prompt = get_werewolf_collab_prompt(
            wolf1, wolf1_mem,
            wolf2, wolf2_mem,
            shared_mem,
            game_state,
        )

        alive_targets = [p.name for p in game_state.alive_players if not p.is_werewolf()]

        # Try up to 2 attempts with JSON validation
        result = None
        for attempt in range(2):
            self._save_debug_log(game_state.game_id, wolf1.id, game_state.round_number,
                                 "wolf_collab", attempt + 1, "prompt", user_prompt, system_prompt)
            response = self._llm_call(
                user_prompt, max_tokens=800, temperature=0.5,
                system_prompt=system_prompt,
            )
            self._save_debug_log(game_state.game_id, wolf1.id, game_state.round_number,
                                 "wolf_collab", attempt + 1, "response", response)
            result = parse_json_response(
                response,
                required_fields=["final_target"],
                field_validators={"final_target": alive_targets},
            )
            if result:
                break
            log.warning(f"[WolfCollab] Attempt {attempt+1} JSON parse failed, raw: {response[:200]}")

        if not result:
            # All attempts failed — fallback
            targets = alive_targets
            return {"target": random.choice(targets) if targets else None}

        target = result["final_target"]

        # Fill in empty fields with in-character fallback reasoning
        result = self._fill_wolf_collab_fallbacks(result, wolf1, wolf2, target)

        # Build cooperation strategy entry for shared memory
        wolf1_role = result.get("wolf1_daytime_role", "")
        wolf2_role = result.get("wolf2_daytime_role", "")
        strategy_summary = result.get("strategy_summary", "")

        strategy_entry = f"\n### 第{game_state.round_number}轮\n"
        strategy_entry += f"- **击杀**: {target}（{result.get('team_reasoning', '')}）\n"
        if wolf1_role:
            strategy_entry += f"- **{wolf1.name}分工**: {wolf1_role}\n"
        if wolf2_role:
            strategy_entry += f"- **{wolf2.name}分工**: {wolf2_role}\n"
        if strategy_summary:
            strategy_entry += f"- **整体策略**: {strategy_summary}\n"

        # Update werewolf shared memory with full cooperation info
        knowledge_manager.update_werewolf_strategy(strategy_entry)

        knowledge_manager.update_werewolf_threats(
            threat_updates={target: 10},
            history_entry={
                "round": game_state.round_number,
                "target": target,
                "reasoning": result.get("team_reasoning", ""),
            }
        )

        # Write individual wolf assignments to their personal knowledge summaries
        for wolf, role_key, reasoning_key in [
            (wolf1, "wolf1_daytime_role", "wolf1_reasoning"),
            (wolf2, "wolf2_daytime_role", "wolf2_reasoning"),
        ]:
            personal_role = result.get(role_key, "")
            personal_reasoning = result.get(reasoning_key, "")
            if personal_role or personal_reasoning:
                try:
                    # Append to "团队分工" section
                    division_content = f"- 第{game_state.round_number}轮: 击杀{target}（{result.get('team_reasoning', '')}）"
                    if personal_role:
                        division_content += f"\n- **我的分工**: {personal_role}"
                    if strategy_summary:
                        division_content += f"\n- **整体策略**: {strategy_summary}"
                    knowledge_manager.update_knowledge_summary(
                        player_id=wolf.id,
                        role="werewolf",
                        operation="append",
                        section="### 团队分工",
                        content=division_content,
                    )
                    # Append to "伪装计划" section
                    if personal_role:
                        knowledge_manager.update_knowledge_summary(
                            player_id=wolf.id,
                            role="werewolf",
                            operation="append",
                            section="### 伪装计划",
                            content=f"- 第{game_state.round_number}轮: {personal_role}",
                        )
                except Exception as e:
                    log.warning(f"[WolfCollab] Failed to update {wolf.name} knowledge: {e}")

        return {
            "target": target,
            "wolf1_name": wolf1.name,
            "wolf1_suggestion": result.get("wolf1_suggestion", target),
            "wolf1_reasoning": result.get("wolf1_reasoning", ""),
            "wolf2_name": wolf2.name,
            "wolf2_suggestion": result.get("wolf2_suggestion", target),
            "wolf2_reasoning": result.get("wolf2_reasoning", ""),
            "team_reasoning": result.get("team_reasoning", ""),
            "wolf1_daytime_role": wolf1_role,
            "wolf2_daytime_role": wolf2_role,
            "strategy_summary": strategy_summary,
        }

    def wolf_reconsider(
        self,
        ai_wolf: "Player",
        human_name: str,
        human_message: str,
        old_suggestion: str,
        targets: list[str],
        game_state: "GameState",
    ) -> dict[str, str | None]:
        """AI wolf reconsiders kill target after hearing human wolf's input.

        Args:
            ai_wolf: AI werewolf player
            human_name: Human wolf's character name
            human_message: Human wolf's night message
            old_suggestion: AI's previous suggestion
            targets: Valid kill target names
            game_state: Current game state

        Returns:
            {"reply": AI's reply (str or None), "final_target": new target name}
            Falls back to parsing human message for target name on LLM failure.
        """
        wolf_storage = MemoryStorage(ai_wolf.id, self.memory_base, game_state.game_id)
        wolf_profile = wolf_storage.read_profile_personality()
        profile_snippet = (wolf_profile[:150] if wolf_profile else "")

        preset = get_character_by_id(ai_wolf.personality)
        voice_hint = ""
        if preset and preset.voice_anchors:
            anchors = "、".join(f"「{a}」" for a in preset.voice_anchors)
            voice_hint = f"\n语气要求: 用{ai_wolf.name}的说话风格，声音标记: {anchors}"

        prompt = (
            f"你是{ai_wolf.name}，一名狼人。你的队友{human_name}刚刚说了：\n"
            f"\"{human_message}\"\n\n"
            f"## 你的角色\n{profile_snippet}{voice_hint}\n\n"
            f"你之前建议击杀{old_suggestion}。\n"
            f"可选目标: {', '.join(targets)}\n\n"
            f"考虑队友的意见后，你要回复队友并给出最终建议。\n"
            f"用JSON格式回复：\n"
            f'{{"reply": "对队友说的话（20字以内，用你的角色语气）",'
            f' "final_target": "从可选目标中选一个"}}'
        )

        # Determine attempt number
        try:
            dbg_dir = self.memory_base / game_state.game_id / ai_wolf.id / "debug"
            rc_count = len(list(dbg_dir.glob(
                f"wolf_reconsider_round{game_state.round_number}_*_prompt.txt"
            ))) if dbg_dir.exists() else 0
            rc_attempt = rc_count + 1
        except Exception:
            rc_attempt = 1

        self._save_debug_log(game_state.game_id, ai_wolf.id, game_state.round_number,
                             "wolf_reconsider", rc_attempt, "prompt", prompt)
        response = self._llm_call(prompt, max_tokens=150)
        self._save_debug_log(game_state.game_id, ai_wolf.id, game_state.round_number,
                             "wolf_reconsider", rc_attempt, "response", response)

        result = parse_json_response(
            response,
            required_fields=["final_target"],
            field_validators={"final_target": targets},
        )
        if result:
            reply = result.get("reply", "")
            # Strip character name prefix from reply
            reply = re.sub(rf"^{re.escape(ai_wolf.name)}[：:，,]?\s*", "", reply).strip()
            return {"reply": reply, "final_target": result["final_target"]}

        # Fallback: parse human message for a target name, respect human's preference
        log.warning(f"[WolfReconsider] JSON parse failed, falling back to message parsing")
        for t in targets:
            if t in human_message:
                log.info(f"[WolfReconsider] Fallback: found '{t}' in human message")
                return {"reply": None, "final_target": t}

        # Complete failure: keep old suggestion
        return {"reply": None, "final_target": old_suggestion}

    def seer_investigate(self, seer: "Player", game_state: "GameState") -> str:
        """Seer investigates a player.

        Args:
            seer: Seer player
            game_state: Current game state

        Returns:
            Target player name
        """
        memories = self._load_player_memories(seer, game_state)
        prompt = get_seer_check_prompt(seer, memories, game_state)

        self._save_debug_log(game_state.game_id, seer.id, game_state.round_number,
                             "seer", 1, "prompt", prompt)
        response = self._llm_call(prompt, max_tokens=400)
        self._save_debug_log(game_state.game_id, seer.id, game_state.round_number,
                             "seer", 1, "response", response)

        # Extract player name from <target> tag, fallback to raw response
        target_match = re.search(r"<target>\s*(.+?)\s*</target>", response, re.DOTALL)
        response = target_match.group(1).strip() if target_match else response.strip()
        response = response.strip('"').strip("'")

        # Build set of already-investigated names from knowledge
        knowledge_manager = KnowledgeManager(self.memory_base, game_state.game_id)
        summary = knowledge_manager.read_knowledge_summary(seer.id, "seer")
        investigated_names = set()
        for line in summary.split("\n"):
            if "✓" in line and "**" in line:
                # Extract name from "- **Name**: RESULT ✓"
                name_match = re.search(r"\*\*(.+?)\*\*", line)
                if name_match:
                    investigated_names.add(name_match.group(1))

        # Validate response — prefer uninvestigated players
        alive_others = [p for p in game_state.alive_players if p.id != seer.id]
        uninvestigated = [p for p in alive_others if p.name not in investigated_names]
        target = None

        for player in alive_others:
            if player.name.lower() in response.lower():
                # Accept LLM choice only if not already investigated
                if player.name not in investigated_names:
                    target = player
                break

        if not target:
            # Fallback: random from uninvestigated, then from all
            candidates = uninvestigated if uninvestigated else alive_others
            target = random.choice(candidates) if candidates else None

        if target:
            # Update seer knowledge with investigation result
            result = "WEREWOLF" if target.is_werewolf() else "GOOD"
            knowledge_manager.update_knowledge_summary(
                player_id=seer.id,
                role="seer",
                operation="append",
                section="### 验证身份",
                content=f"- **{target.name}**: {result} ✓（第{game_state.round_number}轮查验）",
            )

        return target.name if target else None

    def witch_action(
        self,
        witch: "Player",
        killed_player: Optional["Player"],
        game_state: "GameState",
    ) -> dict[str, Any]:
        """Witch decides whether to use potions.

        Args:
            witch: Witch player
            killed_player: Player killed by werewolves (None if no kill)
            game_state: Current game state

        Returns:
            Dict with action info: {"action": "save"/"poison"/"skip", "target": name}
        """
        memories = self._load_player_memories(witch, game_state)

        # Use authoritative game state for potion availability (not LLM memory)
        potion_status = {
            "save_potion": game_state.witch_save_available,
            "poison_potion": game_state.witch_poison_available,
        }

        killed_name = killed_player.name if killed_player else None

        prompt = get_witch_action_prompt(
            witch, memories, killed_name, potion_status, game_state
        )

        # Try up to 2 attempts with JSON validation
        result = None
        for attempt in range(2):
            self._save_debug_log(game_state.game_id, witch.id, game_state.round_number,
                                 "witch", attempt + 1, "prompt", prompt)
            response = self._llm_call(prompt, max_tokens=200)
            self._save_debug_log(game_state.game_id, witch.id, game_state.round_number,
                                 "witch", attempt + 1, "response", response)
            result = parse_json_response(
                response,
                required_fields=["action"],
                field_validators={"action": ["save", "poison", "skip"]},
            )
            if result:
                break
            log.warning(f"[Witch AI] Attempt {attempt+1} JSON parse failed, raw: {response[:200]}")

        if not result:
            # Fallback: first night with victim → save; otherwise skip
            if killed_name and game_state.witch_save_available and game_state.round_number <= 1:
                log.info(f"[Witch AI] All parse failed, fallback: auto-save {killed_name}")
                return {"action": "save", "target": killed_name}
            return {"action": "skip"}

        action = result.get("action", "skip")
        target = result.get("target")

        # System override: force skip if LLM picks an unavailable action
        if action == "save" and not game_state.witch_save_available:
            log.warning(f"[Witch AI] LLM chose 'save' but save potion unavailable, forcing skip")
            action = "skip"
        if action == "poison" and not game_state.witch_poison_available:
            log.warning(f"[Witch AI] LLM chose 'poison' but poison potion unavailable, forcing skip")
            action = "skip"

        # Get game-specific knowledge manager
        knowledge_manager = KnowledgeManager(self.memory_base, game_state.game_id)

        # Update witch knowledge
        if action == "save":
            knowledge_manager.update_knowledge_json(
                player_id=witch.id,
                role="witch",
                status_updates={"save_potion": False, "last_updated_round": game_state.round_number},
                history_append={
                    "round": game_state.round_number,
                    "action": "save",
                    "target": killed_name,
                    "reason": result.get("reasoning", "救人"),
                }
            )
            return {"action": "save", "target": killed_name}

        elif action == "poison" and target:
            knowledge_manager.update_knowledge_json(
                player_id=witch.id,
                role="witch",
                status_updates={"poison_potion": False, "last_updated_round": game_state.round_number},
                history_append={
                    "round": game_state.round_number,
                    "action": "poison",
                    "target": target,
                    "reason": result.get("reasoning", "毒杀嫌疑人"),
                }
            )
            return {"action": "poison", "target": target}

        else:
            return {"action": "skip"}

    def hunter_shoot(self, hunter: "Player", game_state: "GameState") -> str | None:
        """Hunter chooses shooting target or holds fire.

        Args:
            hunter: Hunter player
            game_state: Current game state

        Returns:
            Target player name, or None if hunter chooses not to shoot
        """
        memories = self._load_player_memories(hunter, game_state)
        prompt = get_hunter_shoot_prompt(hunter, memories, game_state)

        self._save_debug_log(game_state.game_id, hunter.id, game_state.round_number,
                             "hunter", 1, "prompt", prompt)
        response = self._llm_call(prompt, max_tokens=400)
        self._save_debug_log(game_state.game_id, hunter.id, game_state.round_number,
                             "hunter", 1, "response", response)

        # Extract player name from <target> tag
        target_match = re.search(r"<target>\s*(.+?)\s*</target>", response, re.DOTALL)
        if not target_match:
            raise ValueError(f"[Hunter AI] {hunter.name}: <target> tag missing (response truncated?)")

        choice = target_match.group(1).strip().strip('"').strip("'")

        # Check if hunter chose not to shoot
        if "不开枪" in choice:
            log.info(f"[Hunter AI] {hunter.name} chose not to shoot")
            return None

        # Validate target name
        alive_others = [p for p in game_state.alive_players if p.id != hunter.id]
        for player in alive_others:
            if player.name in choice:
                return player.name

        raise ValueError(f"[Hunter AI] {hunter.name}: invalid target '{choice}', alive={[p.name for p in alive_others]}")

    def guard_protect(self, guard: "Player", game_state: "GameState") -> str | None:
        """Guard chooses a player to protect or skips.

        Args:
            guard: Guard player
            game_state: Current game state

        Returns:
            Target player name, or None if guard chooses not to protect
        """
        memories = self._load_player_memories(guard, game_state)
        prompt = get_guard_action_prompt(guard, memories, game_state)

        self._save_debug_log(game_state.game_id, guard.id, game_state.round_number,
                             "guard", 1, "prompt", prompt)
        response = self._llm_call(prompt, max_tokens=400)
        self._save_debug_log(game_state.game_id, guard.id, game_state.round_number,
                             "guard", 1, "response", response)

        # Extract player name from <target> tag, fallback to raw response
        target_match = re.search(r"<target>\s*(.+?)\s*</target>", response, re.DOTALL)
        response = target_match.group(1).strip() if target_match else response.strip()
        response = response.strip('"').strip("'")

        # Check if guard chose not to protect
        if "不守护" in response:
            log.info(f"[Guard AI] {guard.name} chose not to protect anyone")
            # Update guard knowledge
            knowledge_manager = KnowledgeManager(self.memory_base, game_state.game_id)
            knowledge_manager.update_knowledge_summary(
                player_id=guard.id,
                role="guard",
                operation="append",
                section="### 守护记录",
                content=f"- 第{game_state.round_number}轮选择不守护任何人",
            )
            return None

        # Validate response
        alive_players = [p for p in game_state.alive_players]
        target = None

        # Check consecutive guard restriction
        last_guarded_id = game_state.last_guarded_player

        for player in alive_players:
            if player.name.lower() in response.lower():
                # Reject if same as last guarded
                if player.id != last_guarded_id:
                    target = player
                break

        if not target:
            # Fallback: random choice excluding last guarded
            candidates = [p for p in alive_players if p.id != last_guarded_id]
            if not candidates:
                candidates = alive_players
            target = random.choice(candidates) if candidates else None

        if target:
            # Update guard knowledge
            knowledge_manager = KnowledgeManager(self.memory_base, game_state.game_id)
            knowledge_manager.update_knowledge_summary(
                player_id=guard.id,
                role="guard",
                operation="append",
                section="### 守护记录",
                content=f"- 第{game_state.round_number}轮守护了 **{target.name}**",
            )

        return target.name if target else None

    def _extract_stickers(self, response: str, character_id: str) -> list[str]:
        """Extract sticker names from <stickers> tag in LLM response.

        Parses JSON array, validates against available stickers, caps at 3.

        Args:
            response: Full LLM response text
            character_id: Character personality ID for validation

        Returns:
            List of valid sticker names (0-3 items)
        """
        match = re.search(r"<stickers>(.*?)</stickers>", response, re.DOTALL)
        if not match:
            return []

        raw = match.group(1).strip()
        names: list[str] = []

        # Try JSON parse first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                names = [str(n) for n in parsed]
        except (json.JSONDecodeError, ValueError):
            # Fallback: extract quoted strings
            names = re.findall(r'[「"\'](.*?)[」"\']', raw)

        # Validate against available stickers
        available = set(get_sticker_list(character_id))
        valid = [n for n in names if n in available]
        return valid[:3]

    def _extract_speech(self, response: str) -> str | None:
        """Extract clean speech from LLM response.

        Extracts content from <speech> tags, then runs sanitize_speech
        to remove English, tags, and meta-information leaks.
        Returns None if extraction fails validation.
        """
        # Try to extract from <speech> tags
        speech_match = re.search(r"<speech>(.*?)</speech>", response, re.DOTALL)
        if speech_match:
            raw_speech = speech_match.group(1).strip().strip('"')
        else:
            # Strip all XML-like tags
            cleaned = re.sub(r"<analysis>.*?</analysis>", "", response, flags=re.DOTALL)
            cleaned = re.sub(r"</?(?:analysis|speech|vote)>", "", cleaned)
            raw_speech = cleaned.strip().strip('"')

        # Run through sanitizer to remove English, tags, and meta leaks
        return sanitize_speech(raw_speech)

    def generate_speech(self, player: "Player", game_state: "GameState") -> tuple[str, list[str]]:
        """Generate player speech for day discussion.

        Includes retry logic: if first attempt has invalid format (leaked
        XML tags), retries once. Falls back to a default response.

        Args:
            player: Speaking player
            game_state: Current game state

        Returns:
            Tuple of (speech_text, sticker_names)
        """
        memories = self._load_player_memories(player, game_state)
        system_prompt, user_prompt = get_speech_prompt(player, memories, game_state)

        # Use character-specific temperature
        preset = get_character_by_id(player.personality)
        temp = preset.temperature_override if preset else 0.7

        for attempt in range(2):
            self._save_debug_log(game_state.game_id, player.id, game_state.round_number,
                                 "speech", attempt + 1, "prompt", user_prompt, system_prompt)
            response = self._llm_call(
                user_prompt, max_tokens=500, temperature=temp,
                system_prompt=system_prompt,
            )
            self._save_debug_log(game_state.game_id, player.id, game_state.round_number,
                                 "speech", attempt + 1, "response", response)
            speech = self._extract_speech(response)
            if speech:
                stickers = self._extract_stickers(response, player.personality)
                return speech, stickers
            log.warning(f"[Speech] {player.name} attempt {attempt+1} failed validation, "
                        f"raw: {response[:100]}")

        # All attempts failed — return a safe default
        defaults = [
            "我目前还在观察，先听听大家的看法。",
            "现在信息不多，我觉得大家先分享线索比较好。",
            "我暂时没有明确的怀疑对象，先观望一下。",
        ]
        return random.choice(defaults), []

    def _extract_vote(self, response: str, alive_others: list) -> str | None:
        """Extract vote target from LLM response.

        Returns player name, "ABSTAIN", or None if extraction fails.
        """
        # Try to extract from <vote> tags
        vote_match = re.search(r"<vote>(.*?)</vote>", response, re.DOTALL)
        if vote_match:
            vote_text = vote_match.group(1).strip().strip('"').strip("'")
        else:
            # Strip all XML tags (including unclosed <analysis> from truncated responses)
            vote_text = re.sub(r"<analysis>.*?</analysis>", "", response, flags=re.DOTALL)
            vote_text = re.sub(r"<analysis>.*", "", response, flags=re.DOTALL)
            vote_text = re.sub(r"</?(?:analysis|vote|speech)>", "", vote_text)
            vote_text = vote_text.strip().strip('"').strip("'")

        if not vote_text:
            return None

        # Check for abstain
        if any(kw in vote_text for kw in ("弃票", "弃权", "ABSTAIN", "abstain")):
            return "ABSTAIN"

        # Match against alive players
        for p in alive_others:
            if p.name.lower() in vote_text.lower():
                return p.name

        return None

    def vote_decision(self, player: "Player", game_state: "GameState") -> str:
        """Player decides who to vote for.

        Includes retry logic for invalid responses.

        Args:
            player: Voting player
            game_state: Current game state

        Returns:
            Target player name or "ABSTAIN"
        """
        memories = self._load_player_memories(player, game_state)
        system_prompt, user_prompt = get_vote_prompt(player, memories, game_state)
        alive_others = [p for p in game_state.alive_players if p.id != player.id]

        # Use character-specific temperature, lowered by 0.1 for voting precision
        preset = get_character_by_id(player.personality)
        base_temp = preset.temperature_override if preset else 0.7
        temp = max(base_temp - 0.1, 0.3)

        for attempt in range(2):
            self._save_debug_log(game_state.game_id, player.id, game_state.round_number,
                                 "vote", attempt + 1, "prompt", user_prompt, system_prompt)
            response = self._llm_call(
                user_prompt, max_tokens=500, temperature=temp,
                system_prompt=system_prompt,
            )
            self._save_debug_log(game_state.game_id, player.id, game_state.round_number,
                                 "vote", attempt + 1, "response", response)
            result = self._extract_vote(response, alive_others)
            if result:
                return result
            log.warning(f"[Vote] {player.name} attempt {attempt+1} failed extraction, "
                        f"raw: {response[:100]}")

        # Fallback: random choice
        if alive_others:
            target = random.choice(alive_others)
            return target.name
        return "ABSTAIN"
