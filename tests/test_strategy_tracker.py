"""Test script for StrategyTracker functionality."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import shutil

from backend.memory.strategy_tracker import StrategyTracker

MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test"
META_PATH = MEMORY_BASE / "test_game" / "player_1" / "knowledge" / "role" / "seer" / "strategy_meta.json"


def setup():
    """Clean up test directory."""
    if META_PATH.exists():
        META_PATH.unlink()
    META_PATH.parent.mkdir(parents=True, exist_ok=True)


def test_initialize():
    """Test metadata initialization."""
    print("\n=== Testing StrategyTracker.initialize ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()

    assert META_PATH.exists(), "Meta file should exist after init"
    data = json.loads(META_PATH.read_text())
    assert data == {"items": {}, "next_index": 1}
    print("PASS: Initialize creates correct structure")


def test_add_item():
    """Test adding strategy items."""
    print("\n=== Testing add_item ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()

    tracker.add_item("s1r1", created_round=1, source="round_1_reflection")
    tracker.add_item("s2r1", created_round=1, source="round_1_reflection")

    data = tracker._read_meta()
    assert len(data["items"]) == 2
    assert data["items"]["s1r1"]["created_round"] == 1
    assert data["items"]["s1r1"]["helpful_count"] == 0
    assert data["items"]["s1r1"]["harmful_count"] == 0
    print("PASS: Items added correctly")

    # Test duplicate prevention
    tracker.add_item("s1r1", created_round=2, source="duplicate")
    data = tracker._read_meta()
    assert data["items"]["s1r1"]["created_round"] == 1, "Duplicate should not overwrite"
    print("PASS: Duplicates prevented")


def test_update_helpful():
    """Test helpful counter updates."""
    print("\n=== Testing update_helpful ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()
    tracker.add_item("s1r1", 1, "test")

    assert tracker.update_helpful("s1r1") is True
    assert tracker.update_helpful("s1r1") is True

    data = tracker._read_meta()
    assert data["items"]["s1r1"]["helpful_count"] == 2
    print("PASS: Helpful counter updated")

    assert tracker.update_helpful("nonexistent") is False
    print("PASS: Nonexistent item returns False")


def test_update_harmful():
    """Test harmful counter updates."""
    print("\n=== Testing update_harmful ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()
    tracker.add_item("s1r1", 1, "test")

    tracker.update_harmful("s1r1")
    tracker.update_harmful("s1r1")
    tracker.update_harmful("s1r1")

    data = tracker._read_meta()
    assert data["items"]["s1r1"]["harmful_count"] == 3
    print("PASS: Harmful counter updated")


def test_get_high_value_items():
    """Test high value item filtering."""
    print("\n=== Testing get_high_value_items ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()

    tracker.add_item("s1r1", 1, "test")
    tracker.add_item("s2r1", 1, "test")
    tracker.add_item("s3r2", 2, "test")

    # s1 gets H:3, s2 gets H:1, s3 gets H:2
    for _ in range(3):
        tracker.update_helpful("s1r1")
    tracker.update_helpful("s2r1")
    for _ in range(2):
        tracker.update_helpful("s3r2")

    high = tracker.get_high_value_items(min_helpful=2)
    assert len(high) == 2, f"Should have 2 high-value items, got {len(high)}"
    assert high[0] == "s1r1", "Highest should be first"
    assert high[1] == "s3r2"
    print("PASS: High value items filtered and sorted correctly")


def test_get_items_to_archive():
    """Test archive threshold detection."""
    print("\n=== Testing get_items_to_archive ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()

    tracker.add_item("s1r1", 1, "test")
    tracker.add_item("s2r1", 1, "test")

    for _ in range(3):
        tracker.update_harmful("s1r1")
    tracker.update_harmful("s2r1")

    to_archive = tracker.get_items_to_archive(max_harmful=3)
    assert len(to_archive) == 1
    assert to_archive[0] == "s1r1"
    print("PASS: Archive threshold items detected")


def test_get_next_index():
    """Test next index increment."""
    print("\n=== Testing get_next_index ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()

    idx1 = tracker.get_next_index()
    idx2 = tracker.get_next_index()
    idx3 = tracker.get_next_index()

    assert idx1 == 1
    assert idx2 == 2
    assert idx3 == 3
    print("PASS: Next index increments correctly")


def test_item_exists():
    """Test item existence check."""
    print("\n=== Testing item_exists ===")
    setup()

    tracker = StrategyTracker(META_PATH)
    tracker.initialize()

    assert tracker.item_exists("s1r1") is False
    tracker.add_item("s1r1", 1, "test")
    assert tracker.item_exists("s1r1") is True
    print("PASS: Item existence check works")


if __name__ == "__main__":
    test_initialize()
    test_add_item()
    test_update_helpful()
    test_update_harmful()
    test_get_high_value_items()
    test_get_items_to_archive()
    test_get_next_index()
    test_item_exists()
    print("\n=== All StrategyTracker tests passed! ===")
