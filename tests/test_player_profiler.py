"""Test script for PlayerProfiler cross-game human behavior profiling."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import shutil
import time

from backend.memory.player_profiler import PlayerProfiler, _PERSPECTIVE_MATRIX, _ROLE_SIDE

MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test_profiler"


def setup():
    """Clean up test directory."""
    if MEMORY_BASE.exists():
        shutil.rmtree(MEMORY_BASE)
    MEMORY_BASE.mkdir(parents=True, exist_ok=True)


def _make_mock_game_state(
    game_id="test_game",
    human_role="villager",
    winner="good",
    speeches=None,
    votes=None,
):
    """Create a minimal mock GameState for testing."""
    class MockPlayer:
        def __init__(self, id, name, role, is_human=False, alive=True, personality="test"):
            self.id = id
            self.name = name
            self.role = role
            self.is_human = is_human
            self.alive = alive
            self.personality = personality
        def is_werewolf(self):
            return self.role == "werewolf"

    class MockGameState:
        def __init__(self):
            self.game_id = game_id
            self.round_number = 2
            self.winner = winner
            self.players = [
                MockPlayer("player_0", "旅行者", human_role, is_human=True),
                MockPlayer("player_1", "胡桃", "werewolf"),
                MockPlayer("player_2", "行秋", "seer"),
                MockPlayer("player_3", "万叶", "villager"),
                MockPlayer("player_4", "钟离", "werewolf"),
                MockPlayer("player_5", "班尼特", "witch" if human_role != "witch" else "villager"),
            ]
            self.speeches = speeches or []
            self.votes = votes or {}
        def get_player_by_id(self, pid):
            for p in self.players:
                if p.id == pid:
                    return p
            return None

    return MockGameState()


def _mock_llm_call(prompt, max_tokens=800, temperature=0.3):
    """Mock LLM that returns perspective-tagged observations."""
    # Detect which perspectives the prompt asks for (match section headers, not examples)
    perspectives = []
    for tag in ["good_teammate", "good_opponent", "wolf_teammate", "wolf_opponent"]:
        if f"### 视角: {tag}" in prompt:
            perspectives.append(tag)
    if not perspectives:
        perspectives = ["good_teammate"]

    observations = []
    p1 = perspectives[0]
    observations.append({
        "perspective": p1,
        "game_phase": "early",
        "behavior_category": "speech_pattern",
        "observation": f"[{p1}] 第一轮倾向沉默观察，不主动发言，等其他人先表态后再发言",
        "keywords": "沉默,观察,第一轮,不发言,等待",
    })
    observations.append({
        "perspective": p1,
        "game_phase": "mid",
        "behavior_category": "defense_pattern",
        "observation": f"[{p1}] 被质疑时会反向指控最积极的发言者来转移注意力",
        "keywords": "反指控,防御,转移注意力,被质疑",
    })

    if len(perspectives) > 1:
        p2 = perspectives[1]
        observations.append({
            "perspective": p2,
            "game_phase": "early",
            "behavior_category": "speech_pattern",
            "observation": f"[{p2}] 旅行者首轮沉默时在收集信息，应在其沉默期谨慎发言减少暴露",
            "keywords": "沉默期,信息收集,谨慎发言,减少暴露",
        })
        observations.append({
            "perspective": p2,
            "game_phase": "mid",
            "behavior_category": "voting_pattern",
            "observation": f"[{p2}] 投票倾向跟随多数意见，可利用此特点引导投票方向",
            "keywords": "跟票,多数意见,从众,引导",
        })

    return json.dumps(observations, ensure_ascii=False)


def test_database_creation():
    """Test 1: Database and tables are created correctly."""
    print("\n=== Test 1: Database creation ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        conn = profiler._get_conn()
        # Check tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        assert "player_observations" in table_names, f"Missing player_observations, got {table_names}"
        assert "game_history" in table_names, f"Missing game_history, got {table_names}"
        assert "served_observations" in table_names, f"Missing served_observations, got {table_names}"
        print("PASS: Database tables created correctly")

        # Check indexes
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {i["name"] for i in indexes}
        assert "idx_obs_role_phase" in index_names
        assert "idx_obs_perspective" in index_names
        assert "idx_obs_confidence" in index_names
        print("PASS: Indexes created correctly")
    finally:
        profiler.close()


def test_distill_extraction():
    """Test 2: Distillation extracts observations via LLM."""
    print("\n=== Test 2: Distillation extraction ===")
    setup()

    game_state = _make_mock_game_state(
        game_id="distill_test",
        human_role="villager",
        winner="good",
        speeches=[{"player": "旅行者", "content": "我觉得胡桃很可疑"}],
    )

    # Create day record files
    game_dir = MEMORY_BASE / "distill_test"
    day_dir = game_dir / "day_record_shared"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "round_1.md").write_text(
        "# 第1轮\n## 发言\n**旅行者**: \"我先观察一下\"\n## 投票\n旅行者 → 胡桃",
        encoding="utf-8",
    )

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        result = profiler.distill_game("distill_test", game_state, _mock_llm_call)
        count = result["count"]
        assert count > 0, f"Expected observations, got {count}"
        assert isinstance(result["matched_ids"], set)
        print(f"PASS: Distilled {count} observations")

        # Verify in database
        stats = profiler.get_stats()
        assert stats["total_observations"] > 0
        assert stats["total_games_distilled"] == 1
        print(f"PASS: Stats correct: {stats}")
    finally:
        profiler.close()


def test_incremental_update_reinforce():
    """Test 3: Same-phase match merges text/keywords, no confidence change."""
    print("\n=== Test 3: Incremental update (same-phase match) ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert initial
        status1, id1 = profiler._upsert_observation(
            player_role="villager", game_phase="early",
            behavior_category="speech_pattern",
            observation="第一轮喜欢沉默观察",
            keywords="沉默,观察,第一轮",
            game_outcome="player_won",
            outcome_perspective="good_counter",
            source_game_id="game_1",
        )
        assert status1 == "inserted"
        assert id1 is not None

        # Same-phase match with similar keywords
        status2, id2 = profiler._upsert_observation(
            player_role="villager", game_phase="early",
            behavior_category="speech_pattern",
            observation="开局倾向不说话，先听别人的意见再做判断",
            keywords="沉默,不说话,第一轮,观察",
            game_outcome="player_won",
            outcome_perspective="good_counter",
            source_game_id="game_2",
        )
        assert status2 == "matched"
        assert id2 == id1, f"Should return old id {id1}, got {id2}"

        # Confidence stays at 0.5 (no change during distill), match_count incremented
        conn = profiler._get_conn()
        row = conn.execute(
            "SELECT confidence, match_count FROM player_observations WHERE id = ?", (id1,)
        ).fetchone()
        assert row["confidence"] == 0.5, f"Expected 0.5 (unchanged), got {row['confidence']}"
        assert row["match_count"] == 2
        print("PASS: Same-phase match: confidence unchanged at 0.5, match_count=2")
    finally:
        profiler.close()


def test_incremental_update_contradiction():
    """Test 4: Different-phase match inserts new, returns matched with old_id."""
    print("\n=== Test 4: Incremental update (different-phase match) ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert observation for early phase
        status1, id1 = profiler._upsert_observation(
            player_role="villager", game_phase="early",
            behavior_category="speech_pattern",
            observation="早期喜欢沉默观察等待",
            keywords="沉默,观察,早期,等待",
            game_outcome="player_won",
            outcome_perspective="good_counter",
            source_game_id="game_1",
        )
        assert status1 == "inserted"

        # Different phase but similar keywords (Jaccard > 0.3)
        status2, id2 = profiler._upsert_observation(
            player_role="villager", game_phase="late",
            behavior_category="speech_pattern",
            observation="后期也会沉默观察等待",
            keywords="沉默,观察,后期,等待",
            game_outcome="player_won",
            outcome_perspective="good_counter",
            source_game_id="game_2",
        )
        assert status2 == "matched", f"Expected 'matched', got '{status2}'"
        assert id2 == id1, f"Should return old id {id1}, got {id2}"

        # Both observations exist, old confidence unchanged
        conn = profiler._get_conn()
        rows = conn.execute(
            "SELECT game_phase, confidence FROM player_observations ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["confidence"] == 0.5, f"Old should stay 0.5, got {rows[0]['confidence']}"
        assert rows[1]["confidence"] == 0.5, f"New should be 0.5, got {rows[1]['confidence']}"
        print("PASS: Different-phase match: both at 0.5, old_id returned for reinforce")
    finally:
        profiler.close()


def test_query_filtering():
    """Test 5: Query filters by perspective and sorts correctly."""
    print("\n=== Test 5: Query filtering ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert observations with different perspectives
        for perspective in ["wolf_teammate", "wolf_opponent", "good_teammate", "good_opponent"]:
            profiler._upsert_observation(
                player_role="villager", game_phase="mid",
                behavior_category="speech_pattern",
                observation=f"观察-{perspective}",
                keywords=f"kw_{perspective}",
                game_outcome="player_lost",
                outcome_perspective=perspective,
                source_game_id="game_1",
            )

        # Query as werewolf — should see wolf-relevant perspectives
        wolf_obs = profiler.query_observations(ai_role="werewolf", current_phase="mid")
        wolf_perspectives = {o["outcome_perspective"] for o in wolf_obs}
        assert "wolf_teammate" in wolf_perspectives or "wolf_opponent" in wolf_perspectives
        print(f"PASS: Werewolf sees perspectives: {wolf_perspectives}")

        # Query as seer — should see good-relevant perspectives
        seer_obs = profiler.query_observations(ai_role="seer", current_phase="mid")
        seer_perspectives = {o["outcome_perspective"] for o in seer_obs}
        assert "good_teammate" in seer_perspectives or "good_opponent" in seer_perspectives
        print(f"PASS: Seer sees perspectives: {seer_perspectives}")
    finally:
        profiler.close()


def test_prompt_formatting():
    """Test 6: format_for_prompt produces expected markdown."""
    print("\n=== Test 6: Prompt formatting ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        observations = [
            {
                "player_role": "werewolf",
                "game_phase": "early",
                "behavior_category": "silence_pattern",
                "observation": "第一轮倾向沉默观察",
                "outcome_perspective": "wolf_learn",
                "confidence": 0.8,
                "match_count": 3,
            },
            {
                "player_role": "seer",
                "game_phase": "mid",
                "behavior_category": "identity_claim",
                "observation": "第二轮会主动跳预言家身份",
                "outcome_perspective": "good_counter",
                "confidence": 0.5,
                "match_count": 1,
            },
        ]

        result = profiler.format_for_prompt(observations, "werewolf")

        assert "对旅行者的行为画像" in result
        assert "当旅行者是狼人时" in result
        assert "当旅行者是预言家时" in result
        assert "[高确信]" in result
        assert "[中确信]" in result
        assert "沉默观察" in result
        print("PASS: Prompt formatting correct")
        print(f"  Output preview: {result[:200]}...")
    finally:
        profiler.close()


def test_confidence_decay():
    """Test 7: Confidence decays for unreinforced observations."""
    print("\n=== Test 7: Confidence decay ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert two observations from different games
        profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="voting_pattern",
            observation="投票时倾向跟随多数人的选择",
            keywords="跟票,从众",
            game_outcome="player_won",
            outcome_perspective="good_counter",
            source_game_id="game_old",
        )
        profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="speech_pattern",
            observation="中期发言积极",
            keywords="积极,发言",
            game_outcome="player_won",
            outcome_perspective="good_counter",
            source_game_id="game_new",
        )

        # Decay unreinforced (except game_new)
        decayed = profiler.decay_unreinforced("game_new")
        assert decayed == 1, f"Expected 1 decayed, got {decayed}"

        conn = profiler._get_conn()
        old_row = conn.execute(
            "SELECT confidence FROM player_observations WHERE source_game_id = 'game_old'"
        ).fetchone()
        new_row = conn.execute(
            "SELECT confidence FROM player_observations WHERE source_game_id = 'game_new'"
        ).fetchone()

        assert old_row["confidence"] == 0.48, f"Old should be 0.48, got {old_row['confidence']}"
        assert new_row["confidence"] == 0.5, f"New should stay 0.5, got {new_row['confidence']}"
        print("PASS: Decay works correctly")
    finally:
        profiler.close()


def test_prune_stale():
    """Test 8: Pruning removes low-confidence observations."""
    print("\n=== Test 8: Prune stale ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        conn = profiler._get_conn()
        now = "2026-01-01T00:00:00"

        # Insert observations with varying confidence
        for i, conf in enumerate([0.1, 0.14, 0.15, 0.5, 0.9]):
            conn.execute(
                """INSERT INTO player_observations
                   (player_role, game_phase, behavior_category, observation, keywords,
                    game_outcome, outcome_perspective, confidence, match_count,
                    created_at, updated_at, source_game_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                ("villager", "mid", "speech_pattern", f"obs_{i}", f"kw_{i}",
                 "player_won", "good_counter", conf, now, now, f"game_{i}"),
            )
        conn.commit()

        pruned = profiler.prune_stale()
        assert pruned == 2, f"Expected 2 pruned (0.1 and 0.14), got {pruned}"

        remaining = conn.execute("SELECT COUNT(*) FROM player_observations").fetchone()[0]
        assert remaining == 3, f"Expected 3 remaining, got {remaining}"
        print("PASS: Pruned 2 stale observations, 3 remaining")
    finally:
        profiler.close()


