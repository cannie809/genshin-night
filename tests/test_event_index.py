"""Test script for EventIndexManager functionality."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import shutil

from backend.memory.event_index import EventIndexManager

MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test"
GAME_ID = "test_event_index"


def setup():
    """Clean up test directory."""
    test_dir = MEMORY_BASE / GAME_ID
    if test_dir.exists():
        shutil.rmtree(test_dir)


def test_initialize():
    """Test event index initialization."""
    print("\n=== Testing EventIndexManager.initialize ===")
    setup()

    eim = EventIndexManager(MEMORY_BASE, GAME_ID)
    eim.initialize()

    assert eim.index_path.exists(), "Index file should exist after init"
    data = json.loads(eim.index_path.read_text())
    assert data == {"claims": [], "deaths": [], "key_contradictions": []}
    print("PASS: Initialize creates correct structure")


def test_record_death():
    """Test death recording."""
    print("\n=== Testing record_death ===")
    setup()

    eim = EventIndexManager(MEMORY_BASE, GAME_ID)
    eim.initialize()

    eim.record_death(1, "Alice", "werewolf_kill", is_night=True)
    eim.record_death(1, "Bob", "witch_poison", is_night=True)
    eim.record_death(2, "Charlie", "vote_elimination", is_night=False)

    data = eim._read_index()
    assert len(data["deaths"]) == 3, f"Should have 3 deaths, got {len(data['deaths'])}"
    assert data["deaths"][0]["player"] == "Alice"
    assert data["deaths"][0]["cause"] == "werewolf_kill"
    assert data["deaths"][1]["cause"] == "witch_poison"
    assert data["deaths"][2]["night"] is False
    print("PASS: Deaths recorded correctly")

    # Test duplicate prevention
    eim.record_death(1, "Alice", "werewolf_kill", is_night=True)
    data = eim._read_index()
    assert len(data["deaths"]) == 3, "Duplicate death should not be recorded"
    print("PASS: Duplicate deaths prevented")


def test_record_claim():
    """Test claim recording."""
    print("\n=== Testing record_claim ===")
    setup()

    eim = EventIndexManager(MEMORY_BASE, GAME_ID)
    eim.initialize()

    eim.record_claim(1, "Alice", "seer")
    eim.record_claim(2, "Bob", "seer", status="contested")

    data = eim._read_index()
    assert len(data["claims"]) == 2
    assert data["claims"][0]["status"] == "unverified"
    assert data["claims"][1]["status"] == "contested"
    print("PASS: Claims recorded correctly")

    # Test update on same player
    eim.record_claim(3, "Alice", "witch")
    data = eim._read_index()
    assert len(data["claims"]) == 2, "Same player claim should update, not add"
    assert data["claims"][0]["claimed_role"] == "witch"
    print("PASS: Same player claim updates")


def test_update_claim_status():
    """Test claim status update."""
    print("\n=== Testing update_claim_status ===")
    setup()

    eim = EventIndexManager(MEMORY_BASE, GAME_ID)
    eim.initialize()

    eim.record_claim(1, "Alice", "seer")
    eim.update_claim_status("Alice", "verified")

    data = eim._read_index()
    assert data["claims"][0]["status"] == "verified"
    print("PASS: Claim status updated")


def test_record_contradiction():
    """Test contradiction recording."""
    print("\n=== Testing record_contradiction ===")
    setup()

    eim = EventIndexManager(MEMORY_BASE, GAME_ID)
    eim.initialize()

    eim.record_contradiction(2, ["Alice", "Bob"], "seer_conflict")

    data = eim._read_index()
    assert len(data["key_contradictions"]) == 1
    assert set(data["key_contradictions"][0]["between"]) == {"Alice", "Bob"}
    print("PASS: Contradiction recorded")

    # Test duplicate prevention
    eim.record_contradiction(2, ["Bob", "Alice"], "seer_conflict")
    data = eim._read_index()
    assert len(data["key_contradictions"]) == 1, "Duplicate contradiction prevented"
    print("PASS: Duplicate contradictions prevented")


def test_get_key_facts():
    """Test key facts formatting."""
    print("\n=== Testing get_key_facts ===")
    setup()

    eim = EventIndexManager(MEMORY_BASE, GAME_ID)
    eim.initialize()

    # Empty index
    facts = eim.get_key_facts()
    assert facts == "", "Empty index should return empty string"
    print("PASS: Empty index returns empty string")

    # With data
    eim.record_death(1, "Alice", "werewolf_kill", is_night=True)
    eim.record_claim(1, "Bob", "seer")
    eim.record_contradiction(2, ["Bob", "Charlie"], "seer_conflict")

    facts = eim.get_key_facts()
    assert "关键事实" in facts
    assert "Alice" in facts
    assert "被狼人击杀" in facts
    assert "Bob 声称seer" in facts
    assert "预言家冲突" in facts
    print("PASS: Key facts formatted correctly")
    print(f"Output:\n{facts}")


if __name__ == "__main__":
    test_initialize()
    test_record_death()
    test_record_claim()
    test_update_claim_status()
    test_record_contradiction()
    test_get_key_facts()
    print("\n=== All EventIndexManager tests passed! ===")
