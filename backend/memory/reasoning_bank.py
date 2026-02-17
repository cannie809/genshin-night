"""ReasoningBank — Cross-game strategy distillation and learning.

Implements the missing cross-game learning component from the memory upgrade plan.
Extracts reusable reasoning strategies from completed games and accumulates
them across sessions, so AI players improve over time.

Storage layout:
    .memory/_cross_game/
    ├── experience.md       # Accumulated cross-game insights (injected into prompts)
    └── game_history.json   # Tracks completed games and outcomes
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Maximum items per role section in experience.md to prevent bloat
MAX_EXPERIENCE_ITEMS_PER_ROLE = 10
# Keep this many most recent games, clean the rest
CLEANUP_THRESHOLD = 10
# Keep this many games after cleanup (don't delete all old ones)
KEEP_AFTER_CLEANUP = 5


class ReasoningBank:
    """Cross-game strategy distillation and learning.

    Manages a persistent `_cross_game/` directory that survives individual
    game cleanup. Extracts high-value strategy items from completed games
    and accumulates them into reusable experience.

    Args:
        memory_base: Path to .memory/ directory
    """

    def __init__(self, memory_base: Path | str):
        self.memory_base = Path(memory_base)
        self.cross_game_dir = self.memory_base / "_cross_game"

    def _ensure_dir(self) -> None:
        """Ensure _cross_game directory exists."""
        self.cross_game_dir.mkdir(parents=True, exist_ok=True)

    # === Game counting and detection ===

    def list_game_ids(self) -> list[str]:
        """List all game directories (excluding _cross_game).

        Returns:
            List of game ID strings, sorted by directory modification time (oldest first)
        """
        if not self.memory_base.exists():
            return []

        game_dirs = []
        for d in self.memory_base.iterdir():
            if d.is_dir() and d.name != "_cross_game":
                game_dirs.append(d)

        # Sort by modification time, oldest first
        game_dirs.sort(key=lambda d: d.stat().st_mtime)
        return [d.name for d in game_dirs]

    def count_games(self) -> int:
        """Count active game directories."""
        return len(self.list_game_ids())

    # === Distillation ===

    def _collect_player_names(self, game_dir: Path) -> set[str]:
        """Collect all player character names from a game directory.

        Scans personality.md files for character names.

        Returns:
            Set of player character names
        """
        names: set[str] = set()
        for player_dir in game_dir.iterdir():
            if not player_dir.is_dir() or player_dir.name.startswith("_"):
                continue
            if player_dir.name in ("day_record_shared", "werewolf_shared", "event_index.json"):
                continue
            personality_path = player_dir / "profile" / "personality.md"
            if personality_path.exists():
                try:
                    first_line = personality_path.read_text(encoding="utf-8").split("\n")[0]
                    # Format: "# 角色名 - 角色档案"
                    if first_line.startswith("# ") and " - " in first_line:
                        name = first_line[2:].split(" - ")[0].strip()
                        if name:
                            names.add(name)
                except OSError:
                    pass
        return names

    @staticmethod
    def _get_all_character_names() -> set[str]:
        """Get all character names from personality presets.

        Returns all preset character names so we can filter them out
        regardless of which characters were in the current game.
        """
        try:
            from backend.ai.personality import CHARACTER_PRESETS
            names = set()
            for preset in CHARACTER_PRESETS:
                names.add(preset.name)      # e.g. "胡桃", "行秋"
                names.add(preset.title)     # e.g. "往生堂第七十七代堂主"
            return names
        except ImportError:
            return set()

    # Character-personality keywords that indicate the insight is about
    # HOW a character speaks/acts (角色) rather than WHAT strategy to use (身份)
    _CHARACTER_KEYWORDS = (
        "语气", "口吻", "风格", "人设", "角色", "声音", "口头禅",
        "自称", "说话方式", "台词", "中二", "诗意", "文言",
        "恶作剧", "冷笑话", "谜语", "嘿嘿", "诶嘿", "骗你的",
        "本堂主", "在下", "吾", "古人云", "正所谓", "冲鸭",
        "往生堂", "断罪", "全知之眼", "永夜", "驱邪",
        "契约", "风的味道", "味道", "火候", "菜谱", "加油",
        "美食", "烹饪", "料理", "武侠", "诗人", "厨师",
        "堂主", "皇女", "客卿", "浪人",
        "表演", "扮演", "模仿", "伪装成角色",
        "性格", "个性", "人格", "气质",
    )

    def _generalize_insight(self, content: str, player_names: set[str]) -> str | None:
        """Generalize a game-specific insight into a reusable role strategy.

        Replaces player names, strips round numbers, and filters out
        insights that are character-specific (personality/voice) rather
        than role-specific (game strategy).

        Returns:
            Generalized insight string, or None if too specific or character-bound
        """
        import re as _re

        result = content

        # Replace player character names with generic references
        # Include both current game's names AND all preset character names
        all_names = player_names | self._get_all_character_names()
        for name in sorted(all_names, key=len, reverse=True):  # longest first
            result = result.replace(name, "某玩家")

        # Collapse multiple "某玩家" into single reference
        result = _re.sub(r"某玩家[和与、]某玩家", "多名玩家", result)

        # Strip specific round numbers: "第1轮" → "前期", "第2轮" → "中期"
        result = _re.sub(r"第[1一]轮", "前期", result)
        result = _re.sub(r"第[2二]轮", "中期", result)
        result = _re.sub(r"第[3-9三四五六七八九]轮", "后期", result)

        # Filter: if more than 2 "某玩家" remain, it's too game-specific
        if result.count("某玩家") > 2:
            return None

        # Filter: character-personality content (角色 not 身份)
        # If the insight is about HOW to speak/act in character rather than
        # WHAT game strategy to use, reject it
        if any(kw in result for kw in self._CHARACTER_KEYWORDS):
            return None

        # Filter: pure factual statements without strategy value
        factual_keywords = ["= VILLAGER", "= WEREWOLF", "= 好人", "= 狼人",
                            "被投出", "被淘汰", "死亡", "出局"]
        strategy_keywords = ["策略", "应该", "需要", "可以", "建议", "注意",
                             "避免", "优先", "保持", "隐藏", "暴露", "观察",
                             "跳", "潜水", "带节奏", "低调", "配合", "协调",
                             "击杀", "救", "毒", "查验", "投票", "威胁"]

        has_factual = any(kw in result for kw in factual_keywords)
        has_strategy = any(kw in result for kw in strategy_keywords)

        # Keep only if it has strategy value (or is a strategy shift)
        if has_factual and not has_strategy:
            return None

        # Filter: too short after generalization
        if len(result.strip()) < 10:
            return None

        return result.strip()

    def distill_game(self, game_id: str) -> dict:
        """Extract generalized learnings from a completed game.

        Scans each player's strategy_meta.json for high-value items
        (helpful_count >= 1), generalizes them by stripping player names
        and round-specific details, then collects by role.

        Args:
            game_id: Game ID to distill

        Returns:
            Dict with extracted insights:
            {
                "game_id": "...",
                "timestamp": "...",
                "role_insights": {
                    "seer": ["insight1", "insight2"],
                    "werewolf": ["insight1"],
                    ...
                }
            }
        """
        game_dir = self.memory_base / game_id
        if not game_dir.exists():
            return {"game_id": game_id, "role_insights": {}}

        # Collect all player names for generalization
        player_names = self._collect_player_names(game_dir)

        role_insights: dict[str, list[str]] = {}

        # Scan all player directories
        for player_dir in game_dir.iterdir():
            if not player_dir.is_dir():
                continue
            if player_dir.name in ("day_record_shared", "werewolf_shared", "_cross_game"):
                continue

            # Find strategy_meta.json for each role
            knowledge_dir = player_dir / "knowledge" / "role"
            if not knowledge_dir.exists():
                continue

            for role_dir in knowledge_dir.iterdir():
                if not role_dir.is_dir():
                    continue

                role = role_dir.name
                meta_path = role_dir / "strategy_meta.json"
                if not meta_path.exists():
                    continue

                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                items = meta.get("items", {})
                # Extract valuable items (helpful_count >= 1)
                for item_id, item_data in items.items():
                    helpful = item_data.get("helpful_count", 0)
                    harmful = item_data.get("harmful_count", 0)

                    # Net positive value: helpful >= 1 and not too harmful
                    if helpful >= 1 and helpful > harmful:
                        content = item_data.get("content", "")
                        if not content:
                            # Try to extract from summary.md
                            summary_path = role_dir / "summary.md"
                            if summary_path.exists():
                                summary_text = summary_path.read_text(encoding="utf-8")
                                for line in summary_text.split("\n"):
                                    if f"[#{item_id}]" in line:
                                        parts = line.split("] ", 2)
                                        if len(parts) >= 3:
                                            content = parts[2].strip()
                                        break

                        if content and len(content) >= 10:
                            generalized = self._generalize_insight(content, player_names)
                            if generalized:
                                if role not in role_insights:
                                    role_insights[role] = []
                                role_insights[role].append(generalized)

                # Also extract from reflections for strategy shifts
                reflections_dir = player_dir / "reflections"
                if reflections_dir.exists():
                    for ref_file in reflections_dir.glob("*.md"):
                        try:
                            ref_text = ref_file.read_text(encoding="utf-8")
                            if "## 策略调整" in ref_text:
                                shift_start = ref_text.index("## 策略调整") + len("## 策略调整")
                                shift_end = ref_text.find("\n##", shift_start)
                                if shift_end == -1:
                                    shift_end = len(ref_text)
                                shift = ref_text[shift_start:shift_end].strip()
                                if shift and len(shift) >= 10:
                                    generalized = self._generalize_insight(
                                        f"[策略调整] {shift}", player_names
                                    )
                                    if generalized:
                                        if role not in role_insights:
                                            role_insights[role] = []
                                        role_insights[role].append(generalized)
                        except (OSError, ValueError):
                            continue

        # Also check werewolf shared memory for team strategies
        shared_dir = game_dir / "werewolf_shared"
        if shared_dir.exists():
            strategy_path = shared_dir / "strategy.md"
            if strategy_path.exists():
                try:
                    strategy_text = strategy_path.read_text(encoding="utf-8")
                    if "## 当前策略" in strategy_text:
                        start = strategy_text.index("## 当前策略") + len("## 当前策略")
                        end = strategy_text.find("\n## ", start)
                        if end == -1:
                            end = len(strategy_text)
                        active = strategy_text[start:end].strip()
                        if active and active != "（本轮合作策略）" and len(active) >= 10:
                            generalized = self._generalize_insight(
                                f"[团队策略] {active[:200]}", player_names
                            )
                            if generalized:
                                if "werewolf" not in role_insights:
                                    role_insights["werewolf"] = []
                                role_insights["werewolf"].append(generalized)
                except (OSError, ValueError):
                    pass

        return {
            "game_id": game_id,
            "timestamp": datetime.now().isoformat(),
            "role_insights": role_insights,
        }

    def _merge_insights(self, distilled: dict) -> None:
        """Merge distilled insights into persistent experience.

        Updates both experience.md and game_history.json.

        Args:
            distilled: Output from distill_game()
        """
        self._ensure_dir()

        # === Update game_history.json ===
        history_path = self.cross_game_dir / "game_history.json"
        if history_path.exists():
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, OSError):
                history = {"games": [], "total_distilled": 0}
        else:
            history = {"games": [], "total_distilled": 0}

        history["games"].append({
            "game_id": distilled["game_id"],
            "timestamp": distilled["timestamp"],
            "roles_with_insights": list(distilled["role_insights"].keys()),
        })
        history["total_distilled"] += 1

        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

        # === Update experience.md ===
        experience_path = self.cross_game_dir / "experience.md"
        if experience_path.exists():
            existing = experience_path.read_text(encoding="utf-8")
        else:
            existing = "# 跨局经验库\n\n> 从历史对局中蒸馏的策略经验，用于提升 AI 对局水平。\n"

        role_insights = distilled.get("role_insights", {})
        if not role_insights:
            return

        # Parse existing sections
        sections = self._parse_experience_sections(existing)

        # Merge new insights
        for role, insights in role_insights.items():
            role_cn = {
                "werewolf": "狼人", "seer": "预言家", "witch": "女巫",
                "hunter": "猎人", "villager": "村民",
            }.get(role, role)
            section_key = f"## {role_cn}经验"

            if section_key not in sections:
                sections[section_key] = []

            for insight in insights:
                # Deduplicate: skip if very similar content already exists
                if not any(self._is_similar(insight, existing_item) for existing_item in sections[section_key]):
                    sections[section_key].append(insight)

            # Trim to max items per role (keep most recent)
            if len(sections[section_key]) > MAX_EXPERIENCE_ITEMS_PER_ROLE:
                sections[section_key] = sections[section_key][-MAX_EXPERIENCE_ITEMS_PER_ROLE:]

        # Rebuild experience.md
        new_content = "# 跨局经验库\n\n> 从历史对局中蒸馏的策略经验，用于提升 AI 对局水平。\n"
        for section_header, items in sections.items():
            new_content += f"\n{section_header}\n"
            for item in items:
                # Clean up item formatting
                item_clean = item.strip()
                if not item_clean.startswith("-"):
                    item_clean = f"- {item_clean}"
                new_content += f"{item_clean}\n"

        experience_path.write_text(new_content, encoding="utf-8")

    def _parse_experience_sections(self, text: str) -> dict[str, list[str]]:
        """Parse experience.md into sections.

        Returns:
            Dict mapping section headers to lists of items
        """
        sections: dict[str, list[str]] = {}
        current_section = None

        for line in text.split("\n"):
            if line.startswith("## "):
                current_section = line.strip()
                if current_section not in sections:
                    sections[current_section] = []
            elif current_section and line.strip().startswith("-"):
                sections[current_section].append(line.strip().lstrip("- ").strip())

        return sections

    def _is_similar(self, new_item: str, existing_item: str) -> bool:
        """Check if two items are similar enough to be considered duplicates.

        Uses Jaccard index on character sets for robust Chinese text handling.
        """
        new_clean = new_item.strip()
        existing_clean = existing_item.strip()

        if new_clean == existing_clean:
            return True

        if len(new_clean) < 10 or len(existing_clean) < 10:
            return False

        # Jaccard index on character sets — works well for Chinese
        set_a = set(new_clean)
        set_b = set(existing_clean)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        if union == 0:
            return False
        return intersection / union > 0.5

    # === Cleanup ===

    def maybe_cleanup(self, threshold: int = CLEANUP_THRESHOLD) -> int:
        """Check game count and clean up if over threshold.

        Distills old games into cross-game experience before deleting.

        Args:
            threshold: Number of games that triggers cleanup

        Returns:
            Number of games cleaned up
        """
        game_ids = self.list_game_ids()
        count = len(game_ids)

        if count <= threshold:
            return 0

        # How many to remove
        to_remove_count = count - KEEP_AFTER_CLEANUP
        if to_remove_count <= 0:
            return 0

        games_to_clean = game_ids[:to_remove_count]

        log.info(f"[ReasoningBank] Cleaning up {len(games_to_clean)} old games "
                 f"(total: {count}, threshold: {threshold}, keeping: {KEEP_AFTER_CLEANUP})")

        cleaned = 0
        for game_id in games_to_clean:
            try:
                # Distill learnings
                distilled = self.distill_game(game_id)

                # Merge into persistent experience
                if distilled.get("role_insights"):
                    self._merge_insights(distilled)
                    log.info(f"[ReasoningBank] Distilled game {game_id[:8]}... "
                             f"({len(distilled['role_insights'])} roles)")

                # Delete game directory
                game_dir = self.memory_base / game_id
                if game_dir.exists():
                    shutil.rmtree(game_dir)
                    log.info(f"[ReasoningBank] Deleted game directory: {game_id[:8]}...")

                cleaned += 1

            except Exception as e:
                log.error(f"[ReasoningBank] Failed to clean game {game_id[:8]}...: {e}")

        return cleaned

    # === Reading experience ===

    def read_experience(self) -> str:
        """Read accumulated cross-game experience.

        Returns:
            Markdown content of experience, or empty string if none
        """
        experience_path = self.cross_game_dir / "experience.md"
        if not experience_path.exists():
            return ""
        try:
            return experience_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def read_role_experience(self, role: str) -> str:
        """Read experience specific to a role.

        Args:
            role: Role name (e.g., "seer", "werewolf")

        Returns:
            Role-specific experience text, or empty string
        """
        full_experience = self.read_experience()
        if not full_experience:
            return ""

        role_cn = {
            "werewolf": "狼人", "seer": "预言家", "witch": "女巫",
            "hunter": "猎人", "villager": "村民",
        }.get(role, role)

        section_header = f"## {role_cn}经验"
        if section_header not in full_experience:
            return ""

        # Extract section content
        start = full_experience.index(section_header)
        end = full_experience.find("\n## ", start + 1)
        if end == -1:
            end = len(full_experience)

        return full_experience[start:end].strip()
