"""End-to-end test: two players (alice, bob) play separate games.

Verifies:
1. Per-player directory isolation (.memory/alice/, .memory/bob/)
2. Game records, night records, day records write to correct player dirs
3. PlayerProfiler DB is per-player (no cross-contamination)
4. Cleanup scoped per-player (alice's cleanup doesn't touch bob's games)
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.game.engine import GameEngine
from backend.memory.player_profiler import PlayerProfiler
from backend.models import GamePhase


def _play_one_round(engine: GameEngine, game):
    """Simulate one full round: night actions → morning → speeches → vote → end_round."""
    # --- Night ---
    wolves = [p for p in game.alive_players if p.role == "werewolf"]
    good_targets = [p for p in game.alive_players if p.role != "werewolf"]

    # Guard (if present)
    if game.phase == GamePhase.NIGHT_GUARD:
        guard = next((p for p in game.alive_players if p.role == "guard"), None)
        if guard:
            engine.process_night_guard(game, good_targets[0].id)
        engine.next_phase(game)

    # Werewolf kill
    assert game.phase == GamePhase.NIGHT_WEREWOLF
    target = good_targets[0]
    engine.process_night_werewolf(game, target.id)
    engine.next_phase(game)

    # Witch (if present)
    if game.phase == GamePhase.NIGHT_WITCH:
        witch = next((p for p in game.alive_players if p.role == "witch"), None)
        if witch:
            # First round: save. Later: skip.
            if game.round_number == 1 and game.witch_save_available:
                engine.process_night_witch(game, use_save=True)
            else:
                engine.process_night_witch(game)
        engine.next_phase(game)

    # Seer (if present and alive)
    if game.phase == GamePhase.NIGHT_SEER:
        seer = next((p for p in game.alive_players if p.role == "seer"), None)
        if seer:
            check_target = wolves[0] if wolves[0].id != target.id else wolves[-1]
            engine.process_night_seer(game, check_target.id)
        engine.next_phase(game)

    # --- Morning ---
    assert game.phase == GamePhase.DAY_DISCUSSION
    dead = engine.process_morning(game)

    # --- Speeches (fake) ---
    for p in game.alive_players:
        engine.add_speech(game, p.id, f"{p.name}：我觉得有人很可疑。")

    # --- Vote ---
    game.phase = GamePhase.DAY_VOTE
    alive = [p for p in game.alive_players]
    # Everyone votes for the first alive non-human player
    vote_target = next(p for p in alive if not p.is_human)
    for p in alive:
        engine.add_vote(game, p.id, vote_target.id)

    from backend.models import RoundSnapshot

    snapshot = RoundSnapshot(
        night_kills=list(game.night_kills),
        saved_player=game.saved_player,
        poisoned_player=game.poisoned_player,
        seer_checked=game.seer_checked,
        guarded_player=game.guarded_player,
        wolf_collab_result=dict(game.wolf_collab_result),
        votes=dict(game.votes),
        speeches=list(game.speeches),
    )

    result = engine.process_vote(game)

    # --- End round (writes records) ---
    engine.end_round(game, snapshot=snapshot)

    # --- Advance through CHECK_VICTORY to next round (or GAME_END) ---
    engine.next_phase(game)  # DAY_VOTE → CHECK_VICTORY
    if game.phase == GamePhase.CHECK_VICTORY:
        engine.next_phase(game)  # CHECK_VICTORY → NIGHT_* or GAME_END
    if game.phase == GamePhase.HUNTER_SHOOT:
        # Hunter was eliminated by vote — skip shooting for test simplicity
        engine.process_hunter_shoot(game, None)  # hold fire
        engine.next_phase(game)  # → CHECK_VICTORY
        if game.phase == GamePhase.CHECK_VICTORY:
            engine.next_phase(game)
    return result


def _fake_llm(prompt, max_tokens=500, temperature=0.7):
    """Fake LLM that returns plausible profiler observations."""
    import json

    return json.dumps(
        [
            {
                "perspective": "good_teammate",
                "behavior_category": "speech_pattern",
                "game_phase": "mid",
                "observation": "玩家在发言中喜欢用逻辑推理来说服其他人",
                "keywords": "逻辑,推理,说服",
            },
            {
                "perspective": "wolf_opponent",
                "behavior_category": "voting_behavior",
                "game_phase": "mid",
                "observation": "投票倾向于跟随预言家的判断",
                "keywords": "跟票,预言家,投票",
            },
        ]
    )


def test_multiuser_isolation():
    """Main test: two players, separate memory, no cross-contamination."""
    tmp = Path(tempfile.mkdtemp())
    engine = GameEngine(tmp)
    engine.set_llm_call_fn(_fake_llm)

    try:
        # === Alice plays a game ===
        print("\n=== Alice starts a game ===")
        alice_game = engine.create_game(mode="classic_6_witch", human_identity="alice")
        assert alice_game.memory_base == tmp / "alice"
        assert alice_game.human_identity == "alice"

        _play_one_round(engine, alice_game)
        if alice_game.phase != GamePhase.GAME_END:
            _play_one_round(engine, alice_game)
        print(f"  Alice game phase: {alice_game.phase}, winner: {alice_game.winner}")

        # === Bob plays a game ===
        print("\n=== Bob starts a game ===")
        bob_game = engine.create_game(mode="classic_6_hunter", human_identity="bob")
        assert bob_game.memory_base == tmp / "bob"
        assert bob_game.human_identity == "bob"

        _play_one_round(engine, bob_game)
        print(f"  Bob game phase: {bob_game.phase}, winner: {bob_game.winner}")

        # === Verify directory isolation ===
        print("\n=== Checking directory structure ===")
        alice_dir = tmp / "alice"
        bob_dir = tmp / "bob"

        assert alice_dir.exists(), "alice/ directory missing"
        assert bob_dir.exists(), "bob/ directory missing"

        alice_games = [d for d in alice_dir.iterdir() if d.is_dir() and d.name != "_cross_game"]
        bob_games = [d for d in bob_dir.iterdir() if d.is_dir() and d.name != "_cross_game"]

        assert len(alice_games) == 1, f"Expected 1 alice game dir, got {len(alice_games)}"
        assert len(bob_games) == 1, f"Expected 1 bob game dir, got {len(bob_games)}"
        assert alice_games[0].name == alice_game.game_id
        assert bob_games[0].name == bob_game.game_id

        print(f"  alice/ has game dir: {alice_games[0].name[:8]}...")
        print(f"  bob/   has game dir: {bob_games[0].name[:8]}...")

        # Check records were written to correct player dirs
        alice_shared = alice_games[0] / "day_record_shared"
        bob_shared = bob_games[0] / "day_record_shared"
        alice_records = list(alice_shared.glob("*.md")) if alice_shared.exists() else []
        bob_records = list(bob_shared.glob("*.md")) if bob_shared.exists() else []

        print(f"  alice day records: {len(alice_records)} files")
        print(f"  bob   day records: {len(bob_records)} files")
        assert len(alice_records) >= 1, "Alice should have at least 1 day record"
        assert len(bob_records) >= 1, "Bob should have at least 1 day record"

        # Check night records
        alice_p0_nights = list((alice_games[0] / "player_0").glob("night_record/*.md"))
        bob_p0_nights = list((bob_games[0] / "player_0").glob("night_record/*.md"))
        print(f"  alice night records (player_0): {len(alice_p0_nights)} files")
        print(f"  bob   night records (player_0): {len(bob_p0_nights)} files")

        # === Verify profiler DB isolation ===
        print("\n=== Checking profiler DB isolation ===")

        # Force a game to end so profiler distills
        # Manually set winner and trigger distillation
        alice_game.winner = "good"
        alice_profiler = PlayerProfiler(alice_game.memory_base)
        try:
            result = alice_profiler.distill_game(alice_game.game_id, alice_game, _fake_llm)
            print(f"  Alice profiler distilled: {result['count']} observations")
        finally:
            alice_profiler.close()

        bob_game.winner = "werewolf"
        bob_profiler = PlayerProfiler(bob_game.memory_base)
        try:
            result = bob_profiler.distill_game(bob_game.game_id, bob_game, _fake_llm)
            print(f"  Bob   profiler distilled: {result['count']} observations")
        finally:
            bob_profiler.close()

        # Verify separate DB files exist
        alice_db = alice_dir / "_cross_game" / "player_profile.db"
        bob_db = bob_dir / "_cross_game" / "player_profile.db"
        assert alice_db.exists(), "Alice profiler DB missing"
        assert bob_db.exists(), "Bob profiler DB missing"
        print(f"  alice DB: {alice_db.relative_to(tmp)}")
        print(f"  bob   DB: {bob_db.relative_to(tmp)}")

        # Verify content isolation: count rows in each DB
        def count_rows(db_path, table):
            conn = sqlite3.connect(str(db_path))
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.close()
            return n

        alice_obs = count_rows(alice_db, "player_observations")
        bob_obs = count_rows(bob_db, "player_observations")
        alice_hist = count_rows(alice_db, "game_history")
        bob_hist = count_rows(bob_db, "game_history")

        print(f"  alice observations: {alice_obs}, game_history: {alice_hist}")
        print(f"  bob   observations: {bob_obs}, game_history: {bob_hist}")

        assert alice_obs > 0, "Alice should have observations"
        assert bob_obs > 0, "Bob should have observations"
        assert alice_hist == 1, "Alice should have 1 game in history"
        assert bob_hist == 1, "Bob should have 1 game in history"

        # Verify no cross-contamination: alice's DB shouldn't have bob's game_id
        conn = sqlite3.connect(str(alice_db))
        bob_in_alice = conn.execute(
            "SELECT COUNT(*) FROM game_history WHERE game_id = ?", (bob_game.game_id,)
        ).fetchone()[0]
        conn.close()
        assert bob_in_alice == 0, "Bob's game should NOT be in Alice's DB"

        conn = sqlite3.connect(str(bob_db))
        alice_in_bob = conn.execute(
            "SELECT COUNT(*) FROM game_history WHERE game_id = ?", (alice_game.game_id,)
        ).fetchone()[0]
        conn.close()
        assert alice_in_bob == 0, "Alice's game should NOT be in Bob's DB"

        print("  ✓ No cross-contamination between alice and bob DBs")

        # === Verify cleanup isolation ===
        print("\n=== Checking cleanup isolation ===")
        # Create 25 games for alice (exceeds KEEP_RECENT_GAMES=20)
        for i in range(24):
            engine.create_game(mode="classic_6_witch", human_identity="alice")

        alice_games_after = [d for d in alice_dir.iterdir() if d.is_dir() and d.name != "_cross_game"]
        bob_games_after = [d for d in bob_dir.iterdir() if d.is_dir() and d.name != "_cross_game"]

        print(f"  alice games after 25 creations: {len(alice_games_after)}")
        print(f"  bob   games (untouched): {len(bob_games_after)}")

        # cleanup runs BEFORE creation → steady state is KEEP_RECENT_GAMES + 1
        assert len(alice_games_after) <= 21, f"Alice cleanup should keep ≤21, got {len(alice_games_after)}"
        assert len(bob_games_after) == 1, f"Bob's games should be untouched, got {len(bob_games_after)}"
        print("  ✓ Alice cleanup did NOT affect Bob's games")

        # === No shared root-level game dirs ===
        root_game_dirs = [d for d in tmp.iterdir() if d.is_dir() and d.name not in ("alice", "bob", "_cross_game")]
        assert len(root_game_dirs) == 0, f"Found root-level dirs that should be inside player dirs: {root_game_dirs}"
        print("  ✓ No orphaned game dirs at root level")

        print("\n" + "=" * 50)
        print("ALL CHECKS PASSED ✓")
        print("=" * 50)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_multiuser_isolation()