def test_game_directory_cleanup():
    """Test 9: Old game directories are cleaned up."""
    print("\n=== Test 9: Game directory cleanup ===")
    setup()

    # Create 15 game directories with different mtimes
    for i in range(15):
        d = MEMORY_BASE / f"game_{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        time.sleep(0.01)

    # Also create _cross_game (should not be counted/deleted)
    (MEMORY_BASE / "_cross_game").mkdir(parents=True, exist_ok=True)

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        removed = profiler.cleanup_old_games()
        assert removed == 5, f"Expected 5 removed (15 - 10), got {removed}"

        # Count remaining game dirs (excluding _cross_game)
        remaining = [d for d in MEMORY_BASE.iterdir()
                     if d.is_dir() and d.name != "_cross_game"]
        assert len(remaining) == 10, f"Expected 10 remaining, got {len(remaining)}"
        print("PASS: Cleaned up 5 old game directories, 10 remaining")
    finally:
        profiler.close()


def test_full_lifecycle():
    """Test 10: Complete lifecycle: distill → query → format → decay → prune."""
    print("\n=== Test 10: Full lifecycle ===")
    setup()

    game_state = _make_mock_game_state(
        game_id="lifecycle_game",
        human_role="seer",
        winner="good",
        speeches=[{"player": "旅行者", "content": "我验了胡桃是狼人"}],
    )

    # Create some game records
    game_dir = MEMORY_BASE / "lifecycle_game"
    day_dir = game_dir / "day_record_shared"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "round_1.md").write_text(
        "# 第1轮\n## 发言\n**旅行者**: \"先听听大家的意见\"\n## 投票\n旅行者 → 胡桃",
        encoding="utf-8",
    )

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Step 1: Distill
        result = profiler.distill_game("lifecycle_game", game_state, _mock_llm_call)
        count = result["count"]
        assert count > 0, f"Expected observations, got {count}"
        assert isinstance(result["matched_ids"], set)
        print(f"  Step 1: Distilled {count} observations, matched_ids={result['matched_ids']}")

        # Step 2: Query
        observations = profiler.query_observations(
            ai_role="werewolf", current_phase="early", limit=5
        )
        print(f"  Step 2: Queried {len(observations)} observations for werewolf")

        # Step 3: Format
        formatted = profiler.format_for_prompt(observations, "werewolf")
        if formatted:
            assert "对旅行者的行为画像" in formatted
            print(f"  Step 3: Formatted prompt ({len(formatted)} chars)")
        else:
            print("  Step 3: No observations for werewolf perspective (expected for good-won seer)")

        # Step 4: Decay
        decayed = profiler.decay_unreinforced("lifecycle_game")
        print(f"  Step 4: Decayed {decayed} observations")

        # Step 5: Prune
        pruned = profiler.prune_stale()
        print(f"  Step 5: Pruned {pruned} observations")

        # Step 6: Stats
        stats = profiler.get_stats()
        assert stats["total_games_distilled"] == 1
        print(f"  Step 6: Stats: {stats}")

        print("PASS: Full lifecycle completed successfully")
    finally:
        profiler.close()


