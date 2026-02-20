"""Strategy tracker - manages strategy metadata with effectiveness counters.

This module provides StrategyTracker which maintains strategy_meta.json files
tracking helpful/harmful counters for strategy items.

Strategy lifecycle:
创建 (H:0) → 被使用 (H:1) → 被验证 (H:3) → 高权重注入 prompt
                  ↓
             使用后失败 (H:1, X:1) → 降权/标记风险
                                          ↓
                                累计 X:3 → 归档 (移出 summary)
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class StrategyTracker:
    """Tracks strategy item metadata with helpful/harmful counters.

    Strategy items are created from reflections and tracked for effectiveness.
    Items with high helpful counts get priority in prompt injection.
    Items with high harmful counts get archived/removed.
    """

    def __init__(self, meta_path: Path | str):
        """Initialize strategy tracker.

        Args:
            meta_path: Path to strategy_meta.json file
        """
        self.meta_path = Path(meta_path)

    def initialize(self) -> None:
        """Create initial empty metadata file."""
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)

        initial_data = {"items": {}, "next_index": 1}

        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, indent=2, ensure_ascii=False)

        log.info(f"Initialized strategy metadata at {self.meta_path}")

    def add_item(self, item_id: str, created_round: int, source: str) -> None:
        """Add a new strategy item.

        Args:
            item_id: Unique ID (e.g., "s1r2")
            created_round: Round when created
            source: Source of the item (e.g., "round_2_reflection")
        """
        data = self._read_meta()

        if item_id in data["items"]:
            log.warning(f"Strategy item {item_id} already exists, skipping")
            return

        data["items"][item_id] = {
            "created_round": created_round,
            "source": source,
            "helpful_count": 0,
            "harmful_count": 0,
            "related": [],
        }

        self._write_meta(data)
        log.info(f"Added strategy item {item_id} from {source} (round {created_round})")

    def update_helpful(self, item_id: str, delta: int = 1) -> bool:
        """Increment helpful counter.

        Args:
            item_id: Strategy item ID
            delta: Amount to increment (default: 1)

        Returns:
            False if item doesn't exist, True otherwise
        """
        data = self._read_meta()

        if item_id not in data["items"]:
            log.warning(f"Strategy item {item_id} not found, cannot update helpful count")
            return False

        data["items"][item_id]["helpful_count"] += delta
        new_count = data["items"][item_id]["helpful_count"]

        self._write_meta(data)
        log.info(f"Updated helpful count for {item_id}: +{delta} -> {new_count}")

        return True

    def update_harmful(self, item_id: str, delta: int = 1) -> bool:
        """Increment harmful counter.

        Args:
            item_id: Strategy item ID
            delta: Amount to increment (default: 1)

        Returns:
            False if item doesn't exist, True otherwise
        """
        data = self._read_meta()

        if item_id not in data["items"]:
            log.warning(f"Strategy item {item_id} not found, cannot update harmful count")
            return False

        data["items"][item_id]["harmful_count"] += delta
        new_count = data["items"][item_id]["harmful_count"]

        self._write_meta(data)
        log.info(f"Updated harmful count for {item_id}: +{delta} -> {new_count}")

        return True

    def get_high_value_items(self, min_helpful: int = 2) -> list[str]:
        """Get item IDs with helpful_count >= min_helpful.

        Args:
            min_helpful: Minimum helpful count threshold (default: 2)

        Returns:
            List of item IDs sorted by helpful count (descending)
        """
        data = self._read_meta()

        high_value = [item_id for item_id, meta in data["items"].items() if meta["helpful_count"] >= min_helpful]

        # Sort by helpful count (descending)
        high_value.sort(key=lambda item_id: data["items"][item_id]["helpful_count"], reverse=True)

        return high_value

    def get_items_to_archive(self, max_harmful: int = 3) -> list[str]:
        """Get item IDs with harmful_count >= max_harmful.

        Args:
            max_harmful: Maximum harmful count threshold (default: 3)

        Returns:
            List of item IDs that should be archived
        """
        data = self._read_meta()

        to_archive = [item_id for item_id, meta in data["items"].items() if meta["harmful_count"] >= max_harmful]

        return to_archive

    def item_exists(self, item_id: str) -> bool:
        """Check if an item ID exists.

        Args:
            item_id: Strategy item ID

        Returns:
            True if item exists, False otherwise
        """
        data = self._read_meta()
        return item_id in data["items"]

    def get_next_index(self) -> int:
        """Get and increment the next available index for new items.

        Returns:
            Next available index number
        """
        data = self._read_meta()

        next_idx = data["next_index"]
        data["next_index"] = next_idx + 1

        self._write_meta(data)
        log.debug(f"Retrieved next index: {next_idx}")

        return next_idx

    def _read_meta(self) -> dict:
        """Read metadata from disk."""
        if not self.meta_path.exists():
            # Return empty structure if file doesn't exist
            return {"items": {}, "next_index": 1}

        with open(self.meta_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_meta(self, data: dict) -> None:
        """Write metadata to disk."""
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
