"""Test script for game engine functionality."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.game import GameEngine
from backend.models import GamePhase

# Test memory base directory
MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test_engine"


def test_game_creation():
    """Test game creation and initialization."""
    print("\n=== Testing Game Creation ===")

    engine = GameEngine(MEMORY_BASE)

    # Create game with witch mode
    game = engine.create_game(mode="classic_6_witch")

    print(f"\nGame ID: {game.game_id}")
    print(f"Mode: {game.mode}")
    print(f"Phase: {game.phase}")
    print(f"Round: {game.round_number}")

    print("\nPlayers:")
    for player in game.players:
        role_display = player.role if player.is_human else "???"
        print(f"  {player.name} ({player.id}): {role_display} [{player.personality}]")

    # Verify memory files were created
    print("\nMemory files created:")
    for player in game.players:
        player_dir = MEMORY_BASE / player.id
        print(f"  {player.id}: {player_dir.exists()}")

    print("\n✓ Game creation test passed")
    return game


def test_night_phase(game):
    """Test night phase processing."""
    print("\n=== Testing Night Phase ===")

    engine = GameEngine(MEMORY_BASE)

    # Find werewolves
    werewolves = [p for p in game.alive_players if p.is_werewolf()]
    print(f"\nWerewolves: {[w.name for w in werewolves]}")

    # Find target (non-werewolf)
    target = next(p for p in game.alive_players if not p.is_werewolf())
    print(f"Target for kill: {target.name}")

    # Process werewolf kill
    result = engine.process_night_werewolf(game, target.id)
    print(f"Werewolf kill result: {result}")

    # Move to witch phase
    game.phase = GamePhase.NIGHT_WITCH

    # Find witch
    witch = next((p for p in game.alive_players if p.role == "witch"), None)
    if witch:
        print(f"\nWitch: {witch.name}")
        # Witch saves the victim
        result = engine.process_night_witch(game, use_save=True, use_poison=False)
        print(f"Witch action result: {result}")

    # Move to seer phase
    game.phase = GamePhase.NIGHT_SEER

    # Find seer
    seer = next((p for p in game.alive_players if p.role == "seer"), None)
    if seer:
        print(f"\nSeer: {seer.name}")
        # Seer checks a werewolf
        check_target = werewolves[0]
        result = engine.process_night_seer(game, check_target.id)
        print(f"Seer check result: {result}")

    # Process morning
    print("\n=== Processing Morning ===")
    dead_ids = engine.process_morning(game)
    print(f"Deaths: {[game.get_player_by_id(did).name for did in dead_ids]}")

    print("\nAlive players:")
    for player in game.alive_players:
        print(f"  {player.name}")

    print("\n✓ Night phase test passed")


def test_day_phase(game):
    """Test day phase processing."""
    print("\n=== Testing Day Phase ===")

    engine = GameEngine(MEMORY_BASE)

    game.phase = GamePhase.DAY_DISCUSSION

    # Add speeches
    for player in game.alive_players[:3]:
        engine.add_speech(game, player.id, f"This is {player.name} speaking.")

    print(f"\nSpeeches: {len(game.speeches)}")
    for speech in game.speeches:
        print(f"  {speech['player']}: {speech['content']}")

    # Move to voting
    game.phase = GamePhase.DAY_VOTE

    # Everyone votes for first werewolf
    werewolf = next(p for p in game.alive_players if p.is_werewolf())
    print(f"\nVoting target: {werewolf.name}")

    for player in game.alive_players:
        engine.add_vote(game, player.id, werewolf.id)

    print(f"Votes cast: {len(game.votes)}")

    # Process vote
    result = engine.process_vote(game)
    print(f"\nVote result: {result}")

    print("\nAlive players after vote:")
    for player in game.alive_players:
        print(f"  {player.name} ({player.role})")

    print("\n✓ Day phase test passed")


def test_victory_check(game):
    """Test victory condition checking."""
    print("\n=== Testing Victory Check ===")

    engine = GameEngine(MEMORY_BASE)

    game.phase = GamePhase.CHECK_VICTORY

    # Check for victory
    from backend.game.victory import check_victory

    winner = check_victory(game)
    print(f"\nWinner: {winner}")

    print(f"\nAlive werewolves: {len(game.alive_werewolves)}")
    print(f"Alive villagers: {len(game.alive_villagers)}")

    print("\n✓ Victory check test passed")


def test_phase_transitions(game):
    """Test phase transition logic."""
    print("\n=== Testing Phase Transitions ===")

    engine = GameEngine(MEMORY_BASE)

    phases = [
        GamePhase.NIGHT_WEREWOLF,
        GamePhase.NIGHT_WITCH,
        GamePhase.NIGHT_SEER,
        GamePhase.DAY_DISCUSSION,
        GamePhase.DAY_VOTE,
        GamePhase.CHECK_VICTORY,
    ]

    game.phase = GamePhase.NIGHT_WEREWOLF

    print("\nPhase transitions:")
    for expected_next in phases[1:]:
        next_phase = engine.next_phase(game)
        print(f"  {game.phase} -> {next_phase}")

    print("\n✓ Phase transition test passed")


def test_witch_save_not_permanent():
    """Test: A player saved by witch in round 1 should die when attacked in round 2.

    This tests the user-reported bug where a saved player becomes permanently immune.
    """
    print("\n=== Testing Witch Save NOT Permanent ===")

    engine = GameEngine(MEMORY_BASE)
    game = engine.create_game(mode="classic_6_witch")

    # Find roles
    target = next(p for p in game.players if p.role == "villager")
    witch = next(p for p in game.players if p.role == "witch")
    print(f"Target: {target.name} ({target.id})")
    print(f"Witch: {witch.name} ({witch.id})")

    # === Round 1: Werewolves attack target, witch SAVES ===
    print("\n--- Round 1 ---")
    game.phase = GamePhase.NIGHT_WEREWOLF
    game.round_number = 1

    engine.process_night_werewolf(game, target.id)
    print(f"  night_kills: {game.night_kills}")
    print(f"  saved_player before witch: {game.saved_player}")

    engine.process_night_witch(game, use_save=True)
    print(f"  saved_player after witch: {game.saved_player}")
    print(f"  witch_save_available: {game.witch_save_available}")

    dead_ids = engine.process_morning(game)
    print(f"  Morning deaths: {dead_ids}")
    print(f"  Target alive: {target.alive}")
    assert target.alive, "Target should be alive after being saved in round 1"
    assert len(dead_ids) == 0, "No one should die when witch saves"
    print("  ✓ Round 1: Target correctly saved")

    # === Reset for Round 2 ===
    game.reset_round_data()
    game.round_number = 2
    print(f"\n--- Round 2 (after reset) ---")
    print(f"  night_kills after reset: {game.night_kills}")
    print(f"  saved_player after reset: {game.saved_player}")
    print(f"  witch_save_available: {game.witch_save_available}")

    # === Round 2: Werewolves attack SAME target, witch does NOT save ===
    game.phase = GamePhase.NIGHT_WEREWOLF
    engine.process_night_werewolf(game, target.id)
    print(f"  night_kills: {game.night_kills}")

    # Witch skips (no potion left)
    engine.process_night_witch(game, use_save=False)
    print(f"  saved_player after witch skip: {game.saved_player}")

    dead_ids = engine.process_morning(game)
    print(f"  Morning deaths: {dead_ids}")
    print(f"  Target alive: {target.alive}")
    assert not target.alive, "BUG: Target should be DEAD in round 2 (no save used)!"
    assert target.id in dead_ids, "BUG: Target ID should be in dead list!"
    print("  ✓ Round 2: Target correctly killed (not permanently immune)")

    # === Round 3: Test with another target to be thorough ===
    game.reset_round_data()
    game.round_number = 3
    target2 = next(p for p in game.alive_players if p.role != "werewolf" and p.id != target.id)
    print(f"\n--- Round 3 ---")
    print(f"  New target: {target2.name} ({target2.id})")

    game.phase = GamePhase.NIGHT_WEREWOLF
    engine.process_night_werewolf(game, target2.id)
    engine.process_night_witch(game, use_save=False)
    dead_ids = engine.process_morning(game)
    print(f"  Morning deaths: {dead_ids}")
    print(f"  Target2 alive: {target2.alive}")
    assert not target2.alive, "Target2 should be dead"
    print("  ✓ Round 3: New target correctly killed")

    print("\n✓ Witch save immunity test PASSED - save is NOT permanent")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Game Engine Test Suite")
    print("=" * 60)

    try:
        # Test 1: Game creation
        game = test_game_creation()

        # Test 2: Night phase
        test_night_phase(game)

        # Test 3: Day phase
        test_day_phase(game)

        # Test 4: Victory check
        test_victory_check(game)

        # Test 5: Phase transitions (new game)
        game2 = GameEngine(MEMORY_BASE).create_game()
        test_phase_transitions(game2)

        # Test 6: Witch save immunity bug
        test_witch_save_not_permanent()

        print("\n" + "=" * 60)
        print("✓ All game engine tests passed!")
        print("=" * 60)

        print(f"\nTest files created in: {MEMORY_BASE}")

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