def test_perspective_matrix():
    """Test 11: Perspective matrix covers all sides with teammate + opponent."""
    print("\n=== Test 11: Perspective matrix ===")

    all_roles = ["werewolf", "seer", "witch", "hunter", "villager"]

    for role in all_roles:
        side = _ROLE_SIDE[role]
        perspectives = _PERSPECTIVE_MATRIX.get(side)
        assert perspectives, f"Missing perspectives for side={side} (role={role})"
        assert len(perspectives) == 2, f"Expected 2 perspectives, got {len(perspectives)}"
        for p in perspectives:
            assert isinstance(p, str) and len(p) > 0
        print(f"  {role} (side={side}): {perspectives}")

    # Verify wolf-side
    wolf_p = _PERSPECTIVE_MATRIX["wolf"]
    assert "wolf_teammate" in wolf_p, "wolf_teammate missing"
    assert "good_opponent" in wolf_p, "good_opponent missing"

    # Verify good-side
    good_p = _PERSPECTIVE_MATRIX["good"]
    assert "good_teammate" in good_p, "good_teammate missing"
    assert "wolf_opponent" in good_p, "wolf_opponent missing"

    print("PASS: All sides covered with teammate + opponent")


def test_duplicate_distillation_skip():
    """Test: Distilling same game twice is idempotent."""
    print("\n=== Test: Duplicate distillation skip ===")
    setup()

    game_state = _make_mock_game_state(game_id="dup_test", winner="good")
    day_dir = MEMORY_BASE / "dup_test" / "day_record_shared"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "round_1.md").write_text(
        "# 第1轮\n## 发言\n**旅行者**: \"测试\"\n", encoding="utf-8"
    )

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        result1 = profiler.distill_game("dup_test", game_state, _mock_llm_call)
        assert result1["count"] > 0
        result2 = profiler.distill_game("dup_test", game_state, _mock_llm_call)
        assert result2["count"] == 0, f"Second distillation should return 0, got {result2['count']}"
        print("PASS: Duplicate distillation correctly skipped")
    finally:
        profiler.close()


