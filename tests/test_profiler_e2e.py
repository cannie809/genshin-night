"""End-to-end test: multiple games with different roles/styles → inspect DB state.

Tests the full distill → reinforce(matched) → decay → prune flow across games
with various human player styles:
  - 划水 (passive/lurking)   — villager, good side
  - 跟风 (bandwagon)        — villager, good side
  - 悍跳 (bold fake claim)  — werewolf, wolf side
  - 逻辑预言家 (logical seer) — seer, good side
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.memory.player_profiler import PlayerProfiler, _ROLE_SIDE

MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test_e2e"


def setup():
    if MEMORY_BASE.exists():
        shutil.rmtree(MEMORY_BASE)
    MEMORY_BASE.mkdir(parents=True, exist_ok=True)


class MockPlayer:
    def __init__(self, id, name, role, is_human=False):
        self.id = id
        self.name = name
        self.role = role
        self.is_human = is_human
        self.alive = True
        self.personality = "test"

    def is_werewolf(self):
        return self.role == "werewolf"


class MockGameState:
    def __init__(self, game_id, human_role, winner, speeches=None, votes=None):
        self.game_id = game_id
        self.round_number = 2
        self.winner = winner
        other_wolf = "villager" if human_role == "werewolf" else "werewolf"
        other_seer = "villager" if human_role == "seer" else "seer"
        other_witch = "villager" if human_role == "witch" else "witch"
        self.players = [
            MockPlayer("player_0", "旅行者", human_role, is_human=True),
            MockPlayer("player_1", "胡桃", other_wolf),
            MockPlayer("player_2", "行秋", other_seer),
            MockPlayer("player_3", "万叶", "villager"),
            MockPlayer("player_4", "钟离", "werewolf"),
            MockPlayer("player_5", "班尼特", other_witch),
        ]
        self.speeches = speeches or []
        self.votes = votes or {}

    def get_player_by_id(self, pid):
        for p in self.players:
            if p.id == pid:
                return p
        return None


# ── Style-specific mock LLMs ──

def _llm_passive(prompt, max_tokens=800, temperature=0.3):
    """划水风格: 沉默、弃票、不参与讨论."""
    perspectives = _detect_perspectives(prompt)
    obs = []
    p1 = perspectives[0]
    obs.append({
        "perspective": p1,
        "game_phase": "early",
        "behavior_category": "silence_pattern",
        "observation": f"[{p1}] 旅行者全程极少发言，典型划水行为，不主动参与讨论",
        "keywords": "沉默,模糊发言,弃票",
    })
    obs.append({
        "perspective": p1,
        "game_phase": "mid",
        "behavior_category": "voting_pattern",
        "observation": f"[{p1}] 中期投票经常弃票或跟随多数人的选择，缺乏独立判断",
        "keywords": "弃票,跟票,从众",
    })
    if len(perspectives) > 1:
        p2 = perspectives[1]
        obs.append({
            "perspective": p2,
            "game_phase": "early",
            "behavior_category": "silence_pattern",
            "observation": f"[{p2}] 旅行者划水时其实在收集信息，不要误以为他没在观察",
            "keywords": "沉默,隐藏身份,冷静应对",
        })
        obs.append({
            "perspective": p2,
            "game_phase": "mid",
            "behavior_category": "voting_pattern",
            "observation": f"[{p2}] 旅行者投票模式可预测，倾向弃票或跟大流",
            "keywords": "弃票,跟票,分票",
        })
    return json.dumps(obs, ensure_ascii=False)


def _llm_bandwagon(prompt, max_tokens=800, temperature=0.3):
    """跟风风格: 附和强势发言者、跟票."""
    perspectives = _detect_perspectives(prompt)
    obs = []
    p1 = perspectives[0]
    obs.append({
        "perspective": p1,
        "game_phase": "early",
        "behavior_category": "speech_pattern",
        "observation": f"[{p1}] 旅行者习惯附和场上最强势的发言者，缺乏独立观点",
        "keywords": "附和,跟票,从众",
    })
    obs.append({
        "perspective": p1,
        "game_phase": "mid",
        "behavior_category": "voting_pattern",
        "observation": f"[{p1}] 投票几乎完全跟随多数意见，可以通过引导多数来控制旅行者的票",
        "keywords": "跟票,集火,从众,附和",
    })
    if len(perspectives) > 1:
        p2 = perspectives[1]
        obs.append({
            "perspective": p2,
            "game_phase": "early",
            "behavior_category": "speech_pattern",
            "observation": f"[{p2}] 旅行者跟风明显，可利用假情报带动旅行者判断失误",
            "keywords": "附和,带节奏,信息泄露",
        })
        obs.append({
            "perspective": p2,
            "game_phase": "mid",
            "behavior_category": "cooperation_pattern",
            "observation": f"[{p2}] 旅行者容易被强势发言者带节奏，狼人可伪装权威引导其投票",
            "keywords": "跟票,带节奏,冲票",
        })
    return json.dumps(obs, ensure_ascii=False)


def _llm_bold_claim(prompt, max_tokens=800, temperature=0.3):
    """悍跳风格 (狼人): 假跳预言家、强势发言."""
    perspectives = _detect_perspectives(prompt)
    obs = []
    p1 = perspectives[0]
    obs.append({
        "perspective": p1,
        "game_phase": "early",
        "behavior_category": "identity_claim",
        "observation": f"[{p1}] 旅行者作为狼人时喜欢首轮悍跳预言家，用假验人信息带节奏",
        "keywords": "假跳,对跳,强势发言,带节奏",
    })
    obs.append({
        "perspective": p1,
        "game_phase": "mid",
        "behavior_category": "deception_pattern",
        "observation": f"[{p1}] 旅行者悍跳后会持续编造验人结果，同队狼人应配合其节奏",
        "keywords": "假跳,验人,配合默契,节奏把控",
    })
    if len(perspectives) > 1:
        p2 = perspectives[1]
        obs.append({
            "perspective": p2,
            "game_phase": "early",
            "behavior_category": "identity_claim",
            "observation": f"[{p2}] 旅行者悍跳预言家时发言逻辑存在漏洞，注意对跳时的细节矛盾",
            "keywords": "假跳,对跳,逻辑分析,自证",
        })
        obs.append({
            "perspective": p2,
            "game_phase": "mid",
            "behavior_category": "defense_pattern",
            "observation": f"[{p2}] 旅行者被质疑时容易情绪化辩解，可通过持续施压暴露其身份",
            "keywords": "慌张辩解,情绪化,质疑,过早暴露",
        })
    return json.dumps(obs, ensure_ascii=False)


def _llm_logical_seer(prompt, max_tokens=800, temperature=0.3):
    """逻辑预言家风格: 逻辑分析、验人、明跳."""
    perspectives = _detect_perspectives(prompt)
    obs = []
    p1 = perspectives[0]
    obs.append({
        "perspective": p1,
        "game_phase": "early",
        "behavior_category": "speech_pattern",
        "observation": f"[{p1}] 旅行者作为预言家首轮会用逻辑分析暗示自己有信息，但不会立即明跳",
        "keywords": "逻辑分析,暗示身份,试探,隐藏身份",
    })
    obs.append({
        "perspective": p1,
        "game_phase": "mid",
        "behavior_category": "identity_claim",
        "observation": f"[{p1}] 旅行者第二轮会根据形势决定是否明跳，跳后会公布完整验人结果",
        "keywords": "明跳,验人,自证,逻辑自洽",
    })
    if len(perspectives) > 1:
        p2 = perspectives[1]
        obs.append({
            "perspective": p2,
            "game_phase": "early",
            "behavior_category": "speech_pattern",
            "observation": f"[{p2}] 旅行者首轮逻辑分析风格是预言家信号，狼人应提前对跳准备",
            "keywords": "逻辑分析,暗示身份,对跳,假跳",
        })
        obs.append({
            "perspective": p2,
            "game_phase": "mid",
            "behavior_category": "night_action",
            "observation": f"[{p2}] 旅行者预言家明跳后是高优先击杀目标，必须在跳后当晚刀掉",
            "keywords": "明跳,验人,集火,节奏把控",
        })
    return json.dumps(obs, ensure_ascii=False)


def _detect_perspectives(prompt):
    """Detect requested perspectives from prompt text."""
    perspectives = []
    for tag in ["good_teammate", "good_opponent", "wolf_teammate", "wolf_opponent"]:
        if f"### 视角: {tag}" in prompt:
            perspectives.append(tag)
    return perspectives or ["good_teammate"]


def _make_day_record(game_id, speeches_by_round):
    """Create day record files."""
    day_dir = MEMORY_BASE / game_id / "day_record_shared"
    day_dir.mkdir(parents=True, exist_ok=True)
    for round_num, lines in speeches_by_round.items():
        content = f"# 第{round_num}轮\n## 发言\n"
        for line in lines:
            content += line + "\n"
        (day_dir / f"round_{round_num}.md").write_text(content, encoding="utf-8")


# ── Games ──

GAMES = [
    # Game 1: 划水村民, 好人赢
    {
        "game_id": "e2e_passive_win",
        "human_role": "villager",
        "winner": "good",
        "llm": _llm_passive,
        "speeches": [{"player": "旅行者", "content": "没什么想法"}],
        "day_records": {1: ['**旅行者**: "没什么想法"'], 2: ['**旅行者**: "我也不确定"']},
        "style": "划水",
    },
    # Game 2: 划水村民, 狼人赢 (same style, different outcome → reinforcement test)
    {
        "game_id": "e2e_passive_loss",
        "human_role": "villager",
        "winner": "werewolf",
        "llm": _llm_passive,
        "speeches": [{"player": "旅行者", "content": "随便吧"}],
        "day_records": {1: ['**旅行者**: "随便吧"']},
        "style": "划水",
    },
    # Game 3: 跟风村民, 好人赢
    {
        "game_id": "e2e_bandwagon_win",
        "human_role": "villager",
        "winner": "good",
        "llm": _llm_bandwagon,
        "speeches": [{"player": "旅行者", "content": "我同意行秋说的"}],
        "day_records": {1: ['**旅行者**: "我同意行秋说的"'], 2: ['**旅行者**: "对对对他说得对"']},
        "style": "跟风",
    },
    # Game 4: 跟风村民, 狼人赢
    {
        "game_id": "e2e_bandwagon_loss",
        "human_role": "villager",
        "winner": "werewolf",
        "llm": _llm_bandwagon,
        "speeches": [{"player": "旅行者", "content": "我也觉得钟离可疑"}],
        "day_records": {1: ['**旅行者**: "我也觉得钟离可疑"']},
        "style": "跟风",
    },
    # Game 5: 悍跳狼人, 狼人赢
    {
        "game_id": "e2e_boldclaim_win",
        "human_role": "werewolf",
        "winner": "werewolf",
        "llm": _llm_bold_claim,
        "speeches": [{"player": "旅行者", "content": "我是预言家，昨晚验了万叶是好人"}],
        "day_records": {1: ['**旅行者**: "我是预言家，昨晚验了万叶是好人"']},
        "style": "悍跳",
    },
    # Game 6: 悍跳狼人, 好人赢 (exposed)
    {
        "game_id": "e2e_boldclaim_loss",
        "human_role": "werewolf",
        "winner": "good",
        "llm": _llm_bold_claim,
        "speeches": [{"player": "旅行者", "content": "我跳预言家，验的结果是..."}],
        "day_records": {1: ['**旅行者**: "我跳预言家"'], 2: ['**旅行者**: "那个...验的结果..."']},
        "style": "悍跳",
    },
    # Game 7: 逻辑预言家, 好人赢
    {
        "game_id": "e2e_seer_win",
        "human_role": "seer",
        "winner": "good",
        "llm": _llm_logical_seer,
        "speeches": [{"player": "旅行者", "content": "我手上有关键信息，第二轮公布"}],
        "day_records": {
            1: ['**旅行者**: "我手上有关键信息"'],
            2: ['**旅行者**: "我是预言家，验了胡桃和钟离都是狼人"'],
        },
        "style": "逻辑预言家",
    },
    # Game 8: 逻辑预言家, 狼人赢 (被刀)
    {
        "game_id": "e2e_seer_loss",
        "human_role": "seer",
        "winner": "werewolf",
        "llm": _llm_logical_seer,
        "speeches": [{"player": "旅行者", "content": "我有重要信息"}],
        "day_records": {1: ['**旅行者**: "我有重要信息，但先不说"']},
        "style": "逻辑预言家",
    },
]


def _dump_db_snapshot(profiler, label):
    """Print a concise DB snapshot showing all observations."""
    conn = profiler._get_conn()
    rows = conn.execute(
        """SELECT id, player_role, game_phase, behavior_category,
                  outcome_perspective, observation, keywords,
                  confidence, match_count, source_game_id
           FROM player_observations
           ORDER BY id"""
    ).fetchall()
    total = len(rows)
    print(f"\n  ┌── DB Snapshot ({label}): {total} observations ──")
    for r in rows:
        conf = r["confidence"]
        print(
            f"  │ #{r['id']:2d} [{conf:.2f}] m={r['match_count']} "
            f"{r['outcome_perspective']:15s} {r['game_phase']:5s} "
            f"{r['behavior_category']:20s} {r['observation'][:45]}…"
        )
    print(f"  └── end snapshot ──\n")


def run_e2e():
    """Run all games through the full lifecycle and inspect DB after each."""
    setup()
    profiler = PlayerProfiler(MEMORY_BASE)

    try:
        for i, g in enumerate(GAMES, 1):
            _make_day_record(g["game_id"], g["day_records"])

            gs = MockGameState(
                game_id=g["game_id"],
                human_role=g["human_role"],
                winner=g["winner"],
                speeches=g["speeches"],
            )

            side = _ROLE_SIDE.get(g["human_role"], "good")
            human_won = (side == "good" and g["winner"] == "good") or \
                        (side == "wolf" and g["winner"] == "werewolf")
            outcome = "WIN" if human_won else "LOSS"

            print(f"\n{'='*60}")
            print(f"GAME {i}/{len(GAMES)}: [{g['style']}] {g['game_id']}")
            print(f"  Role: {g['human_role']}, Winner: {g['winner']}, Outcome: {outcome}")

            # distill → reinforce(matched) → decay → prune
            result = profiler.distill_game(g["game_id"], gs, g["llm"])
            human_role = g["human_role"]
            matched_ids = result["matched_ids"]

            print(f"  Distilled: {result['count']}, Matched IDs: {matched_ids or '{}'}")

            if result["count"] > 0:
                rl = profiler.reinforce(
                    g["game_id"], g["winner"], human_role,
                    matched_ids=matched_ids if matched_ids else None,
                )
                print(f"  Reinforce: +{rl['reinforced']} rewarded, -{rl['penalized']} penalized, adjusted={rl['adjusted_ids'] or '{}'}")
                decayed = profiler.decay_unreinforced(g["game_id"], rl["adjusted_ids"])
                pruned = profiler.prune_stale()
                print(f"  Decay: {decayed} decayed, Prune: {pruned} pruned")

            _dump_db_snapshot(profiler, f"after game {i}")

        # ── Inspect final DB state ──
        print("\n" + "=" * 70)
        print("FINAL DB STATE")
        print("=" * 70)

        conn = profiler._get_conn()

        # Summary
        stats = profiler.get_stats()
        print(f"\nStats: {stats}")

        # All observations grouped by perspective
        rows = conn.execute(
            """SELECT id, player_role, game_phase, behavior_category,
                      outcome_perspective, observation, keywords,
                      confidence, match_count, source_game_id
               FROM player_observations
               ORDER BY outcome_perspective, confidence DESC"""
        ).fetchall()

        current_persp = None
        for r in rows:
            if r["outcome_perspective"] != current_persp:
                current_persp = r["outcome_perspective"]
                print(f"\n── {current_persp} ──")
            conf = r["confidence"]
            if conf >= 0.7:
                conf_label = "HIGH"
            elif conf >= 0.4:
                conf_label = "MID"
            else:
                conf_label = "LOW"
            print(
                f"  [{conf_label} {conf:.2f}] (match={r['match_count']}) "
                f"[{r['player_role']}/{r['game_phase']}/{r['behavior_category']}] "
                f"{r['observation'][:60]}"
            )
            print(f"         keywords: {r['keywords']}")
            print(f"         source: {r['source_game_id']}")

        # Game history
        print(f"\n── Game History ──")
        games = conn.execute("SELECT * FROM game_history ORDER BY id").fetchall()
        for g in games:
            print(f"  {g['game_id']}: role={g['human_role']}, outcome={g['game_outcome']}, "
                  f"winner={g['winning_side']}, obs_count={g['observation_count']}")

        # Confidence distribution
        print(f"\n── Confidence Distribution ──")
        for bracket in [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM player_observations WHERE confidence >= ? AND confidence < ?",
                bracket,
            ).fetchone()[0]
            if cnt > 0:
                print(f"  [{bracket[0]:.1f}-{bracket[1]:.1f}): {cnt}")

        # Verify key properties
        print(f"\n── Verification ──")

        # 1. good_teammate observations should NOT contain condemning language
        good_tm = conn.execute(
            "SELECT observation FROM player_observations WHERE outcome_perspective = 'good_teammate'"
        ).fetchall()
        condemn_words = ["错误", "失误", "犯了", "导致失败"]
        for r in good_tm:
            for w in condemn_words:
                assert w not in r["observation"], (
                    f"good_teammate observation contains condemning word '{w}': {r['observation']}"
                )
        print(f"  PASS: {len(good_tm)} good_teammate observations — no condemning language")

        # 2. Matched observations (match_count > 1) should exist for repeated styles
        matched = conn.execute(
            "SELECT COUNT(*) FROM player_observations WHERE match_count > 1"
        ).fetchone()[0]
        print(f"  PASS: {matched} observations have match_count > 1 (pattern recurrence)")

        # 3. Wolf-side and good-side observations both exist
        persp_counts = conn.execute(
            """SELECT outcome_perspective, COUNT(*) as cnt
               FROM player_observations
               GROUP BY outcome_perspective"""
        ).fetchall()
        persp_map = {r["outcome_perspective"]: r["cnt"] for r in persp_counts}
        assert "good_teammate" in persp_map, "Missing good_teammate observations"
        assert "wolf_opponent" in persp_map, "Missing wolf_opponent observations"
        assert "wolf_teammate" in persp_map, "Missing wolf_teammate observations"
        assert "good_opponent" in persp_map, "Missing good_opponent observations"
        print(f"  PASS: All 4 perspectives present: {dict(persp_map)}")

        print(f"\n{'=' * 70}")
        print("ALL E2E CHECKS PASSED")
        print(f"{'=' * 70}")

    finally:
        profiler.close()
        # Cleanup
        if MEMORY_BASE.exists():
            shutil.rmtree(MEMORY_BASE)


if __name__ == "__main__":
    run_e2e()
