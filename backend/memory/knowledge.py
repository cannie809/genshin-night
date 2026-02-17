"""Knowledge layer manager - wrapper for knowledge summary and JSON operations.

This module provides a high-level interface for managing role-specific knowledge.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal

from .generators import generate_initial_knowledge_json, generate_initial_knowledge_summary
from .storage import MemoryStorage


class KnowledgeManager:
    """Manages knowledge layer (summary + JSON) for players.

    Args:
        memory_base: Base directory for all memory files
        game_id: Game ID to isolate memories per game
    """

    def __init__(self, memory_base: Path | str, game_id: str = "default"):
        self.memory_base = Path(memory_base)
        self.game_id = game_id

    def initialize_player_knowledge(self, player_id: str, role: str, game_mode: str = "classic_6_witch") -> None:
        """Initialize knowledge layer for a player.

        Args:
            player_id: Player ID (e.g., "player_1")
            role: Player role
            game_mode: Current game mode for filtering rules
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)

        # Create initial summary
        summary_content = generate_initial_knowledge_summary(role, game_mode)
        storage.create_knowledge_summary(role, summary_content)

        # Create initial JSON (if role has JSON data)
        json_data = generate_initial_knowledge_json(role)
        if json_data:
            # Determine filename based on role
            filename = self._get_json_filename(role)
            if filename:
                storage.create_knowledge_json(role, filename, json_data)

    def _get_json_filename(self, role: str) -> str | None:
        """Get the appropriate JSON filename for a role.

        Args:
            role: Role name

        Returns:
            JSON filename or None if role doesn't use JSON
        """
        filenames = {
            "witch": "items.json",
            "seer": "investigations.json",
            "werewolf": "kills.json",
            "guard": "protections.json",
            "hunter": "status.json",
        }
        return filenames.get(role)

    def read_knowledge_summary(self, player_id: str, role: str) -> str:
        """Read knowledge summary for a player.

        Args:
            player_id: Player ID
            role: Player role

        Returns:
            Markdown content of knowledge summary
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)
        return storage.read_knowledge_summary(role)

    def update_knowledge_summary(
        self,
        player_id: str,
        role: str,
        operation: Literal["append", "update", "remove"],
        section: str,
        content: str = "",
        old_content: str = "",
    ) -> None:
        """Update knowledge summary for a player.

        Args:
            player_id: Player ID
            role: Player role
            operation: Type of update
            section: Section header (e.g., "## Verified Identities")
            content: New content
            old_content: Old content (for update operation)
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)
        storage.update_knowledge_summary(
            role=role,
            operation=operation,
            section=section,
            content=content,
            old_content=old_content,
        )

    def read_knowledge_json(self, player_id: str, role: str) -> dict[str, Any]:
        """Read knowledge JSON for a player.

        Args:
            player_id: Player ID
            role: Player role

        Returns:
            Parsed JSON data
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)
        filename = self._get_json_filename(role)

        if not filename:
            return {}

        return storage.read_knowledge_json(role, filename)

    def update_knowledge_json(
        self,
        player_id: str,
        role: str,
        status_updates: dict[str, Any] | None = None,
        history_append: dict[str, Any] | None = None,
    ) -> None:
        """Update knowledge JSON for a player.

        Args:
            player_id: Player ID
            role: Player role
            status_updates: Status fields to update
            history_append: History entry to append
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)
        filename = self._get_json_filename(role)

        if not filename:
            raise ValueError(f"Role {role} does not support JSON knowledge storage")

        storage.update_knowledge_json(
            role=role,
            filename=filename,
            status_updates=status_updates,
            history_append=history_append,
        )

    def merge_delta_to_summary(
        self,
        player_id: str,
        role: str,
        new_items: list[dict],
        round_num: int,
    ) -> int:
        """Merge delta items into summary.md's 'My Game Notes' area.

        Each new_item has:
        - "section": target section header (e.g., "## Suspicions (Unverified)")
        - "content": the text content
        - "source": where it came from

        Items get formatted with ID tag: [#s{index}r{round}] [H:0] {content}

        Args:
            player_id: Player ID
            role: Player role
            new_items: List of delta items from Reflector
            round_num: Current round number

        Returns:
            Number of items successfully merged
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)

        # Load or initialize strategy metadata
        meta_rel_path = f"knowledge/role/{role}/strategy_meta.json"
        meta = storage.read_json(meta_rel_path)
        if not meta:
            meta = {"items": {}, "next_index": 1}

        merged_count = 0

        for item in new_items:
            # Validate content length
            content = item.get("content", "").strip()
            if len(content) < 10:
                continue

            section = item.get("section", "")
            source = item.get("source", "")

            # Get next index and format item
            index = meta["next_index"]
            item_id = f"s{index}r{round_num}"
            formatted_content = f"[#{item_id}] [H:0] {content}"

            # Append to the specified section using existing method
            self.update_knowledge_summary(
                player_id=player_id,
                role=role,
                operation="append",
                section=section,
                content=formatted_content,
            )

            # Update metadata
            meta["items"][item_id] = {
                "index": index,
                "round": round_num,
                "section": section,
                "content": content,
                "source": source,
                "heat": 0,
            }
            meta["next_index"] += 1
            merged_count += 1

        # Save updated metadata
        storage.write_json(meta_rel_path, meta)

        return merged_count

    def save_reflection(self, player_id: str, round_num: int, reflection_data: dict) -> None:
        """Save reflection archive.

        Writes to: player_N/reflections/round_N_reflection.md

        Args:
            player_id: Player ID
            round_num: Current round number
            reflection_data: Reflection data to save
        """
        storage = MemoryStorage(player_id, self.memory_base, self.game_id)

        # Create reflections directory if it doesn't exist
        reflections_dir = storage.player_dir / "reflections"
        reflections_dir.mkdir(parents=True, exist_ok=True)

        # Format reflection content
        reflection_md = f"""# 第{round_num}轮复盘

