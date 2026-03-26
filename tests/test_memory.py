"""Test script for memory system functionality."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.memory import (
    KnowledgeManager,
    ProfileManager,
    RecordsManager,
)

# Test memory base directory
MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test"


def test_profile_manager():
    """Test profile manager functionality."""
    print("\n=== Testing ProfileManager ===")

    pm = ProfileManager(MEMORY_BASE)

    # Create profiles for different personality types
    personalities = ["hutao", "zhongli", "fischl"]
    for i, personality in enumerate(personalities):
        player_id = f"player_{i + 1}"
        player_name = f"Player {i + 1}"

        print(f"\nCreating profile for {player_name} ({personality})...")
        pm.initialize_player_profile(player_id, player_name, personality)

        # Read back
        profile = pm.read_player_profile(player_id)
        print(f"Profile created: {len(profile)} characters")
        print(f"First 200 chars: {profile[:200]}...")

    print("\n✓ ProfileManager tests passed")


def test_knowledge_manager():
    """Test knowledge manager functionality."""
    print("\n=== Testing KnowledgeManager ===")

    km = KnowledgeManager(MEMORY_BASE)

    # Test different roles
    roles = [
        ("player_1", "seer"),
        ("player_2", "witch"),
        ("player_3", "werewolf"),
        ("player_4", "villager"),
    ]

    for player_id, role in roles:
        print(f"\nInitializing knowledge for {player_id} ({role})...")
        km.initialize_player_knowledge(player_id, role)

        # Read summary
        summary = km.read_knowledge_summary(player_id, role)
        print(f"Summary created: {len(summary)} characters")

        # Read JSON (if applicable)
        json_data = km.read_knowledge_json(player_id, role)
        if json_data:
            print(f"JSON data: {json_data}")

    # Test updating knowledge summary for seer
    print("\n\nTesting knowledge summary updates for seer...")
    km.update_knowledge_summary(
        player_id="player_1",
        role="seer",
        operation="append",
        section="## Verified Identities",
        content="- **Player 2 (Bob)**: WEREWOLF ✓ (checked Round 1)",
    )
    updated_summary = km.read_knowledge_summary("player_1", "seer")
    print(f"Updated summary (showing verified section):")
    print(updated_summary[updated_summary.find("## Verified") : updated_summary.find("## Verified") + 200])

    # Test updating JSON for witch
    print("\n\nTesting knowledge JSON updates for witch...")
    km.update_knowledge_json(
        player_id="player_2",
        role="witch",
        status_updates={"save_potion": False, "last_updated_round": 1},
        history_append={"round": 1, "action": "save", "target": "Player 3", "reason": "Important player"},
    )
    updated_json = km.read_knowledge_json("player_2", "witch")
    print(f"Updated JSON: {updated_json}")

    # Test werewolf shared memory
    print("\n\nTesting werewolf shared memory...")
    km.initialize_werewolf_shared()
    shared = km.read_werewolf_shared()
    print(f"Initial shared strategy: {shared['strategy'][:200]}...")

    km.update_werewolf_strategy("- Round 1: Agreed to kill Player 3 (high threat)")
    km.update_werewolf_threats(
        threat_updates={"Player 3": 10, "Player 5": 7},
        history_entry={"round": 1, "target": "Player 3", "reasoning": "Strong player"},
    )

    shared = km.read_werewolf_shared()
    print(f"Updated threats: {shared['threats']}")

    print("\n✓ KnowledgeManager tests passed")


def test_records_manager():
    """Test records manager functionality."""
    print("\n=== Testing RecordsManager ===")

    rm = RecordsManager(MEMORY_BASE)

    # Create a day record
    print("\nCreating day record for Round 1...")
    rm.create_day_record(
        round_num=1,
        morning_deaths=[{"name": "Player 3"}],
        speeches=[
            {"player": "Player 1 (You)", "content": "I think Player 2 is suspicious."},
            {"player": "Player 2 (Bob)", "content": "I'm innocent! Player 5 is the werewolf."},
        ],
        votes={"Player 1": "Player 2", "Player 2": "Player 5", "Player 4": "Player 2"},
        eliminated={"name": "Player 2", "votes": 2},
    )

    # Read back
    day_records = rm.read_day_records(num_rounds=1)
    print(f"Day record created: {len(day_records[0])} characters")
    print(f"First 300 chars:\n{day_records[0][:300]}...")

    # Create night records for different roles
    print("\n\nCreating night records...")

    # Seer night record
    rm.create_night_record(
        player_id="player_1",
        player_name="Player 1",
        role="seer",
        night_num=1,
        action={
            "target": "Player 2 (Bob)",
            "result": "WEREWOLF",
            "thoughts": "Confirmed my suspicion!",
        },
    )

    # Witch night record
    rm.create_night_record(
        player_id="player_2",
        player_name="Player 2",
        role="witch",
        night_num=1,
        action={
            "victim": "Player 3",
            "save_used": True,
            "poison_used": False,
            "reasoning": "Player 3 is important to the town.",
        },
    )

    # Werewolf night record
    rm.create_night_record(
        player_id="player_3",
        player_name="Player 3",
        role="werewolf",
        night_num=1,
        action={
            "target": "Player 5",
            "discussion": "Partner and I agreed on target.",
            "reasoning": "Player 5 is vocal and dangerous.",
        },
    )

    # Villager night record
    rm.create_night_record(
        player_id="player_4",
        player_name="Player 4",
        role="villager",
        night_num=1,
    )

    # Read back night records
    for player_id in ["player_1", "player_2", "player_3", "player_4"]:
        night_records = rm.read_night_records(player_id, num_nights=1)
        if night_records:
            print(f"\n{player_id} night record (first 200 chars):")
            print(night_records[0][:200])

    print("\n✓ RecordsManager tests passed")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Memory System Test Suite")
    print("=" * 60)

    try:
        test_profile_manager()
        test_knowledge_manager()
        test_records_manager()

        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)

        print(f"\nTest files created in: {MEMORY_BASE}")
        print("\nYou can inspect the generated files to verify content.")

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
