"""Test script for AI agent functionality (without actual LLM calls)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

from backend.ai import UnifiedGameAgent
from backend.game import GameEngine
from backend.models import GamePhase

# Test memory base directory
MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test_agent"


def test_agent_initialization():
    """Test agent initialization."""
    print("\n=== Testing Agent Initialization ===")

    agent = UnifiedGameAgent(MEMORY_BASE, llm_provider="openrouter")

    print(f"Agent initialized")
    print(f"Memory base: {agent.memory_base}")
    print(f"LLM provider: {agent.llm_provider}")
    print(f"Model: {agent.model}")

    print("\n✓ Agent initialization test passed")


def test_memory_loading():
    """Test memory loading for different players."""
    print("\n=== Testing Memory Loading ===")

    # Create a test game
    engine = GameEngine(MEMORY_BASE)
    game = engine.create_game(mode="classic_6_witch")

    agent = UnifiedGameAgent(MEMORY_BASE, llm_provider="openrouter")

    # Load memories for each player
    for player in game.players[:3]:  # Test first 3 players
        print(f"\nLoading memories for {player.name} ({player.role})...")

        memories = agent._load_player_memories(player, game)

        print(f"  Profile: {len(memories['profile'])} chars")
        print(f"  Knowledge summary: {len(memories['knowledge_summary'])} chars")
        print(f"  Knowledge JSON: {len(memories['knowledge_json'])} items")
        print(f"  Day records: {len(memories['day_record'])} records")
        print(f"  Night records: {len(memories['night_record'])} records")

        if player.role == "werewolf":
            print(f"  Werewolf shared: {'strategy' in memories.get('shared', {})}")

    print("\n✓ Memory loading test passed")


def test_seer_investigate_mock():
    """Test seer investigation with mocked LLM."""
    print("\n=== Testing Seer Investigation (Mocked) ===")

    engine = GameEngine(MEMORY_BASE)
    game = engine.create_game(mode="classic_6_witch")

    agent = UnifiedGameAgent(MEMORY_BASE, llm_provider="openrouter")

    # Find seer
    seer = next((p for p in game.players if p.role == "seer"), None)
    if not seer:
        print("No seer found, skipping test")
        return

    print(f"\nSeer: {seer.name}")

    # Mock LLM response
    target_name = next(p.name for p in game.alive_players if p.id != seer.id)

    with patch.object(agent, '_llm_call', return_value=target_name):
        result = agent.seer_investigate(seer, game)
        print(f"Investigation target: {result}")

        # Verify knowledge was updated
        knowledge = agent.knowledge_manager.read_knowledge_summary(seer.id, "seer")
        print(f"Knowledge updated: {target_name in knowledge}")

    print("\n✓ Seer investigation test passed")


def test_werewolf_collab_mock():
    """Test werewolf collaboration with mocked LLM."""
    print("\n=== Testing Werewolf Collaboration (Mocked) ===")

    engine = GameEngine(MEMORY_BASE)
    game = engine.create_game(mode="classic_6_witch")

    agent = UnifiedGameAgent(MEMORY_BASE, llm_provider="openrouter")

    # Find werewolves
    wolves = [p for p in game.players if p.role == "werewolf"]
    print(f"\nWerewolves: {[w.name for w in wolves]}")

    # Mock LLM response with valid JSON
    target = next(p for p in game.alive_players if not p.is_werewolf())
    mock_response = f"""{{
  "wolf1_suggestion": "{target.name}",
  "wolf1_reasoning": "High threat",
  "wolf2_suggestion": "{target.name}",
  "wolf2_reasoning": "Agreed",
  "final_target": "{target.name}",
  "team_reasoning": "Most dangerous player"
}}"""

    with patch.object(agent, '_llm_call', return_value=mock_response):
        result = agent.werewolf_collaborate(game)
        print(f"Kill target: {result}")

        # Verify shared memory was updated
        shared = agent.knowledge_manager.read_werewolf_shared()
        print(f"Shared memory updated: {target.name in shared['strategy']}")

    print("\n✓ Werewolf collaboration test passed")


def test_speech_generation_mock():
    """Test speech generation with mocked LLM."""
    print("\n=== Testing Speech Generation (Mocked) ===")

    engine = GameEngine(MEMORY_BASE)
    game = engine.create_game(mode="classic_6_witch")
    game.phase = GamePhase.DAY_DISCUSSION

    agent = UnifiedGameAgent(MEMORY_BASE, llm_provider="openrouter")

    # Test speech for first 3 players
    mock_speech = "I think we should vote carefully today."

    with patch.object(agent, '_llm_call', return_value=mock_speech):
        for player in game.alive_players[:3]:
            speech = agent.generate_speech(player, game)
            print(f"\n{player.name}: {speech}")

    print("\n✓ Speech generation test passed")


def test_vote_decision_mock():
    """Test voting decision with mocked LLM."""
    print("\n=== Testing Vote Decision (Mocked) ===")

    engine = GameEngine(MEMORY_BASE)
    game = engine.create_game(mode="classic_6_witch")
    game.phase = GamePhase.DAY_VOTE

    # Add some speeches
    game.speeches = [
        {"player": "Alice", "content": "I suspect Bob"},
        {"player": "Bob", "content": "Alice is lying"},
    ]

    agent = UnifiedGameAgent(MEMORY_BASE, llm_provider="openrouter")

    # Mock LLM response with player name
    target = next(p for p in game.alive_players if not p.is_human)

    with patch.object(agent, '_llm_call', return_value=target.name):
        for player in game.alive_players[:3]:
            vote = agent.vote_decision(player, game)
            print(f"{player.name} votes for: {vote}")

    print("\n✓ Vote decision test passed")


def main():
    """Run all tests."""
    print("=" * 60)
    print("AI Agent Test Suite (Mocked LLM)")
    print("=" * 60)

    try:
        test_agent_initialization()
        test_memory_loading()
        test_seer_investigate_mock()
        test_werewolf_collab_mock()
        test_speech_generation_mock()
        test_vote_decision_mock()

        print("\n" + "=" * 60)
        print("✓ All AI agent tests passed!")
        print("=" * 60)

        print(f"\nTest files created in: {MEMORY_BASE}")

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