## 摘要
{reflection_data.get('summary', '无')}

## 关键发现
{reflection_data.get('insights', '无')}

## 增量条目
{json.dumps(reflection_data.get('delta_items', []), indent=2, ensure_ascii=False)}

## 元数据
- 轮次: {round_num}
- 时间: {reflection_data.get('timestamp', '无')}
"""

        # Write reflection file
        reflection_file = reflections_dir / f"round_{round_num}_reflection.md"
        reflection_file.write_text(reflection_md, encoding="utf-8")

    # === Werewolf shared memory ===

    def initialize_werewolf_shared(self) -> None:
        """Initialize shared memory for werewolf team with 3-zone structure."""
        storage = MemoryStorage("werewolf_shared", self.memory_base, self.game_id)

        # Create initial strategy file with 3-zone structure
        initial_strategy = """# 狼队策略

## 当前策略
（本轮合作策略）

## 击杀记录
（最近3轮击杀记录）

## 早期策略
（历史策略归档）
"""
        storage.write_file("strategy.md", initial_strategy)

        # Create initial threat scores JSON
        initial_threats = {
            "threats": {},
            "history": [],
        }
        storage.write_json("threat_scores.json", initial_threats)

        # Initialize strategy metadata
        initial_meta = {"items": {}, "next_index": 1}
        storage.write_json("strategy_meta.json", initial_meta)

    def read_werewolf_shared(self) -> dict[str, Any]:
        """Read werewolf shared memory.

        Returns:
            Dict with 'strategy' (markdown) and 'threats' (JSON) keys
        """
        storage = MemoryStorage("werewolf_shared", self.memory_base, self.game_id)

        return {
            "strategy": storage.read_file("strategy.md"),
            "threats": storage.read_json("threat_scores.json"),
        }

    def update_werewolf_strategy(self, new_entry: str) -> None:
        """Append a new entry to werewolf strategy.

        Args:
            new_entry: New strategy entry to append
        """
        storage = MemoryStorage("werewolf_shared", self.memory_base, self.game_id)

        current = storage.read_file("strategy.md")
        updated = current + "\n" + new_entry
        storage.write_file("strategy.md", updated)

    def update_werewolf_threats(
        self,
        threat_updates: dict[str, int] | None = None,
        history_entry: dict[str, Any] | None = None,
    ) -> None:
        """Update werewolf threat scores.

        Args:
            threat_updates: Dict mapping player names to threat scores
            history_entry: History entry to append
        """
        storage = MemoryStorage("werewolf_shared", self.memory_base, self.game_id)

        data = storage.read_json("threat_scores.json")
        if not data:
            data = {"threats": {}, "history": []}

        if threat_updates:
            if "threats" not in data:
                data["threats"] = {}
            data["threats"].update(threat_updates)

        if history_entry:
            if "history" not in data:
                data["history"] = []
            data["history"].append(history_entry)

        storage.write_json("threat_scores.json", data)

    def archive_old_strategies(self, max_active: int = 5) -> None:
        """Move oldest active strategies to Archived section when exceeding max.

        Also maintains Kill History with sliding window of 3 rounds.

        Args:
            max_active: Maximum number of active strategy entries to keep
        """
        storage = MemoryStorage("werewolf_shared", self.memory_base, self.game_id)

        # Read current strategy
        strategy_md = storage.read_file("strategy.md")

        # Parse sections
        active_match = re.search(
            r"## 当前策略\n(.*?)(?=\n## |\Z)",
            strategy_md,
            re.DOTALL,
        )
        kill_match = re.search(
            r"## 击杀记录\n(.*?)(?=\n## |\Z)",
            strategy_md,
            re.DOTALL,
        )
        archived_match = re.search(
            r"## 早期策略\n(.*?)(?=\Z)",
            strategy_md,
            re.DOTALL,
        )

        active_section = active_match.group(1).strip() if active_match else ""
        kill_section = kill_match.group(1).strip() if kill_match else ""
        archived_section = archived_match.group(1).strip() if archived_match else ""

        # Process active strategies - split by entries (assuming each is a bullet or paragraph)
        active_entries = [e.strip() for e in active_section.split("\n\n") if e.strip()]

        if len(active_entries) > max_active:
            # Move oldest entries to archived
            to_archive = active_entries[: len(active_entries) - max_active]
            active_entries = active_entries[len(active_entries) - max_active :]

            # Add to archived section
            archived_entries = [archived_section] if archived_section else []
            archived_entries.extend(to_archive)
            archived_section = "\n\n".join(archived_entries)

        # Process kill history - keep only last 3 rounds
        kill_entries = [e.strip() for e in kill_section.split("\n\n") if e.strip()]

        # Pattern: look for "Round N" markers
        round_pattern = re.compile(r"Round (\d+)", re.IGNORECASE)
        rounds_seen = []

        for entry in kill_entries:
            match = round_pattern.search(entry)
            if match:
                round_num = int(match.group(1))
                if round_num not in rounds_seen:
                    rounds_seen.append(round_num)

        # If more than 3 rounds, archive the oldest
        if len(rounds_seen) > 3:
            rounds_seen.sort()
            rounds_to_keep = rounds_seen[-3:]

            keep_entries = []
            archive_entries = []

            for entry in kill_entries:
                match = round_pattern.search(entry)
                if match:
                    round_num = int(match.group(1))
                    if round_num in rounds_to_keep:
                        keep_entries.append(entry)
                    else:
                        archive_entries.append(entry)
                else:
                    # No round marker - keep it
                    keep_entries.append(entry)

            kill_section = "\n\n".join(keep_entries)

            # Add old kills to archived
            if archive_entries:
                archived_entries = [archived_section] if archived_section else []
                archived_entries.extend(archive_entries)
                archived_section = "\n\n".join(archived_entries)

        # Reconstruct strategy.md
        new_strategy = f"""# 狼队策略

## 当前策略
{"\n\n".join(active_entries) if active_entries else "（本轮合作策略）"}

## 击杀记录
{kill_section if kill_section else "（最近3轮击杀记录）"}

## 早期策略
{archived_section if archived_section else "（历史策略归档）"}
"""

        storage.write_file("strategy.md", new_strategy)
