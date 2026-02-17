"""Memory storage layer - handles file I/O for all memory types.

This module provides the MemoryStorage class which manages:
- Reading and writing memory files for individual players
- Creating and updating knowledge layer (Markdown + JSON)
- Managing event records (day/night)
- Handling profile data
"""

import json
import re
from pathlib import Path
from typing import Any, Literal


class MemoryStorage:
    """Manages memory file I/O for a single player or shared memory space.

    Args:
        player_id: Player ID (e.g., "player_1") or "day_record_shared" or "werewolf_shared"
        memory_base: Base directory for all memory files (e.g., backend/.memory/)
        game_id: Game ID to isolate memories per game (optional, defaults to "default")
    """

    def __init__(self, player_id: str, memory_base: Path | str, game_id: str = "default"):
        self.player_id = player_id
        self.game_id = game_id
        self.memory_base = Path(memory_base) / game_id
        self.player_dir = self.memory_base / player_id

    # === Layer 1: Event Records ===

    def create_day_record_shared(self, round_num: int, content: str) -> None:
        """Create a shared day record visible to all players.

        Args:
            round_num: Round number
            content: Markdown content of the day's events
        """
        # Day records are stored in day_record_shared/ directory
        day_dir = self.memory_base / "day_record_shared"
        day_dir.mkdir(parents=True, exist_ok=True)

        file_path = day_dir / f"round_{round_num}.md"
        file_path.write_text(content, encoding="utf-8")

    def read_day_record_shared(self, num_rounds: int = 3) -> list[str]:
        """Read recent shared day records.

        Args:
            num_rounds: Number of recent rounds to read (default: 3)

        Returns:
            List of markdown content strings for recent rounds
        """
        day_dir = self.memory_base / "day_record_shared"
        if not day_dir.exists():
            return []

        day_files = sorted(day_dir.glob("round_*.md"))
        recent = day_files[-num_rounds:] if len(day_files) > num_rounds else day_files
        return [f.read_text(encoding="utf-8") for f in recent]

    def create_night_record(self, night_num: int, content: str) -> None:
        """Create a private night record for this player.

        Args:
            night_num: Night number
            content: Markdown content of the player's night actions
        """
        night_dir = self.player_dir / "night_record"
        night_dir.mkdir(parents=True, exist_ok=True)

        file_path = night_dir / f"night_{night_num}.md"
        file_path.write_text(content, encoding="utf-8")

    def read_night_record(self, num_nights: int = 3) -> list[str]:
        """Read recent private night records for this player.

        Args:
            num_nights: Number of recent nights to read (default: 3)

        Returns:
            List of markdown content strings for recent nights
        """
        night_dir = self.player_dir / "night_record"
        if not night_dir.exists():
            return []

        night_files = sorted(night_dir.glob("night_*.md"))
        recent = night_files[-num_nights:] if len(night_files) > num_nights else night_files
        return [f.read_text(encoding="utf-8") for f in recent]

    # === Layer 2: Knowledge (Markdown Summary) ===

    def create_knowledge_summary(self, role: str, content: str) -> None:
        """Create initial knowledge summary for a role.

        Args:
            role: Role name (e.g., "seer", "witch", "werewolf", "villager")
            content: Initial markdown content with bullet point sections
        """
        knowledge_dir = self.player_dir / "knowledge" / "role" / role
        knowledge_dir.mkdir(parents=True, exist_ok=True)

        file_path = knowledge_dir / "summary.md"
        file_path.write_text(content, encoding="utf-8")

    def read_knowledge_summary(self, role: str) -> str:
        """Read knowledge summary for a role.

        Args:
            role: Role name

        Returns:
            Markdown content of the knowledge summary
        """
        file_path = self.player_dir / "knowledge" / "role" / role / "summary.md"
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8")

    def update_knowledge_summary(
        self,
        role: str,
        operation: Literal["append", "update", "remove"],
        section: str,
        content: str = "",
        old_content: str = "",
    ) -> None:
        """Update knowledge summary using bullet point operations.

        Args:
            role: Role name
            operation: Type of update ("append", "update", "remove")
            section: Section header (e.g., "## Verified Identities")
            content: New content to add/update
            old_content: Old content to replace (for "update" operation)
        """
        file_path = self.player_dir / "knowledge" / "role" / role / "summary.md"
        if not file_path.exists():
            raise FileNotFoundError(f"Knowledge summary not found: {file_path}")

        text = file_path.read_text(encoding="utf-8")

        if operation == "append":
            text = self._append_bullet(text, section, content)
        elif operation == "update":
            text = self._update_bullet(text, section, old_content, content)
        elif operation == "remove":
            text = self._remove_bullet(text, section, content)

        file_path.write_text(text, encoding="utf-8")

    def _append_bullet(self, text: str, section: str, content: str) -> str:
        """Append a bullet point to a section.

        Args:
            text: Full markdown text
            section: Section header (e.g., "## Verified Identities")
            content: Content to append (should start with "- ")

        Returns:
            Updated markdown text
        """
        # Find the section
        pattern = re.escape(section) + r"\n((?:.*\n)*?)(?=\n##|\Z)"
        match = re.search(pattern, text)

        if not match:
            # Section doesn't exist, create it at the end
            if not text.endswith("\n"):
                text += "\n"
            text += f"\n{section}\n{content}\n"
            return text

        # Find where to insert (after existing bullets or right after header)
        section_start = match.start()
        section_end = match.end()
        section_content = match.group(1)

        # Insert before next section or at end
        insertion_point = section_start + len(section) + 1 + len(section_content)

        # Ensure content starts with "- " and ends with newline
        if not content.strip().startswith("-"):
            content = f"- {content}"
        if not content.endswith("\n"):
            content += "\n"

        return text[:insertion_point] + content + text[insertion_point:]

    def _update_bullet(self, text: str, section: str, old: str, new: str) -> str:
        """Update a specific bullet point in a section.

        Args:
            text: Full markdown text
            section: Section header
            old: Old bullet content to find
            new: New bullet content to replace with

        Returns:
            Updated markdown text
        """
        # Find the section first
        pattern = re.escape(section) + r"\n((?:.*\n)*?)(?=\n##|\Z)"
        match = re.search(pattern, text)

        if not match:
            raise ValueError(f"Section not found: {section}")

        # Replace within the section
        section_text = match.group(0)
        old_escaped = re.escape(old.strip())

        # Match the bullet line (allow for leading/trailing whitespace)
        bullet_pattern = r"^(\s*-\s*)" + old_escaped + r"(\s*)$"

        if not re.search(bullet_pattern, section_text, re.MULTILINE):
            raise ValueError(f"Bullet not found in section {section}: {old}")

        # Ensure new content starts with "- "
        if not new.strip().startswith("-"):
            new = f"- {new}"

        updated_section = re.sub(bullet_pattern, new, section_text, flags=re.MULTILINE)
        return text.replace(section_text, updated_section)

    def _remove_bullet(self, text: str, section: str, content: str) -> str:
        """Remove a bullet point from a section.

        Args:
            text: Full markdown text
            section: Section header
            content: Bullet content to remove

        Returns:
            Updated markdown text
        """
        # Find the section first
        pattern = re.escape(section) + r"\n((?:.*\n)*?)(?=\n##|\Z)"
        match = re.search(pattern, text)

        if not match:
            raise ValueError(f"Section not found: {section}")

        section_text = match.group(0)
        content_escaped = re.escape(content.strip())

        # Match the full bullet line including newline
        bullet_pattern = r"^\s*-\s*" + content_escaped + r"\s*\n"

        if not re.search(bullet_pattern, section_text, re.MULTILINE):
            raise ValueError(f"Bullet not found in section {section}: {content}")

        updated_section = re.sub(bullet_pattern, "", section_text, flags=re.MULTILINE)
        return text.replace(section_text, updated_section)

    # === Layer 2: Knowledge (JSON Data) ===

    def create_knowledge_json(self, role: str, filename: str, initial_data: dict[str, Any]) -> None:
        """Create initial knowledge JSON file for a role.

        Args:
            role: Role name
            filename: JSON filename (e.g., "items.json", "investigations.json")
            initial_data: Initial data structure
        """
        knowledge_dir = self.player_dir / "knowledge" / "role" / role
        knowledge_dir.mkdir(parents=True, exist_ok=True)

        file_path = knowledge_dir / filename
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, indent=2, ensure_ascii=False)

    def read_knowledge_json(self, role: str, filename: str) -> dict[str, Any]:
        """Read knowledge JSON file for a role.

        Args:
            role: Role name
            filename: JSON filename

        Returns:
            Parsed JSON data
        """
        file_path = self.player_dir / "knowledge" / "role" / role / filename
        if not file_path.exists():
            return {}

        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def update_knowledge_json(
        self,
        role: str,
        filename: str,
        status_updates: dict[str, Any] | None = None,
        history_append: dict[str, Any] | None = None,
    ) -> None:
        """Update knowledge JSON using mixed mode (status overwrite + history append).

        Args:
            role: Role name
            filename: JSON filename
            status_updates: Dict of status fields to update (will be merged into "status" key)
            history_append: Dict to append to "usage_history" or similar history list
        """
        file_path = self.player_dir / "knowledge" / "role" / role / filename

        # Read existing data
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"status": {}, "usage_history": []}

        # Update status (overwrite)
        if status_updates:
            if "status" not in data:
                data["status"] = {}
            data["status"].update(status_updates)

        # Append to history
        if history_append:
            if "usage_history" not in data:
                data["usage_history"] = []
            data["usage_history"].append(history_append)

        # Write back
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # === Layer 3: Profile ===

    def create_profile_personality(self, content: str) -> None:
        """Create personality profile for this player.

        Args:
            content: Markdown content of the personality profile
        """
        profile_dir = self.player_dir / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        file_path = profile_dir / "personality.md"
        file_path.write_text(content, encoding="utf-8")

    def read_profile_personality(self) -> str:
        """Read personality profile for this player.

        Returns:
            Markdown content of the personality profile
        """
        file_path = self.player_dir / "profile" / "personality.md"
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8")

    # === Generic file operations ===

    def read_file(self, relative_path: str) -> str:
        """Read any file relative to this player's directory.

        Args:
            relative_path: Path relative to player directory

        Returns:
            File content as string
        """
        file_path = self.player_dir / relative_path
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8")

    def write_file(self, relative_path: str, content: str) -> None:
        """Write content to any file relative to this player's directory.

        Args:
            relative_path: Path relative to player directory
            content: Content to write
        """
        file_path = self.player_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    def read_json(self, relative_path: str) -> dict[str, Any]:
        """Read JSON file relative to this player's directory.

        Args:
            relative_path: Path relative to player directory

        Returns:
            Parsed JSON data
        """
        file_path = self.player_dir / relative_path
        if not file_path.exists():
            return {}

        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, relative_path: str, data: dict[str, Any]) -> None:
        """Write data to JSON file relative to this player's directory.

        Args:
            relative_path: Path relative to player directory
            data: Data to write
        """
        file_path = self.player_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
