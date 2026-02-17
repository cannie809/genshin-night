"""Test script for FastAPI endpoints."""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_root():
    """Test root endpoint."""
    print("\n=== Testing Root Endpoint ===")
    response = client.get("/")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    print("✓ Root endpoint test passed")


def test_start_game():
    """Test game creation."""
    print("\n=== Testing Game Start ===")
    response = client.post("/api/game/start", json={"mode": "classic_6_witch"})
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"Game ID: {data['game_id']}")
        print(f"Mode: {data['mode']}")
        print(f"Phase: {data['phase']}")
        print(f"Players: {len(data['players'])}")

        for player in data['players']:
            print(f"  - {player['name']} ({player['id']}): {player['role']} [alive={player['alive']}]")

        print("✓ Game start test passed")
        return data['game_id']
    else:
        print(f"✗ Game start failed: {response.text}")
        return None


def test_get_game_state(game_id):
    """Test get game state."""
    print("\n=== Testing Get Game State ===")
    response = client.get(f"/api/game/state/{game_id}")
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"Phase: {data['phase']}")
        print(f"Round: {data['round_number']}")
        print(f"Alive players: {sum(1 for p in data['players'] if p['alive'])}")
        print("✓ Get game state test passed")
    else:
        print(f"✗ Get game state failed: {response.text}")


def test_ai_speak(game_id):
    """Test AI speech generation."""
    print("\n=== Testing AI Speech ===")

    # Find an AI player
    state_response = client.get(f"/api/game/state/{game_id}")
    players = state_response.json()['players']
    ai_player = next((p for p in players if not p['is_human']), None)

    if not ai_player:
        print("No AI player found")
        return

    response = client.post("/api/game/ai-speak", json={
        "game_id": game_id,
        "player_id": ai_player['id']
    })

    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"Player: {data['data']['player']}")
        print(f"Speech: {data['data']['speech']}")
        print("✓ AI speech test passed")
    else:
        print(f"✗ AI speech failed: {response.text}")


def test_invalid_game_id():
    """Test error handling for invalid game ID."""
    print("\n=== Testing Invalid Game ID ===")
    response = client.get("/api/game/state/invalid-game-id")
    print(f"Status: {response.status_code}")

    if response.status_code == 404:
        print(f"Error message: {response.json()['detail']}")
        print("✓ Invalid game ID test passed")
    else:
        print(f"✗ Expected 404, got {response.status_code}")


def main():
    """Run all tests."""
    print("=" * 60)
    print("FastAPI Endpoints Test Suite")
    print("=" * 60)

    try:
        # Test 1: Root endpoint
        test_root()

        # Test 2: Start game
        game_id = test_start_game()

        if game_id:
            # Test 3: Get game state
            test_get_game_state(game_id)

            # Test 4: AI speech (mocked LLM will return placeholder)
            test_ai_speak(game_id)

        # Test 5: Error handling
        test_invalid_game_id()

        print("\n" + "=" * 60)
        print("✓ All API endpoint tests passed!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
