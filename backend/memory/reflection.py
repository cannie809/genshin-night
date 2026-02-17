"""Reflection pipeline: Generator → Reflector → Curator.

This module implements the core reflection system that upgrades player memory
by extracting insights from game events using LLM analysis.

Generator (pure Python): Collects round events into structured text
Reflector (LLM call): Generates delta strategy entries from events
Curator (pure Python): Validates and merges deltas into memory
"""

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .storage import MemoryStorage
from .event_index import EventIndexManager

if TYPE_CHECKING:
    from backend.models import GameState, Player, RoundSnapshot

log = logging.getLogger(__name__)


class ReflectionPipeline:
    """Reflection pipeline: Generator → Reflector → Curator.

    Generator and Curator are pure Python (zero LLM cost).
    Reflector uses one LLM call per player.
    """

    def __init__(
        self,
        memory_base: Path,
        game_id: str,
        llm_call_fn: Callable[[str, int, float], str] | None = None,
    ):
        """
        Args:
            memory_base: Path to .memory/ directory
            game_id: Current game ID
            llm_call_fn: Optional callback for LLM calls.
                         Signature: (prompt: str, max_tokens: int, temperature: float) -> str
                         If None, Reflector step is skipped.
        """
        self.memory_base = Path(memory_base)
        self.game_id = game_id
        self.llm_call_fn = llm_call_fn

    def generate_events(self, player: "Player", game_state: "GameState",
                         snapshot: "RoundSnapshot | None" = None) -> str:
        """Generator step: Collect round events into structured text.

        Pure Python - no LLM call. Extracts from snapshot (preferred) or game_state:
        - Morning deaths (from event_index.json — reliable across background tasks)
        - Speeches summary (from snapshot)
        - Vote results (from snapshot)
        - Player's own night actions (from snapshot, role-filtered)

        Args:
            player: Player to generate events for
            game_state: Current game state (for player lookup)
            snapshot: Round data snapshot (preferred over game_state for transient data)

        Returns:
            Structured event summary text in Chinese
        """
        rn = self._round_num if hasattr(self, '_round_num') else game_state.round_number
        lines = [f"# 第{rn}轮事件摘要", ""]

        # Read from snapshot or fall back to live game_state
        speeches = snapshot["speeches"] if snapshot else game_state.speeches
        votes = snapshot["votes"] if snapshot else game_state.votes
        night_kills = snapshot["night_kills"] if snapshot else game_state.night_kills
        seer_checked = snapshot["seer_checked"] if snapshot else game_state.seer_checked
        saved_player = snapshot["saved_player"] if snapshot else game_state.saved_player
        poisoned_player = snapshot["poisoned_player"] if snapshot else game_state.poisoned_player
        guarded_player = snapshot["guarded_player"] if snapshot else game_state.guarded_player

        # Read event_index once for both morning deaths and vote elimination
        round_deaths: list[dict] = []
        try:
            eim = EventIndexManager(self.memory_base, self.game_id)
            index_data = eim._read_index()
            round_deaths = [d for d in index_data.get("deaths", []) if d["round"] == rn]
        except Exception as e:
            log.warning(f"[Reflection] Failed to read event_index: {e}")

        # === Morning deaths ===
        lines.append("## 早晨死亡")

        morning_deaths = []
        cause_map = {"werewolf_kill": "狼人击杀", "witch_poison": "女巫毒杀", "hunter_shot": "猎人击杀"}
        for d in round_deaths:
            if d.get("night", False):
                morning_deaths.append({
                    "name": d["player"],
                    "cause": cause_map.get(d.get("cause", ""), d.get("cause", "未知")),
                })

        if morning_deaths:
            for death in morning_deaths:
                lines.append(f"- **{death['name']}** 死亡 (原因: {death['cause']})")
        else:
            lines.append("- 平安夜，无人死亡")

        lines.append("")

        # === Day speeches (from snapshot) ===
        lines.append("## 白天发言")

        if speeches:
            for speech in speeches:
                speaker = speech.get("player", "Unknown")
                content = speech.get("content", "")
                # Truncate long speeches
                if len(content) > 150:
                    content = content[:150] + "..."
                lines.append(f"- **{speaker}**: {content}")
        else:
            lines.append("- (本轮无发言)")

        lines.append("")

        # === Voting results (from snapshot) ===
        lines.append("## 投票结果")

        if votes:
            # Count votes
            vote_counts: dict[str, int] = {}
            for target_id in votes.values():
                if target_id == "ABSTAIN":
                    vote_counts["弃权"] = vote_counts.get("弃权", 0) + 1
                else:
                    target = game_state.get_player_by_id(target_id)
                    if target:
                        vote_counts[target.name] = vote_counts.get(target.name, 0) + 1

            # Show vote distribution
            for target_name, count in sorted(vote_counts.items(), key=lambda x: -x[1]):
                lines.append(f"- **{target_name}**: {count}票")

            # Vote elimination conclusion (from cached round_deaths)
            vote_eliminated = [
                d["player"] for d in round_deaths
                if not d.get("night", False) and d.get("cause") == "vote_elimination"
            ]
            if vote_eliminated:
                lines.append(f"- → {'、'.join(vote_eliminated)} 被放逐出局")
            else:
                lines.append("- → 平票，无人出局")

            # Show who this player voted for
            if player.id in votes:
                voted_id = votes[player.id]
                if voted_id == "ABSTAIN":
                    lines.append("- (你选择了弃权)")
                else:
                    voted = game_state.get_player_by_id(voted_id)
                    if voted:
                        lines.append(f"- (你投票给: **{voted.name}**)")
        else:
            lines.append("- (本轮无投票)")

        lines.append("")

        # === Hunter shot events (day-phase deaths from hunter) ===
        hunter_shots = [
            d for d in round_deaths
            if not d.get("night", False) and d.get("cause") == "hunter_shot"
        ]
        if hunter_shots:
            lines.append("## 猎人开枪")
            for d in hunter_shots:
                lines.append(f"- **{d['player']}** 被猎人开枪带走")
            lines.append("")

        # === Player's own night actions (from snapshot, role-filtered) ===
        if player.role in ["seer", "witch", "werewolf", "guard"]:
            lines.append("## 我的夜间行动")

            if player.role == "seer":
                if seer_checked:
                    target = game_state.get_player_by_id(seer_checked)
                    if target:
                        result = game_state.seer_checks.get(seer_checked, "Unknown")
                        lines.append(f"- 验人: **{target.name}** → 结果: {result}")
                else:
                    lines.append("- (本轮未进行验人)")

            elif player.role == "witch":
                if saved_player:
                    saved = game_state.get_player_by_id(saved_player)
                    if saved:
                        lines.append(f"- 使用解药: 救下 **{saved.name}**")

                if poisoned_player:
                    poisoned = game_state.get_player_by_id(poisoned_player)
                    if poisoned:
                        lines.append(f"- 使用毒药: 毒杀 **{poisoned.name}**")

                if not saved_player and not poisoned_player:
                    lines.append("- (本轮未使用药水)")

            elif player.role == "werewolf":
                if night_kills:
                    kill_ids = night_kills
                    targets = [game_state.get_player_by_id(kid) for kid in kill_ids if game_state.get_player_by_id(kid)]
                    if targets:
                        target_names = ", ".join([t.name for t in targets])
                        lines.append(f"- 团队击杀目标: **{target_names}**")
                else:
                    lines.append("- (本轮未击杀)")

            elif player.role == "guard":
                if guarded_player:
                    guarded = game_state.get_player_by_id(guarded_player)
                    if guarded:
                        lines.append(f"- 守护目标: **{guarded.name}**")
                else:
                    lines.append("- (本轮未守护任何人)")

            lines.append("")

        return "\n".join(lines)

    def reflect(
        self,
        player: "Player",
        game_state: "GameState",
        events_text: str,
        current_summary: str,
    ) -> dict[str, Any] | None:
        """Reflector step: LLM generates delta entries.

        One LLM call using self.llm_call_fn.

        Args:
            player: Player to reflect for
            game_state: Current game state
            events_text: Generated event summary
            current_summary: Current knowledge summary content

        Returns:
            Dict with reflection result or None if LLM call fails:
            {
                "updates": [{"id": "s3r2", "delta_helpful": 1, "reason": "..."}],
                "new_items": [{"section": "## section_name", "content": "...", "source": "..."}],
                "strategy_shift": "one-line strategy change description"
            }
        """
        if not self.llm_call_fn:
            log.warning(f"[Reflection] No LLM function provided, skipping reflection for {player.name}")
            return None

        # Build role-specific prompt
        prompt = self._build_reflection_prompt(
            player=player,
            game_state=game_state,
            events_text=events_text,
            current_summary=current_summary,
        )

        # Call LLM
        try:
            response = self.llm_call_fn(prompt, max_tokens=800, temperature=0.7)

            if not response:
                log.warning(f"[Reflection] Empty LLM response for {player.name}")
                return None

            # Parse JSON from response
            result = self._parse_json_response(response)

            if not result:
                log.warning(f"[Reflection] Failed to parse JSON from LLM response for {player.name}")
                return None

            return result

        except Exception as e:
            log.error(f"[Reflection] LLM call failed for {player.name}: {e}")
            return None

    def _build_reflection_prompt(
        self,
        player: "Player",
        game_state: "GameState",
        events_text: str,
        current_summary: str,
    ) -> str:
        """Build role-specific reflection prompt.

        Args:
            player: Player to reflect for
            game_state: Current game state
            events_text: Generated event summary
            current_summary: Current knowledge summary

        Returns:
            Formatted prompt string
        """
        role = player.role
        round_num = game_state.round_number

        # Role-specific analysis guidance
        role_guidance = {
            "werewolf": "分析重点：隐藏身份策略、威胁评估、队友配合、伪装计划",
            "seer": "分析重点：验人结果整合、信任网络构建、身份公开时机、引导村民",
            "witch": "分析重点：药水使用决策、保护优先级、关键时刻判断",
            "hunter": "分析重点：目标评估、开枪计划、威胁优先级",
            "villager": "分析重点：怀疑追踪、逻辑分析、投票决策、保护真神",
        }

        guidance = role_guidance.get(role, "分析重点：逻辑推理、投票决策")

        # JSON format example
        json_format = """{
  "updates": [
    {"id": "s1r2", "delta_helpful": 1, "reason": "该观察被本轮事件证实"},
    {"id": "s2r3", "delta_helpful": -1, "reason": "该判断与新信息矛盾"}
  ],
  "new_items": [
    {
      "section": "### 怀疑对象",
      "content": "player_3 在第3轮声称预言家，但与验人结果矛盾",
      "source": "round_3_discussion"
    }
  ],
  "strategy_shift": "改变策略：不再信任 player_3，优先调查其队友"
}"""

        round_num = self._round_num if hasattr(self, '_round_num') else round_num

        prompt = f"""你是{player.name}，角色是{role}，正在第{round_num}轮结束后进行复盘。

## 你当前的策略笔记
{current_summary}

## 本轮发生的事件
{events_text}

## 任务
回顾本轮的所有事件，从你的角色视角分析：
{guidance}

具体输出：
1. **new_items**: 本轮有哪些新的发现或洞察？每条内容应该简洁（50-100字），指向具体事实或推理
2. **updates**: 之前的笔记中是否有需要更新有用度的条目？用 delta_helpful (+1/-1) 标记
3. **strategy_shift**: 你的整体策略是否需要调整？用一句话概括

**重要提示**:
- new_items 的 section 必须对应你"本局笔记"中的某个 ### 章节标题
- new_items 每条内容必须 >= 10 字符
- new_items 不要超过 5 条
- updates 只引用已存在的笔记条目 ID（格式如 s1r2）
- 只返回 JSON，不要其他内容

**返回格式**:
{json_format}"""

        return prompt

    def _parse_json_response(self, response: str) -> dict[str, Any] | None:
        """Parse JSON from LLM response.

        Handles:
        - Markdown code blocks (```json ... ```)
        - Leading/trailing text
        - Malformed JSON

        Args:
            response: Raw LLM response

        Returns:
            Parsed dict or None if parsing fails
        """
        # Strip markdown code blocks
        response = response.strip()

        # Try to extract JSON from markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)
        else:
            # Try to find raw JSON object
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                response = json_match.group(0)

        # Parse JSON
        try:
            data = json.loads(response)

            # Validate structure
            if not isinstance(data, dict):
                return None

            # Ensure required keys exist
            if "new_items" not in data:
                data["new_items"] = []
            if "updates" not in data:
                data["updates"] = []
            if "strategy_shift" not in data:
                data["strategy_shift"] = ""

            return data

        except json.JSONDecodeError as e:
            log.warning(f"[Reflection] JSON decode error: {e}")
            return None

    def curate(
        self,
        player: "Player",
        game_state: "GameState",
        reflection_result: dict[str, Any],
    ) -> bool:
        """Curator step: Validate and merge deltas.

        Pure Python - no LLM call.

        Validation rules:
        1. JSON format valid (already parsed) → else discard
        2. new_items.content length >= 10 chars → reject empty entries
        3. new_items count <= 5 → prevent flooding
        4. updates reference existing IDs in strategy_meta.json → ignore invalid refs
        5. strategy_shift non-empty → archive to reflections/

        Actions:
        - Append new_items to summary.md section (with [#id] [H:0] format)
        - Update strategy_meta.json with new item metadata
        - Save reflection to reflections/round_N_reflection.md

        Args:
            player: Player to curate for
            game_state: Current game state
            reflection_result: Parsed reflection result from Reflector

        Returns:
            True if at least one item was successfully merged
        """
        storage = MemoryStorage(player.id, self.memory_base, self.game_id)
        role = player.role
        round_num = self._round_num if hasattr(self, '_round_num') else game_state.round_number

        merged_count = 0

        # === Load or initialize strategy_meta.json ===
        meta_path = f"knowledge/role/{role}/strategy_meta.json"
        meta = storage.read_json(meta_path)

        if not meta:
            meta = {"items": {}, "next_index": 1}

        if "items" not in meta:
            meta["items"] = {}
        if "next_index" not in meta:
            meta["next_index"] = 1

        # === Validate and merge new_items ===
        new_items = reflection_result.get("new_items", [])

        # Rule 3: Limit to 5 items
        if len(new_items) > 5:
            log.warning(f"[Curator] Too many new_items ({len(new_items)}), limiting to 5")
            new_items = new_items[:5]

        # Load current summary
        current_summary = storage.read_knowledge_summary(role)

        for item in new_items:
            # Rule 2: Content length >= 10 chars
            content = item.get("content", "")
            if len(content.strip()) < 10:
                log.warning(f"[Curator] Skipping item with content too short: {content}")
                continue

            section = item.get("section", "")
            source = item.get("source", f"round_{round_num}_reflection")

            # Validate section exists in summary
            if section not in current_summary:
                log.warning(f"[Curator] Section not found in summary: {section}, skipping item")
                continue

            # Generate ID for this item
            item_id = f"s{meta['next_index']}r{round_num}"
            meta["next_index"] += 1

            # Format entry with ID and helpfulness tag
            entry = f"[#{item_id}] [H:0] {content}"

            # Append to summary.md
            try:
                storage.update_knowledge_summary(
                    role=role,
                    operation="append",
                    section=section,
                    content=entry,
                )

                # Update metadata
                meta["items"][item_id] = {
                    "created_round": round_num,
                    "source": source,
                    "helpful_count": 0,
                    "harmful_count": 0,
                    "related": [],
                }

                merged_count += 1
                log.info(f"[Curator] Merged item {item_id}: {content[:50]}...")

            except Exception as e:
                log.error(f"[Curator] Failed to append item to summary: {e}")

        # === Process updates ===
        updates = reflection_result.get("updates", [])

        for update in updates:
            item_id = update.get("id", "")
            delta = update.get("delta_helpful", 0)
            reason = update.get("reason", "")

            # Rule 4: Validate ID exists in metadata
            if item_id not in meta["items"]:
                log.warning(f"[Curator] Invalid item ID in update: {item_id}, skipping")
                continue

            # Update helpful_count
            if delta > 0:
                meta["items"][item_id]["helpful_count"] += delta
            elif delta < 0:
                meta["items"][item_id]["harmful_count"] += abs(delta)

            log.info(f"[Curator] Updated {item_id} helpfulness: delta={delta}, reason={reason}")

        # === Save strategy_meta.json ===
        storage.write_json(meta_path, meta)

        # === Archive reflection (Rule 5) ===
        strategy_shift = reflection_result.get("strategy_shift", "")

        if strategy_shift.strip():
            reflection_content = self._format_reflection_archive(
                player=player,
                game_state=game_state,
                events_text="",  # We don't need to store full events text
                strategy_shift=strategy_shift,
                new_items=new_items,
            )

            reflection_path = f"reflections/round_{round_num}_reflection.md"
            storage.write_file(reflection_path, reflection_content)
            log.info(f"[Curator] Archived reflection for {player.name} round {round_num}")

        return merged_count > 0

    def _format_reflection_archive(
        self,
        player: "Player",
        game_state: "GameState",
        events_text: str,
        strategy_shift: str,
        new_items: list[dict[str, Any]],
    ) -> str:
        """Format reflection archive markdown.

        Args:
            player: Player
            game_state: Current game state
            events_text: Event summary (can be empty)
            strategy_shift: Strategy change description
            new_items: List of new items merged

        Returns:
            Formatted markdown string
        """
        rn = self._round_num if hasattr(self, '_round_num') else game_state.round_number
        lines = [
            f"# 第{rn}轮复盘 - {player.name}（{player.role}）",
            "",
            "## 策略调整",
            strategy_shift,
            "",
            "## 新发现",
        ]

        if new_items:
            for item in new_items:
                section = item.get("section", "")
                content = item.get("content", "")
                lines.append(f"- **{section}**: {content}")
        else:
            lines.append("（本轮无新发现）")

        lines.append("")

        return "\n".join(lines)

    def run(self, player: "Player", game_state: "GameState", round_num: int | None = None,
            snapshot: "RoundSnapshot | None" = None) -> bool:
        """Run full pipeline for one player.

        Args:
            player: Player to reflect for
            game_state: Current game state
            round_num: Actual round number (before increment). If None, uses game_state.round_number.
            snapshot: Round data snapshot for accurate event generation.

        Returns:
            True if reflection was successful
        """
        self._round_num = round_num if round_num is not None else game_state.round_number
        log.info(f"[Reflection] Starting pipeline for {player.name} (round {self._round_num})")

        # Step 1: Generate events
        events_text = self.generate_events(player, game_state, snapshot=snapshot)

        if not events_text.strip():
            log.warning(f"[Reflection] No events generated for {player.name}, skipping")
            return False

        # Step 2: Load current summary
        storage = MemoryStorage(player.id, self.memory_base, self.game_id)
        current_summary = storage.read_knowledge_summary(player.role)

        if not current_summary.strip():
            log.warning(f"[Reflection] No current summary for {player.name}, skipping")
            return False

        # Step 3: Reflect (LLM call)
        reflection_result = self.reflect(player, game_state, events_text, current_summary)

        if not reflection_result:
            log.warning(f"[Reflection] Reflection failed for {player.name}")
            return False

        # Step 4: Curate (validate and merge)
        success = self.curate(player, game_state, reflection_result)

        if success:
            log.info(f"[Reflection] Pipeline completed successfully for {player.name}")
        else:
            log.warning(f"[Reflection] Curation produced no merges for {player.name}")

        return success