def test_parse_observations_json():
    """Test: JSON parsing handles various formats."""
    print("\n=== Test: JSON parsing ===")

    # Normal JSON
    result = PlayerProfiler._parse_observations_json(
        '[{"observation": "test", "game_phase": "early"}]'
    )
    assert len(result) == 1

    # With markdown fences
    result = PlayerProfiler._parse_observations_json(
        '```json\n[{"observation": "test"}]\n```'
    )
    assert len(result) == 1

    # With extra text
    result = PlayerProfiler._parse_observations_json(
        'Here are the observations:\n[{"observation": "test"}]'
    )
    assert len(result) == 1

    # Invalid JSON
    result = PlayerProfiler._parse_observations_json("not json at all")
    assert len(result) == 0

    # Empty observation filtered out
    result = PlayerProfiler._parse_observations_json('[{"no_obs_key": "test"}]')
    assert len(result) == 0

    print("PASS: JSON parsing handles all formats correctly")


def test_perspectives_are_differentiated():
    """Test: Different perspectives produce different observations."""
    print("\n=== Test: Perspective differentiation ===")
    setup()

    game_state = _make_mock_game_state(
        game_id="persp_test",
        human_role="seer",   # good side → ["good_teammate", "wolf_opponent"]
        winner="good",
        speeches=[{"player": "旅行者", "content": "我验了胡桃是狼人"}],
    )

    day_dir = MEMORY_BASE / "persp_test" / "day_record_shared"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "round_1.md").write_text(
        "# 第1轮\n## 发言\n**旅行者**: \"我验了胡桃\"\n", encoding="utf-8"
    )

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        result = profiler.distill_game("persp_test", game_state, _mock_llm_call)
        count = result["count"]
        assert count > 0, f"Expected observations, got {count}"

        conn = profiler._get_conn()
        rows = conn.execute(
            "SELECT outcome_perspective, observation FROM player_observations ORDER BY id"
        ).fetchall()

        # Group by perspective
        by_persp: dict[str, list[str]] = {}
        for r in rows:
            p = r["outcome_perspective"]
            by_persp.setdefault(p, []).append(r["observation"])

        # Must have both perspectives (teammate + opponent)
        assert "good_teammate" in by_persp, f"Missing good_teammate, got {list(by_persp.keys())}"
        assert "wolf_opponent" in by_persp, f"Missing wolf_opponent, got {list(by_persp.keys())}"

        # Observations under different perspectives must NOT be identical
        gt_obs = set(by_persp["good_teammate"])
        wo_obs = set(by_persp["wolf_opponent"])
        assert gt_obs != wo_obs, (
            "good_teammate and wolf_opponent have identical observations — "
            "perspectives should produce different analyses"
        )

        print(f"PASS: {len(gt_obs)} good_teammate obs, {len(wo_obs)} wolf_opponent obs, all different")
        for p, obs_list in by_persp.items():
            for o in obs_list:
                print(f"  [{p}] {o[:60]}...")
    finally:
        profiler.close()


def test_served_tracking():
    """Test: query_observations with game_id tracks served observations."""
    print("\n=== Test: Served observation tracking ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert observations
        for i, perspective in enumerate(["good_teammate", "good_opponent"]):
            profiler._upsert_observation(
                player_role="villager", game_phase="mid",
                behavior_category="speech_pattern",
                observation=f"观察_{perspective}_{i}",
                keywords=f"kw_{perspective}_{i}",
                game_outcome="player_won",
                outcome_perspective=perspective,
                source_game_id="old_game",
            )

        # Query with game_id should track served
        obs = profiler.query_observations(
            ai_role="seer", current_phase="mid", game_id="current_game"
        )
        assert len(obs) > 0, "Expected observations"

        # Check served_observations table
        conn = profiler._get_conn()
        served = conn.execute(
            "SELECT * FROM served_observations WHERE game_id = 'current_game'"
        ).fetchall()
        assert len(served) == len(obs), f"Expected {len(obs)} served, got {len(served)}"
        print(f"PASS: Tracked {len(served)} served observations")

        # Query without game_id should NOT track
        obs2 = profiler.query_observations(ai_role="seer", current_phase="mid")
        served2 = conn.execute("SELECT COUNT(*) FROM served_observations").fetchone()[0]
        assert served2 == len(served), "No new served records without game_id"
        print("PASS: No tracking without game_id")
    finally:
        profiler.close()


def test_reinforce_reward():
    """Test: reinforce() rewards observations consumed by the winning side (served_observations fallback)."""
    print("\n=== Test: RL reinforce (reward via served) ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert a good_teammate observation
        _, obs_id = profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="speech_pattern",
            observation="好人队友观察",
            keywords="good,teammate",
            game_outcome="player_won",
            outcome_perspective="good_teammate",
            source_game_id="old_game",
        )

        # Simulate serving it to current game
        conn = profiler._get_conn()
        conn.execute(
            "INSERT INTO served_observations (game_id, observation_id) VALUES (?, ?)",
            ("rl_game", obs_id),
        )
        conn.commit()

        # Good side wins → good_teammate observation should be rewarded (fallback path)
        result = profiler.reinforce("rl_game", "good", "villager")
        assert result["reinforced"] == 1, f"Expected 1 reinforced, got {result}"
        assert result["penalized"] == 0
        assert obs_id in result["adjusted_ids"]

        row = conn.execute("SELECT confidence FROM player_observations WHERE id = ?", (obs_id,)).fetchone()
        assert row["confidence"] == 0.6, f"Expected 0.6, got {row['confidence']}"
        print(f"PASS: Rewarded (served fallback): confidence 0.5 → {row['confidence']}")
    finally:
        profiler.close()


def test_reinforce_penalty():
    """Test: reinforce() penalizes observations consumed by the losing side."""
    print("\n=== Test: RL reinforce (penalty via served) ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert a good_teammate observation
        _, obs_id = profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="speech_pattern",
            observation="好人队友建议",
            keywords="good,suggestion",
            game_outcome="player_won",
            outcome_perspective="good_teammate",
            source_game_id="old_game",
        )

        conn = profiler._get_conn()
        conn.execute(
            "INSERT INTO served_observations (game_id, observation_id) VALUES (?, ?)",
            ("rl_game", obs_id),
        )
        conn.commit()

        # Werewolf wins → good_teammate observation should be penalized
        result = profiler.reinforce("rl_game", "werewolf", "villager")
        assert result["reinforced"] == 0
        assert result["penalized"] == 1, f"Expected 1 penalized, got {result}"
        assert obs_id in result["adjusted_ids"]

        row = conn.execute("SELECT confidence FROM player_observations WHERE id = ?", (obs_id,)).fetchone()
        assert row["confidence"] == 0.35, f"Expected 0.35, got {row['confidence']}"
        print(f"PASS: Penalized (served fallback): confidence 0.5 → {row['confidence']}")
    finally:
        profiler.close()


def test_reinforce_mixed():
    """Test: reinforce() correctly handles mixed perspectives in one game."""
    print("\n=== Test: RL reinforce (mixed) ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert observations for both sides
        profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="speech_pattern",
            observation="好人队友观察",
            keywords="good,obs",
            game_outcome="player_won",
            outcome_perspective="good_teammate",
            source_game_id="old_game",
        )
        profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="voting_pattern",
            observation="狼人队友观察",
            keywords="wolf,obs",
            game_outcome="player_lost",
            outcome_perspective="wolf_teammate",
            source_game_id="old_game",
        )

        conn = profiler._get_conn()
        rows = conn.execute("SELECT id, outcome_perspective FROM player_observations ORDER BY id").fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO served_observations (game_id, observation_id) VALUES (?, ?)",
                ("rl_game", row["id"]),
            )
        conn.commit()

        # Good side wins — pass human_role="villager" so both observations match
        result = profiler.reinforce("rl_game", "good", "villager")
        assert result["reinforced"] == 1, f"Expected 1 reinforced, got {result}"
        assert result["penalized"] == 1, f"Expected 1 penalized, got {result}"
        assert len(result["adjusted_ids"]) == 2

        confs = conn.execute(
            "SELECT outcome_perspective, confidence FROM player_observations ORDER BY id"
        ).fetchall()
        good_conf = confs[0]["confidence"]
        wolf_conf = confs[1]["confidence"]
        assert good_conf == 0.6, f"good_teammate should be 0.6, got {good_conf}"
        assert wolf_conf == 0.35, f"wolf_teammate should be 0.35, got {wolf_conf}"
        print(f"PASS: Mixed: good_teammate→{good_conf}, wolf_teammate→{wolf_conf}")
    finally:
        profiler.close()


def test_reinforce_no_served():
    """Test: reinforce() with no served observations returns zeros."""
    print("\n=== Test: RL reinforce (no served) ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        result = profiler.reinforce("nonexistent_game", "good", "villager")
        assert result["reinforced"] == 0
        assert result["penalized"] == 0
        assert result["adjusted_ids"] == set()
        print("PASS: No served observations → no changes")
    finally:
        profiler.close()


def test_reinforce_with_matched_ids():
    """Test: reinforce() with matched_ids queries those IDs directly."""
    print("\n=== Test: RL reinforce (matched_ids path) ===")
    setup()

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Insert two observations
        _, id1 = profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="speech_pattern",
            observation="好人队友观察matched",
            keywords="good,matched",
            game_outcome="player_won",
            outcome_perspective="good_teammate",
            source_game_id="old_game",
        )
        _, id2 = profiler._upsert_observation(
            player_role="villager", game_phase="mid",
            behavior_category="voting_pattern",
            observation="投票模式观察not_matched",
            keywords="vote,pattern",
            game_outcome="player_won",
            outcome_perspective="good_teammate",
            source_game_id="old_game",
        )

        # Only pass id1 as matched — id2 should not be touched
        result = profiler.reinforce("rl_game", "good", "villager", matched_ids={id1})
        assert result["reinforced"] == 1
        assert result["penalized"] == 0
        assert id1 in result["adjusted_ids"]
        assert id2 not in result["adjusted_ids"]

        conn = profiler._get_conn()
        row1 = conn.execute("SELECT confidence FROM player_observations WHERE id = ?", (id1,)).fetchone()
        row2 = conn.execute("SELECT confidence FROM player_observations WHERE id = ?", (id2,)).fetchone()
        assert row1["confidence"] == 0.6, f"Matched should be 0.6, got {row1['confidence']}"
        assert row2["confidence"] == 0.5, f"Unmatched should stay 0.5, got {row2['confidence']}"
        print(f"PASS: matched_ids path: id1→0.6, id2→0.5 (untouched)")
    finally:
        profiler.close()


def test_full_new_flow():
    """Test: Full new flow: distill → reinforce(matched) → decay → prune."""
    print("\n=== Test: Full new flow (distill → reinforce → decay → prune) ===")
    setup()

    # First game: insert initial observations
    game1 = _make_mock_game_state(
        game_id="flow_game_1",
        human_role="villager",
        winner="good",
        speeches=[{"player": "旅行者", "content": "我觉得胡桃可疑"}],
    )
    day_dir = MEMORY_BASE / "flow_game_1" / "day_record_shared"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "round_1.md").write_text(
        "# 第1轮\n## 发言\n**旅行者**: \"先观察\"\n", encoding="utf-8"
    )

    profiler = PlayerProfiler(MEMORY_BASE)
    try:
        # Game 1: distill (all new inserts)
        r1 = profiler.distill_game("flow_game_1", game1, _mock_llm_call)
        assert r1["count"] > 0
        assert len(r1["matched_ids"]) == 0, "First game should have no matches"

        # Game 2: same human role, similar observations → should match
        game2 = _make_mock_game_state(
            game_id="flow_game_2",
            human_role="villager",
            winner="werewolf",
            speeches=[{"player": "旅行者", "content": "我还是先听听"}],
        )
        day_dir2 = MEMORY_BASE / "flow_game_2" / "day_record_shared"
        day_dir2.mkdir(parents=True, exist_ok=True)
        (day_dir2 / "round_1.md").write_text(
            "# 第1轮\n## 发言\n**旅行者**: \"先听听\"\n", encoding="utf-8"
        )

        r2 = profiler.distill_game("flow_game_2", game2, _mock_llm_call)
        assert r2["count"] > 0

        # Reinforce matched observations
        if r2["matched_ids"]:
            rl = profiler.reinforce("flow_game_2", "werewolf", "villager", matched_ids=r2["matched_ids"])
            # good_teammate observations should be penalized (werewolf won)
            assert rl["penalized"] > 0 or rl["reinforced"] > 0
            adjusted = rl["adjusted_ids"]
        else:
            adjusted = set()

        # Decay unreinforced (old observations not matched/reinforced)
        decayed = profiler.decay_unreinforced("flow_game_2", adjusted)

        # Prune
        pruned = profiler.prune_stale()

        stats = profiler.get_stats()
        print(f"  Game 1: {r1['count']} obs, 0 matched")
        print(f"  Game 2: {r2['count']} obs, {len(r2['matched_ids'])} matched")
        print(f"  Reinforced/penalized: {adjusted}, decayed: {decayed}, pruned: {pruned}")
        print(f"  Final stats: {stats}")
        print("PASS: Full new flow completed")
    finally:
        profiler.close()


def cleanup():
    """Clean up test directory."""
    if MEMORY_BASE.exists():
        shutil.rmtree(MEMORY_BASE)


if __name__ == "__main__":
    try:
        test_database_creation()
        test_distill_extraction()
        test_incremental_update_reinforce()
        test_incremental_update_contradiction()
        test_query_filtering()
        test_prompt_formatting()
        test_confidence_decay()
        test_prune_stale()
        test_game_directory_cleanup()
        test_full_lifecycle()
        test_perspective_matrix()
        test_duplicate_distillation_skip()
        test_parse_observations_json()
        test_perspectives_are_differentiated()
        test_served_tracking()
        test_reinforce_reward()
        test_reinforce_penalty()
        test_reinforce_mixed()
        test_reinforce_no_served()
        test_reinforce_with_matched_ids()
        test_full_new_flow()
        print("\n=== All PlayerProfiler tests passed! ===")
    finally:
        cleanup()
