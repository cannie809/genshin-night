"""Tests for role-skill context integrity (v2.0.1 bug investigation).

These tests exercise:
  - Bug A: Seer <target> parsing — partial names + ambiguous names + silent
    random fallback.
  - Bug B: Seer → process_night_seer → write_night_records consistency
    (ground-truth and per-round persistence).
  - Bug C: Speech prompt contamination across concurrent generate_speech calls
    (shared game.speeches race between prefetch and /advance-discussion).
  - Sanity: witch poison / werewolf kill / guard protect partial-name fallback
    should NOT be silent.

The tests intentionally bypass `GameEngine.create_game` where we need tight
control over player names (to reproduce the substring-match corner cases),
constructing `GameState` / `Player` directly.

Per instructions: KNOWN currently-failing cases are marked `xfail` so the
suite stays green while the bugs are tracked.
"""

import asyncio
import logging
import random
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.ai import UnifiedGameAgent
from backend.game import GameEngine
from backend.memory import KnowledgeManager, RecordsManager
from backend.memory.storage import MemoryStorage
from backend.models import GamePhase, GameState, Player

MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test_skill_integrity"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_memory_base():
    """Reset the per-suite memory base for deterministic tests."""
    if MEMORY_BASE.exists():
        shutil.rmtree(MEMORY_BASE, ignore_errors=True)
    MEMORY_BASE.mkdir(parents=True, exist_ok=True)


def _make_game(
    player_specs: list[tuple[str, str]],
    *,
    game_id: str,
    phase: GamePhase = GamePhase.NIGHT_SEER,
    round_number: int = 1,
) -> GameState:
    """Build a GameState with exactly the players we want.

    player_specs: list of (name, role) tuples — first entry becomes player_1, etc.
    """
    players = [
        Player(
            id=f"player_{idx + 1}",
            name=name,
            role=role,
            personality="LOGICAL",
            is_human=False,
        )
        for idx, (name, role) in enumerate(player_specs)
    ]
    game = GameState(
        game_id=game_id,
        mode="classic_6_witch",
        phase=phase,
        round_number=round_number,
        players=players,
        memory_base=MEMORY_BASE,
    )
    # Initialize per-player knowledge files so update_knowledge_summary/json work.
    km = KnowledgeManager(MEMORY_BASE, game_id)
    for p in players:
        try:
            km.initialize_player_knowledge(p.id, p.role, game_mode="classic_6_witch")
        except Exception:
            pass
    # Werewolf shared area (used when role=werewolf).
    if any(p.role == "werewolf" for p in players):
        try:
            km.initialize_werewolf_shared()
        except Exception:
            pass
    return game


def _new_agent() -> UnifiedGameAgent:
    return UnifiedGameAgent(MEMORY_BASE, llm_provider="openrouter")


# ---------------------------------------------------------------------------
# Bug A — Seer <target> mis-parse
# ---------------------------------------------------------------------------


def test_seer_target_tag_exact_name_baseline():
    """Sanity baseline: when LLM returns exact name in <target>, seer picks it."""
    _clean_memory_base()
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "villager"),
            ("行秋", "villager"),
        ],
        game_id="test_seer_baseline",
    )
    agent = _new_agent()
    seer = game.players[0]

    response = (
        "<analysis>胡桃嫌疑最大</analysis>\n"
        "<target>胡桃</target>"
    )
    with patch.object(agent, "_llm_call", return_value=response):
        result = agent.seer_investigate(seer, game)

    assert result == "胡桃", f"Expected '胡桃', got {result!r}"


@pytest.mark.xfail(
    reason="Bug A: partial name '空' falls through substring match (not a substring "
    "of '旅行者·空') and silently hits random.choice fallback."
)
def test_seer_target_partial_name_kong_resolves_to_traveler():
    """LLM writes <target>空</target> intending '旅行者·空'.

    Current code: `"旅行者·空".lower()` does NOT contain `"空".lower()` as a
    substring check — wait, it DOES. But the loop iterates `alive_others` and
    checks `player.name.lower() in response.lower()`, i.e. is the PLAYER NAME
    a substring of the RESPONSE. "旅行者·空" is NOT a substring of "空", so
    the check fails for every player → random fallback.
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "villager"),
            ("行秋", "villager"),
            ("迪卢克", "villager"),
        ],
        game_id="test_seer_partial_kong",
    )
    # Put the "intended" player at a known slot so we know what correct looks like.
    # Seer wants to check 胡桃 but writes "<target>胡</target>" — first char only.
    agent = _new_agent()
    seer = game.players[0]

    response = "<target>胡</target>"

    # Lock rng so that the random fallback would pick a DIFFERENT player than
    # 胡桃, so we can tell the difference between a correct match and a
    # fallback pick.
    with patch.object(agent, "_llm_call", return_value=response), \
            patch.object(random, "choice", side_effect=lambda seq: seq[-1]):
        result = agent.seer_investigate(seer, game)

    # If the bug is fixed, the partial-name "胡" should resolve to 胡桃
    # (the only player whose name contains it) rather than silently fall
    # through to random.choice — which, with our patched rng, would pick
    # the LAST player in uninvestigated (迪卢克), not 胡桃.
    assert result == "胡桃", (
        f"Expected partial name '胡' to resolve to '胡桃', got {result!r} "
        "(silent random fallback masked the mis-parse)"
    )


@pytest.mark.xfail(
    reason="Bug A: ambiguous '旅行者' silently falls to random.choice. No "
    "deterministic resolution / no loud error."
)
def test_seer_target_ambiguous_raises_or_deterministic():
    """LLM writes <target>旅行者</target> — could match 旅行者·空 or 旅行者·荧.

    Neither name is a substring of "旅行者" (reverse direction), so loop never
    matches → random fallback. Acceptable fix: raise ValueError, or log WARNING
    and consistently pick one candidate.
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("旅行者·荧", "werewolf"),
            ("胡桃", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_seer_ambiguous",
    )
    agent = _new_agent()
    seer = game.players[0]

    response = "<target>旅行者</target>"

    raised = False
    with patch.object(agent, "_llm_call", return_value=response):
        try:
            result = agent.seer_investigate(seer, game)
        except ValueError:
            raised = True
            result = None

    # Accept EITHER: raises ValueError (loud), OR returns 旅行者·荧 (the only
    # valid candidate that isn't the seer itself, if ever implemented as
    # "pick the unique alive non-self candidate that matches the fuzzy key").
    # Current code: returns random from alive_others, NOT None.
    assert raised or result == "旅行者·荧", (
        f"Ambiguous '旅行者' must either raise or deterministically pick "
        f"'旅行者·荧', got {result!r}"
    )


@pytest.mark.xfail(
    reason="Bug A: when no <target> tag, loop picks FIRST name found in "
    "alive_others order, not the LAST-mentioned-in-response (intent)."
)
def test_seer_no_target_tag_picks_last_mentioned_name():
    """Reasoning mentions multiple names, no <target> tag.

    Current code iterates `alive_others` in fixed order and breaks on the FIRST
    player whose name appears anywhere in the response string. Intent-preserving
    behaviour is to prefer the LAST mention (the one the model landed on).
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("行秋", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_seer_last_mentioned",
    )
    agent = _new_agent()
    seer = game.players[0]

    # No <target> tag. Mentions 胡桃 then 行秋, then lands on 菲谢尔.
    response = "胡桃怀疑行秋，但我最终决定查菲谢尔"

    with patch.object(agent, "_llm_call", return_value=response):
        result = agent.seer_investigate(seer, game)

    assert result == "菲谢尔", (
        f"Expected LAST-mentioned '菲谢尔' (intent), got {result!r} "
        "(current code picks first-in-alive_others order)"
    )


@pytest.mark.xfail(
    reason="Bug A: random-fallback path emits no WARNING log — violates "
    "project 'no silent fallback' rule."
)
def test_seer_random_fallback_emits_warning(caplog):
    """When LLM response has NO valid target at all, fallback must log WARNING."""
    _clean_memory_base()
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("行秋", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_seer_fallback_warn",
    )
    agent = _new_agent()
    seer = game.players[0]

    # Garbage response — nothing matches any alive player name.
    response = "<target>zzz_nobody</target>"

    with caplog.at_level(logging.WARNING, logger="backend.ai.agent"):
        with patch.object(agent, "_llm_call", return_value=response):
            result = agent.seer_investigate(seer, game)

    # Must still return SOMETHING (game has to proceed) but must have logged
    # at WARNING+ level describing the fallback. Any record at WARNING level
    # whose message mentions fallback / random / not found is acceptable.
    assert result is not None, "seer_investigate returned None on unknown target"
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and (
            "fallback" in r.getMessage().lower()
            or "random" in r.getMessage().lower()
            or "not found" in r.getMessage().lower()
        )
    ]
    assert warnings, (
        "Expected a WARNING+ log describing the silent random fallback, "
        f"but caplog captured: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Bug B — Seer → process_night_seer → write_night_records consistency
# ---------------------------------------------------------------------------


def test_seer_investigate_then_process_night_sets_correct_id():
    """After seer_investigate returns a name, get_player_by_name + process_night_seer
    must set game_state.seer_checked to that player's id (exact name match path)."""
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("行秋", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_seer_id_consistency",
    )
    agent = _new_agent()
    seer = game.players[0]

    response = "<target>胡桃</target>"
    with patch.object(agent, "_llm_call", return_value=response):
        name = agent.seer_investigate(seer, game)

    assert name == "胡桃"
    target = game.get_player_by_name(name)
    assert target is not None, f"get_player_by_name({name!r}) returned None"
    engine.process_night_seer(game, target.id)

    expected_id = game.players[1].id  # 胡桃
    assert game.seer_checked == expected_id, (
        f"seer_checked={game.seer_checked!r}, expected {expected_id!r}"
    )


def test_seer_night_record_reflects_actual_target_good():
    """write_night_records must persist the ACTUAL checked target + correct GOOD result."""
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("行秋", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_seer_rec_good",
    )
    agent = _new_agent()
    seer = game.players[0]

    # Seer checks 行秋 (a villager) → should produce GOOD.
    response = "<target>行秋</target>"
    with patch.object(agent, "_llm_call", return_value=response):
        name = agent.seer_investigate(seer, game)
    assert name == "行秋"

    target = game.get_player_by_name(name)
    engine.process_night_seer(game, target.id)

    # Snapshot & write night record.
    snapshot = game.snapshot_round_data()
    engine.write_night_records(game, round_num=game.round_number, snapshot=snapshot)

    # Read seer's on-disk night record for round 1.
    storage = MemoryStorage(seer.id, MEMORY_BASE, game.game_id)
    records = storage.read_night_record(num_nights=3)
    assert len(records) >= 1, "No night record written for seer"
    content = records[-1]
    assert "行秋" in content, f"Night record missing target '行秋':\n{content}"
    assert "GOOD" in content, f"Night record missing 'GOOD' result:\n{content}"
    assert "WEREWOLF" not in content, f"Night record wrongly claims WEREWOLF:\n{content}"


def test_seer_night_record_reflects_actual_target_werewolf():
    """Same as above but target IS a werewolf → record must say WEREWOLF."""
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("行秋", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_seer_rec_wolf",
    )
    agent = _new_agent()
    seer = game.players[0]

    response = "<target>胡桃</target>"
    with patch.object(agent, "_llm_call", return_value=response):
        name = agent.seer_investigate(seer, game)
    assert name == "胡桃"

    target = game.get_player_by_name(name)
    engine.process_night_seer(game, target.id)

    snapshot = game.snapshot_round_data()
    engine.write_night_records(game, round_num=game.round_number, snapshot=snapshot)

    storage = MemoryStorage(seer.id, MEMORY_BASE, game.game_id)
    records = storage.read_night_record(num_nights=3)
    content = records[-1]
    assert "胡桃" in content, f"Night record missing '胡桃':\n{content}"
    assert "WEREWOLF" in content, f"Night record should mark 胡桃 as WEREWOLF:\n{content}"


def test_seer_multi_round_records_do_not_overwrite():
    """Round 1 record for player_A must remain after round 2 checks player_B.

    Night records are stored one file per round under `night_record/`. Round 2
    must not clobber round 1.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("胡桃", "werewolf"),
            ("行秋", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_seer_multi_round",
    )
    agent = _new_agent()
    seer = game.players[0]

    # --- Round 1: check 胡桃 (wolf) ---
    game.round_number = 1
    with patch.object(agent, "_llm_call", return_value="<target>胡桃</target>"):
        name = agent.seer_investigate(seer, game)
    assert name == "胡桃"
    engine.process_night_seer(game, game.get_player_by_name(name).id)
    snap1 = game.snapshot_round_data()
    engine.write_night_records(game, round_num=1, snapshot=snap1)
    game.reset_round_data()

    # --- Round 2: check 行秋 (villager) ---
    game.round_number = 2
    with patch.object(agent, "_llm_call", return_value="<target>行秋</target>"):
        name = agent.seer_investigate(seer, game)
    assert name == "行秋"
    engine.process_night_seer(game, game.get_player_by_name(name).id)
    snap2 = game.snapshot_round_data()
    engine.write_night_records(game, round_num=2, snapshot=snap2)

    # Read individual round files directly — read_night_record gives latest N;
    # we want to assert round 1 is still on disk and still carries 胡桃/WEREWOLF.
    night_dir = MEMORY_BASE / game.game_id / seer.id / "night_record"
    assert night_dir.exists(), f"night_record dir missing: {night_dir}"
    files = sorted(night_dir.glob("night_*.md"))
    assert len(files) >= 2, f"Expected 2 night records, found {[f.name for f in files]}"

    round1 = next((f for f in files if "1" in f.stem), None)
    round2 = next((f for f in files if "2" in f.stem), None)
    assert round1 is not None and round2 is not None, (
        f"Could not identify round1/round2 files: {[f.name for f in files]}"
    )

    r1 = round1.read_text(encoding="utf-8")
    r2 = round2.read_text(encoding="utf-8")

    assert "胡桃" in r1, f"Round 1 record lost 胡桃 target:\n{r1}"
    assert "WEREWOLF" in r1, f"Round 1 record lost WEREWOLF result:\n{r1}"
    assert "行秋" in r2, f"Round 2 record missing 行秋 target:\n{r2}"
    assert "GOOD" in r2, f"Round 2 record missing GOOD result:\n{r2}"
    # Cross-contamination guard:
    assert "行秋" not in r1, f"Round 1 got round-2 target 行秋 leaked in:\n{r1}"


# ---------------------------------------------------------------------------
# Bug C — Speech context contamination under concurrent generate_speech
# ---------------------------------------------------------------------------


def test_generate_speech_no_context_contamination_across_concurrent_calls():
    """Two concurrent coroutines call generate_speech for different AIs.

    Because game.speeches is shared mutable state and add_speech mutates it
    between calls, the prompt built for the EARLIER speaker can end up
    containing speeches from LATER speakers if the ordering is wrong.

    We capture each AI's prompt and assert: AI_a's prompt must NOT contain
    AI_b's or AI_c's speech CONTENT.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("温迪", "villager"),     # AI_a
            ("胡桃", "werewolf"),     # AI_b
            ("菲谢尔", "villager"),   # AI_c
            ("行秋", "villager"),
            ("钟离", "seer"),
            ("迪卢克", "witch"),
        ],
        game_id="test_speech_race",
        phase=GamePhase.DAY_DISCUSSION,
        round_number=1,
    )
    agent = _new_agent()

    # Speakers we care about.
    wendy, hutao, fischl = game.players[0], game.players[1], game.players[2]
    game.speech_order = [wendy.id, hutao.id, fischl.id]

    # Sentinel content that should never leak BACKWARDS.
    sentinels = {
        wendy.name: "SENTINEL_WENDY_SPEECH_ALPHA",
        hutao.name: "SENTINEL_HUTAO_SPEECH_BETA",
        fischl.name: "SENTINEL_FISCHL_SPEECH_GAMMA",
    }

    # Every generate_speech call captures (name, prompt_that_was_built).
    captured: list[tuple[str, str]] = []
    captured_lock = threading.Lock()

    def fake_llm(prompt, *, max_tokens=500, temperature=0.7, system_prompt=None, **kwargs):
        # Infer which player this prompt is for by scanning for one of the
        # speaker names in the system_prompt (build_system_prompt embeds it).
        current_name = None
        for n in sentinels:
            if system_prompt and n in system_prompt:
                current_name = n
                break
        if current_name is None:
            # Fallback: last player in game.speech_order whose speech isn't logged yet.
            spoken = {s["player"] for s in game.speeches}
            for pid in game.speech_order:
                p = game.get_player_by_id(pid)
                if p and p.name not in spoken:
                    current_name = p.name
                    break
        with captured_lock:
            captured.append((current_name, prompt))
        # Widen the race window so the concurrent mutation is visible.
        time.sleep(0.05)
        return f"<speech>{sentinels.get(current_name, 'unknown')}</speech>"

    def run_speaker(player: Player):
        speech, _stickers = agent.generate_speech(player, game)
        engine.add_speech(game, player.id, speech)

    # Simulate the race: one "prefetch" thread generates all three in sequence,
    # concurrently with a second "/advance-discussion" thread that kicks off
    # the same speakers. In the buggy code, both tasks mutate game.speeches
    # concurrently and the prompt built for an earlier speaker can see a
    # later speaker's content.
    with patch.object(agent, "_llm_call", side_effect=fake_llm):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(lambda: [run_speaker(p) for p in (wendy, hutao, fischl)])
            f2 = pool.submit(lambda: [run_speaker(p) for p in (wendy, hutao, fischl)])
            f1.result(timeout=30)
            f2.result(timeout=30)

    # For the FIRST capture of each speaker, the prompt must not contain
    # the sentinels of LATER speakers. (We only check first capture because
    # subsequent calls legitimately see earlier speeches.)
    first_prompt: dict[str, str] = {}
    for name, prompt in captured:
        if name and name not in first_prompt:
            first_prompt[name] = prompt

    assert wendy.name in first_prompt, f"No captured prompt for {wendy.name}"
    wendy_prompt = first_prompt[wendy.name]
    # Wendy is FIRST speaker — her prompt must mention nobody else's sentinel.
    assert sentinels[hutao.name] not in wendy_prompt, (
        f"{wendy.name}'s prompt leaked {hutao.name}'s speech content"
    )
    assert sentinels[fischl.name] not in wendy_prompt, (
        f"{wendy.name}'s prompt leaked {fischl.name}'s speech content"
    )

    if hutao.name in first_prompt:
        hutao_prompt = first_prompt[hutao.name]
        assert sentinels[fischl.name] not in hutao_prompt, (
            f"{hutao.name}'s prompt leaked {fischl.name}'s later speech"
        )


def test_advance_discussion_adopts_prefetch_instead_of_racing_it():
    """End-to-end regression for the speech-race (Bug 1).

    Reproduces the production race: after /advance-night launches the
    `_prefetch_pre_speeches` background task, the frontend's auto-advance
    calls /advance-discussion while the prefetch is still mid-flight. The
    buggy behavior was: advance_discussion synchronously regenerated speeches
    for the same speakers, causing
      - duplicate entries in game.speeches / game.events
      - prompt contamination when one thread's add_speech landed between
        another thread's memory load and LLM call.

    After the fix, advance_discussion must CONSUME the prefetch cache via
    prefetch.get_or_wait instead of regenerating. Invariants checked:
      - game.speeches contains each pre-human speaker exactly once
      - game.events has exactly one 'speech' event per pre-human speaker
      - the captured prompt for a later pre-human speaker does not contain
        the LLM output string produced for an earlier pre-human speaker
        (since _prefetch_pre_speeches generates them serially, each prompt
        sees only strictly-earlier speeches — never later ones).
    """
    import uuid

    import httpx

    from backend import app as app_mod
    from backend.models import RoomStatus

    # Isolate memory side-effects from other tests.
    _clean_memory_base()
    app_mod.game_engine.memory_base = MEMORY_BASE
    app_mod.games.clear()
    # Reset rooms so any leftover state from prior tests doesn't bleed in.
    for room in app_mod._room_manager._rooms.values():
        room.players = []
        room.host_identity = None
        room.status = RoomStatus.WAITING
        room.game_id = None
        room.started_at = None
        room.mode = "classic_6_witch"

    # Build a game where the human is the LAST speaker so all 5 AI slots
    # are pre-human. This maximizes the contamination surface.
    human_identity = str(uuid.uuid4())
    game_id = f"test_race_{uuid.uuid4().hex[:8]}"
    players = []
    # AI speakers first (player_1..player_5), human last (player_6).
    ai_specs = [
        ("温迪", "villager"),
        ("胡桃", "werewolf"),
        ("菲谢尔", "villager"),
        ("行秋", "seer"),
        ("迪卢克", "witch"),
    ]
    for idx, (name, role) in enumerate(ai_specs):
        players.append(
            Player(
                id=f"player_{idx + 1}",
                name=name,
                role=role,
                personality="LOGICAL",
                is_human=False,
            )
        )
    players.append(
        Player(
            id="player_6",
            name="Alice",
            role="villager",
            personality="LOGICAL",
            is_human=True,
            identity=human_identity,
        )
    )

    game = GameState(
        game_id=game_id,
        mode="classic_6_witch",
        phase=GamePhase.DAY_DISCUSSION,
        round_number=1,
        players=players,
        memory_base=MEMORY_BASE,
        human_identities={human_identity},
    )
    # Morning-ack quorum must be satisfied or /advance-discussion short-circuits.
    game.morning_acks = {human_identity}
    # Fix the speaking order so pre-human ids are the 5 AI players.
    # Match /advance-night's behavior: current_speaker_id snaps to the
    # first speaker once speech_order is known.
    game.speech_order = [p.id for p in players]
    game.current_speaker_id = game.speech_order[0]

    # Initialize knowledge files so generate_speech's memory load doesn't crash.
    km = KnowledgeManager(MEMORY_BASE, game_id)
    for p in players:
        try:
            km.initialize_player_knowledge(p.id, p.role, game_mode="classic_6_witch")
        except Exception:
            pass
    try:
        km.initialize_werewolf_shared()
    except Exception:
        pass

    app_mod.games[game_id] = game

    pre_human_ids = [p.id for p in players if not p.is_human]

    # Sentinels — one per AI speaker. The LLM mock returns its sentinel and
    # sleeps briefly so the race is observable.
    # Sentinels must contain ≥4 Chinese chars to survive sanitize_speech.
    sentinels = {
        "温迪": "哨兵温迪甲的发言内容",
        "胡桃": "哨兵胡桃乙的发言内容",
        "菲谢尔": "哨兵菲谢尔丙的发言内容",
        "行秋": "哨兵行秋丁的发言内容",
        "迪卢克": "哨兵迪卢克戊的发言内容",
    }
    captured: list[tuple[str, str]] = []
    captured_lock = threading.Lock()

    # Wrap generate_speech so we know which player is active for each
    # _llm_call. The signature/system_prompt alone doesn't uniquely identify
    # the speaker (multiple character names appear in inter-character
    # dynamics), so we thread the speaker through a context list.
    active_speaker_stack: list[str] = []
    stack_lock = threading.Lock()

    original_generate_speech = app_mod.ai_agent.generate_speech

    def wrapped_generate_speech(player, game_state):
        with stack_lock:
            active_speaker_stack.append(player.name)
        try:
            return original_generate_speech(player, game_state)
        finally:
            with stack_lock:
                active_speaker_stack.pop()

    def fake_llm(prompt, *, max_tokens=500, temperature=0.7, system_prompt=None, **kwargs):
        with stack_lock:
            current_name = active_speaker_stack[-1] if active_speaker_stack else None
        with captured_lock:
            captured.append((current_name, prompt or ""))
        # Sleep so _prefetch_pre_speeches and the /advance-discussion path
        # have a visible race window.
        time.sleep(0.05)
        return f"<speech>{sentinels.get(current_name, 'unknown')}</speech>"

    async def _drive_race():
        # 1. Launch the prefetch coroutine just like /advance-night does.
        #    Running inside the same event loop as the AsyncClient means
        #    the prefetch task is schedulable while the endpoint awaits.
        await app_mod.prefetch.launch(
            game.game_id,
            "pre_speeches",
            game.round_number,
            app_mod._prefetch_pre_speeches(game, pre_human_ids),
        )
        # Let the prefetch start its first LLM call before we invoke
        # the adoption path. Without this yield the cache entry exists
        # but no work has started yet, which is a less interesting race.
        await asyncio.sleep(0.01)

        # 2. Now call /advance-discussion while the prefetch is still
        #    producing speeches.
        transport = httpx.ASGITransport(app=app_mod.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            return await ac.post(
                "/api/game/advance-discussion",
                json={"game_id": game_id, "player_id": "player_6"},
                headers={"Authorization": f"Bearer {human_identity}"},
            )

    with patch.object(app_mod.ai_agent, "_llm_call", side_effect=fake_llm):
        with patch.object(app_mod.ai_agent, "generate_speech", side_effect=wrapped_generate_speech):
            resp = asyncio.run(_drive_race())

    assert resp.status_code == 200, (
        f"/advance-discussion failed: {resp.status_code} {resp.text}"
    )

    # --- Invariant 1: each pre-human speaker appears exactly once in
    # game.speeches. The buggy pre-fix code would get 2 entries per speaker
    # (one from prefetch, one from the synchronous loop).
    by_speaker: dict[str, int] = {}
    for s in game.speeches:
        by_speaker[s["player"]] = by_speaker.get(s["player"], 0) + 1
    for name in sentinels:
        count = by_speaker.get(name, 0)
        assert count == 1, (
            f"game.speeches has {count} entries for {name} (expected 1). "
            f"Full speeches: {game.speeches}"
        )

    # --- Invariant 2: exactly one speech event per pre-human speaker.
    event_counts: dict[str, int] = {}
    for ev in game.events:
        if ev.type == "speech" and ev.round == game.round_number:
            # message format: "【<name>】: <speech>"
            speaker_name = ev.message.split("】")[0].lstrip("【")
            event_counts[speaker_name] = event_counts.get(speaker_name, 0) + 1
    for name in sentinels:
        count = event_counts.get(name, 0)
        assert count == 1, (
            f"game.events has {count} speech events for {name} in round "
            f"{game.round_number} (expected 1). Event messages: "
            f"{[ev.message for ev in game.events if ev.type == 'speech']}"
        )

    # --- Invariant 3: no wasted LLM call. If advance_discussion correctly
    # ADOPTS the prefetch's work, each pre-human speaker's generate_speech
    # should be invoked exactly once across the whole run. In the buggy
    # pre-fix code, both the prefetch task and the synchronous loop call
    # generate_speech for the same speaker, producing two LLM calls per
    # speaker (one is a silent waste even though add_speech dedups).
    per_speaker_calls: dict[str, int] = {}
    for name, _prompt in captured:
        if name:
            per_speaker_calls[name] = per_speaker_calls.get(name, 0) + 1
    for name in sentinels:
        n = per_speaker_calls.get(name, 0)
        assert n == 1, (
            f"generate_speech ran {n} times for {name} (expected 1). "
            f"The pre-fix code regenerates speeches already produced by "
            f"_prefetch_pre_speeches — wasting LLM calls and risking "
            f"prompt contamination via shared game.speeches mutation. "
            f"Per-speaker call counts: {per_speaker_calls}"
        )

    # --- Invariant 4: context isolation. For the LATER pre-human speaker's
    # first captured prompt, the EARLIER speaker's LLM OUTPUT sentinel must
    # not appear. Because _prefetch_pre_speeches runs serially and the
    # adoption path doesn't regenerate, no prompt should ever contain a
    # later speaker's sentinel.
    first_prompt: dict[str, str] = {}
    for name, prompt in captured:
        if name and name not in first_prompt:
            first_prompt[name] = prompt

    # For each ordered pair (earlier, later), later's sentinel must not
    # leak into earlier's prompt.
    order_names = [p.name for p in players if not p.is_human]
    for i, earlier in enumerate(order_names):
        if earlier not in first_prompt:
            continue
        earlier_prompt = first_prompt[earlier]
        for later in order_names[i + 1 :]:
            assert sentinels[later] not in earlier_prompt, (
                f"Race-free adoption invariant broken: {earlier}'s prompt "
                f"contains {later}'s sentinel {sentinels[later]!r}. "
                f"This means either (a) advance_discussion regenerated a "
                f"speech while prefetch was still running, or (b) two "
                f"generators mutated game.speeches concurrently."
            )


# ---------------------------------------------------------------------------
# Sanity: witch poison / werewolf kill / guard protect partial-name fallback
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="Sanity/Bug A-sibling: witch poison with partial name → "
    "get_player_by_name returns None → poison silently skipped (no WARNING)."
)
def test_witch_poison_partial_name_fallback_is_loud(caplog):
    """Witch returns `{"action":"poison","target":"空"}` while the player is
    actually named "旅行者·空". app.py uses `get_player_by_name` (exact match)
    → returns None → poison silently skipped. Must log WARNING.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("迪卢克", "witch"),
            ("胡桃", "werewolf"),
            ("旅行者·空", "villager"),
            ("菲谢尔", "villager"),
            ("行秋", "villager"),
            ("钟离", "seer"),
        ],
        game_id="test_witch_partial_poison",
        phase=GamePhase.NIGHT_WITCH,
        round_number=1,
    )
    agent = _new_agent()
    witch = game.players[0]

    # Valid JSON, valid action, but partial target name.
    response = '{"action": "poison", "target": "空", "reasoning": "too suspicious"}'

    with caplog.at_level(logging.WARNING, logger="backend.ai.agent"):
        with patch.object(agent, "_llm_call", return_value=response):
            action = agent.witch_action(witch, None, game)

    # The agent itself returns {"action": "poison", "target": "空"}; the *call
    # site* in app.py then silently drops the poison. That silent-drop must
    # be accompanied by a WARNING.
    #
    # Mirror the app.py logic here to demonstrate the silent drop, and assert
    # a warning is emitted. Current implementation: no warning.
    if action.get("action") == "poison":
        poison_target = game.get_player_by_name(action.get("target"))
        if not poison_target:
            # BUG: app.py / engine code should log here. Today it doesn't.
            # (No logging call in the current code path.)
            pass

    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "poison" in r.getMessage().lower()
    ]
    assert warnings, (
        "Partial poison target dropped without WARNING log. "
        f"caplog={[r.getMessage() for r in caplog.records]}"
    )


def test_werewolf_kill_partial_name_fallback_is_loud(caplog):
    """wolf LLM JSON final_target='空' while true name is '旅行者·空'.
    Downstream app code does `game.get_player_by_name(target_name)` which is
    exact-match → None. Must log WARNING instead of silent no-op.
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("胡桃", "werewolf"),
            ("迪卢克", "werewolf"),
            ("旅行者·空", "villager"),
            ("菲谢尔", "villager"),
            ("行秋", "seer"),
            ("钟离", "witch"),
        ],
        game_id="test_wolf_partial_kill",
        phase=GamePhase.NIGHT_WEREWOLF,
        round_number=1,
    )
    agent = _new_agent()

    # JSON that parses cleanly but uses partial name.
    response = (
        '{"wolf1_suggestion":"空","wolf1_reasoning":"x",'
        '"wolf2_suggestion":"空","wolf2_reasoning":"y",'
        '"final_target":"空","team_reasoning":"z"}'
    )

    with caplog.at_level(logging.WARNING, logger="backend.ai.agent"):
        with patch.object(agent, "_llm_call", return_value=response):
            collab = agent.werewolf_collaborate(game)

    target_name = collab.get("target") if isinstance(collab, dict) else collab
    resolved = game.get_player_by_name(target_name) if target_name else None

    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
    ]

    # Two acceptable outcomes:
    #   (a) The LLM response was REJECTED by parse_json_response validator
    #       (field_validators on final_target), a WARNING/ERROR was emitted,
    #       and the random fallback picked some valid alive target. Good —
    #       this is already "loud".
    #   (b) A future fix resolves the partial name to 旅行者·空 exactly.
    # What we reject:
    #   - target_name is "空" (partial name passed through silently).
    assert target_name != "空", (
        "Wolf collab passed the partial LLM string '空' through unchanged — "
        "downstream get_player_by_name will fail silently."
    )
    if resolved is None:
        assert warnings, (
            "Wolf partial-name kill target silently dropped (no WARNING). "
            f"target={target_name!r}"
        )
    # If resolved is a valid player, we tolerate random fallback as long as a
    # WARNING/ERROR was logged explaining why the LLM's target was rejected.
    if resolved and resolved.name != "旅行者·空":
        assert warnings, (
            "Wolf collab fell back to a non-intent target without any "
            f"WARNING log. target={target_name!r}"
        )


@pytest.mark.xfail(
    reason="Guard silent-fallback branch at agent.py:921 logs WARNING, but "
    "partial-name case doesn't reach that branch because '胡' IS substring of "
    "'胡桃' — which means the bug manifests for prefix-missing names like '空' "
    "vs '旅行者·空' (reverse direction)."
)
def test_guard_protect_partial_name_reverse_direction_is_loud(caplog):
    """Guard gets '<target>空</target>' — "旅行者·空" is NOT substring of "空",
    so the substring loop never matches → random fallback. The code DOES emit
    a WARNING here (good), so this test mainly guards against a regression
    where the warning is removed.
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("钟离", "guard"),
            ("胡桃", "werewolf"),
            ("旅行者·空", "villager"),
            ("菲谢尔", "villager"),
            ("行秋", "villager"),
            ("迪卢克", "witch"),
        ],
        game_id="test_guard_partial",
        phase=GamePhase.NIGHT_GUARD,
        round_number=1,
    )
    agent = _new_agent()
    guard = game.players[0]

    response = "<target>空</target>"  # will NOT substring-match any player name
    with caplog.at_level(logging.WARNING, logger="backend.ai.agent"):
        with patch.object(agent, "_llm_call", return_value=response):
            target = agent.guard_protect(guard, game)

    # In the ideal fix: '空' resolves to '旅行者·空' (the unique player whose
    # name contains '空'). Currently: random fallback.
    assert target == "旅行者·空", (
        f"Partial name '空' should resolve to '旅行者·空', got {target!r}"
    )

    # Regardless of resolution direction, guard already logs a WARNING on
    # fallback (agent.py:921). Confirm the warning infrastructure exists.
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
    ]
    # Even in current buggy state a warning should be emitted. If the bug is
    # fixed (partial-name resolves), this assertion is harmlessly skipped.
    if target != "旅行者·空":
        assert warnings, f"Guard fallback without WARNING: {[r.getMessage() for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Bug R — Reflection Curator fabricates seer-check lines (live Render bug)
# ---------------------------------------------------------------------------
#
# Smoking-gun investigation: the seer on Render is receiving a prompt context
# that labels a villager ('旅行者·空') as WEREWOLF. Because seer context is
# loaded from the seer's own knowledge summary (`summary.md` → section
# `### 验证身份`), any append to that section is what the seer sees next
# round.
#
# Write paths into that section:
#   1. ReflectionPipeline.curate()  — LLM-driven, NO GROUNDING CHECK
#   2. (nothing else currently writes "### 验证身份" during normal play; the
#      night record goes to night_record/, not knowledge/role/seer/summary.md)
#
# Curator logic (backend/memory/reflection.py):
#   - _parse_json_response: purely structural JSON sanity (lines 364-411)
#   - curate(): accepts any new_item whose content is >=10 chars and whose
#     `section` literally appears in the current summary (lines 472-516).
#     It appends `[#id] [H:0] {content}` verbatim. No check against
#     game_state.seer_checks. No rejection of content mentioning players the
#     seer has NOT actually checked. No check that "WEREWOLF"/"GOOD" labels
#     match the authoritative seer_checks dict.
#
# That is the injection path for the live bug.


def _read_seer_summary(seer: Player, game_id: str) -> str:
    """Helper: read the on-disk seer summary for assertions."""
    storage = MemoryStorage(seer.id, MEMORY_BASE, game_id)
    return storage.read_knowledge_summary("seer")


def _parse_verified_lines(summary_text: str) -> list[tuple[str, str]]:
    """Extract (name, result) tuples from '### 验证身份' section of a summary.

    Matches lines like `- **{name}**: {RESULT} ...` or
    `[#xxx] [H:0] **{name}**: {RESULT} ...` within/after the 验证身份 header.
    """
    import re as _re

    # Grab only the 验证身份 subsection (up to next ###/## header or EOF).
    m = _re.search(
        r"###\s*验证身份\s*\n(.*?)(?=\n###|\n##|\Z)",
        summary_text,
        _re.DOTALL,
    )
    if not m:
        return []
    block = m.group(1)

    # Typical entries we care about:
    #   - **旅行者·空**: WEREWOLF ✓（第1轮查验）
    #   - [#s1r1] [H:0] **旅行者·空**: WEREWOLF ...
    pairs: list[tuple[str, str]] = []
    for line in block.splitlines():
        mm = _re.search(r"\*\*([^\*\n]+?)\*\*\s*[:：]\s*(WEREWOLF|GOOD)", line)
        if mm:
            pairs.append((mm.group(1).strip(), mm.group(2).strip()))
    return pairs


def test_R1_curator_does_not_fabricate_werewolf_tag_on_villager():
    """R1: Curator must not accept an LLM delta that claims a villager is WEREWOLF.

    Setup: seer legitimately checked villager X in round 1 (result=GOOD, per
    game_state.seer_checks). The Reflector LLM is mocked to hallucinate a
    new_item saying `**X**: WEREWOLF ✓（第1轮查验）`. We then run the full
    pipeline end-to-end.

    Expected (safe): summary.md MUST NOT end up with a line labelling X as
    WEREWOLF, because X's authoritative seer_checks result is GOOD.

    If this test FAILS: Curator has no grounding check against seer_checks
    and is the injection vector for the live Render bug.
    """
    _clean_memory_base()
    from backend.memory.reflection import ReflectionPipeline

    game = _make_game(
        [
            ("钟离", "seer"),
            ("胡桃", "werewolf"),
            ("旅行者·空", "villager"),   # X — legitimately GOOD
            ("菲谢尔", "villager"),
            ("行秋", "villager"),
            ("迪卢克", "witch"),
        ],
        game_id="test_R1_curator_fabrication",
        phase=GamePhase.NIGHT_SEER,
        round_number=1,
    )
    seer = game.players[0]
    victim_villager = game.players[2]  # 旅行者·空

    # Authoritative ground truth: seer checked 旅行者·空 and got GOOD.
    game.seer_checked = victim_villager.id
    game.seer_checks[victim_villager.id] = "GOOD"

    # Pre-populate a benign day_record shared entry (not load-bearing here,
    # but mirrors the live flow).
    shared_storage = MemoryStorage("day_record_shared", MEMORY_BASE, game.game_id)
    shared_storage.create_day_record_shared(
        round_num=1,
        content="# 第1轮白天\n\n## 白天发言\n- **钟离**: 我观察中\n",
    )

    # Mock LLM: Reflector hallucinates "旅行者·空 is WEREWOLF".
    hallucinated = {
        "updates": [],
        "new_items": [
            {
                "section": "### 验证身份",
                "content": f"**{victim_villager.name}**: WEREWOLF ✓（第1轮查验）",
                "source": "round_1_reflection",
            }
        ],
        "strategy_shift": "我已验出狼人",
    }

    import json as _json

    def fake_llm(prompt, max_tokens=800, temperature=0.7):
        return _json.dumps(hallucinated, ensure_ascii=False)

    pipeline = ReflectionPipeline(MEMORY_BASE, game.game_id, llm_call_fn=fake_llm)
    pipeline.run(seer, game, round_num=1)

    # Inspect resulting summary.
    summary = _read_seer_summary(seer, game.game_id)
    pairs = _parse_verified_lines(summary)

    # Any line naming the villager must NOT claim WEREWOLF.
    bad = [(n, r) for (n, r) in pairs if n == victim_villager.name and r == "WEREWOLF"]
    assert not bad, (
        f"SMOKING GUN: Curator accepted LLM-hallucinated WEREWOLF tag on villager "
        f"{victim_villager.name} despite seer_checks[{victim_villager.id}]='GOOD'. "
        f"Grounding check missing at backend/memory/reflection.py::curate "
        f"(reflection.py:472-516). Parsed verified lines: {pairs}"
    )


def test_R2_curator_delta_grounded_only_in_actual_seer_checks():
    """R2: Curator must reject a delta about a player the seer never checked.

    Setup: seer_checks = {A: GOOD}, i.e. only player A was checked.
    Reflector LLM hallucinates a new_item saying `**B**: WEREWOLF`.

    Expected (safe): summary.md gets no WEREWOLF row for B.

    If this test FAILS: Curator accepts arbitrary player-identity claims with
    no grounding — which is the same smoking-gun class as R1.
    """
    _clean_memory_base()
    from backend.memory.reflection import ReflectionPipeline

    game = _make_game(
        [
            ("钟离", "seer"),
            ("胡桃", "werewolf"),
            ("旅行者·空", "villager"),   # A — the only checked one
            ("菲谢尔", "villager"),       # B — NEVER checked
            ("行秋", "villager"),
            ("迪卢克", "witch"),
        ],
        game_id="test_R2_curator_ungrounded",
        phase=GamePhase.NIGHT_SEER,
        round_number=1,
    )
    seer = game.players[0]
    a = game.players[2]  # 旅行者·空 — actually checked
    b = game.players[3]  # 菲谢尔 — never checked

    game.seer_checked = a.id
    game.seer_checks[a.id] = "GOOD"
    # Explicitly: b.id NOT in seer_checks.

    hallucinated = {
        "updates": [],
        "new_items": [
            {
                "section": "### 验证身份",
                "content": f"**{b.name}**: WEREWOLF ✓（第1轮查验）",
                "source": "round_1_reflection",
            }
        ],
        "strategy_shift": "菲谢尔是狼",
    }

    import json as _json

    def fake_llm(prompt, max_tokens=800, temperature=0.7):
        return _json.dumps(hallucinated, ensure_ascii=False)

    pipeline = ReflectionPipeline(MEMORY_BASE, game.game_id, llm_call_fn=fake_llm)
    pipeline.run(seer, game, round_num=1)

    summary = _read_seer_summary(seer, game.game_id)
    pairs = _parse_verified_lines(summary)

    # No verified-identity claim should reference B (never checked).
    injected = [(n, r) for (n, r) in pairs if n == b.name]
    assert not injected, (
        f"SMOKING GUN: Curator accepted a verified-identity delta for {b.name} "
        f"who was never investigated (seer_checks keys = {list(game.seer_checks.keys())}). "
        f"Curator must ground entries in '### 验证身份' against game_state.seer_checks. "
        f"Offending lines: {injected}"
    )


def test_R3_multi_round_full_memory_audit_two_humans():
    """R3: Full 3-round audit with 2 humans (双旅行者 setup).

    Walks a 6-player game through 3 rounds, with the seer checking:
      round 1: 旅行者·空 (villager)     → should record GOOD
      round 2: 胡桃        (werewolf)    → should record WEREWOLF
      round 3: 旅行者·荧 (villager)     → should record GOOD

    After each round's reflection, parse the seer's summary and assert that
    the 验证身份 section contains NO label contradicting the ground truth.

    Also assert no cross-player leakage: no villager's knowledge dir should
    have seer-only content.
    """
    _clean_memory_base()
    from backend.memory.reflection import ReflectionPipeline

    game = _make_game(
        [
            ("钟离", "seer"),
            ("胡桃", "werewolf"),
            ("迪卢克", "werewolf"),
            ("旅行者·空", "villager"),
            ("旅行者·荧", "villager"),
            ("菲谢尔", "villager"),
        ],
        game_id="test_R3_multi_round_audit",
        phase=GamePhase.NIGHT_SEER,
        round_number=1,
    )
    seer = game.players[0]
    wolves = {p.name: p for p in game.players if p.role == "werewolf"}
    villagers = {p.name: p for p in game.players if p.role == "villager"}

    # Reflector hallucinator (per-round): we DO NOT feed it a malicious delta
    # here. We feed it a plausible correct delta based on the round's check
    # and let Curator merge. This is the baseline: if the REAL check is
    # correctly merged, good. We ALSO inject ONE malicious row per round to
    # see if it slips through.
    import json as _json

    def make_fake_llm(correct_name: str, correct_result: str, malicious_name: str, malicious_result: str):
        payload = {
            "updates": [],
            "new_items": [
                {
                    "section": "### 验证身份",
                    "content": f"**{correct_name}**: {correct_result} ✓（第{game.round_number}轮查验）",
                    "source": f"round_{game.round_number}_reflection",
                },
                {
                    "section": "### 验证身份",
                    "content": f"**{malicious_name}**: {malicious_result} ✓（推断）",
                    "source": f"round_{game.round_number}_reflection",
                },
            ],
            "strategy_shift": "继续追查",
        }

        def fake_llm(prompt, max_tokens=800, temperature=0.7):
            return _json.dumps(payload, ensure_ascii=False)

        return fake_llm

    # Round 1: check 旅行者·空 (villager → GOOD).
    # Malicious delta: claim 旅行者·荧 is WEREWOLF (villager → should be rejected).
    game.round_number = 1
    game.seer_checked = villagers["旅行者·空"].id
    game.seer_checks[villagers["旅行者·空"].id] = "GOOD"
    pipeline = ReflectionPipeline(
        MEMORY_BASE,
        game.game_id,
        llm_call_fn=make_fake_llm("旅行者·空", "GOOD", "旅行者·荧", "WEREWOLF"),
    )
    pipeline.run(seer, game, round_num=1)
    game.reset_round_data()

    # Round 2: check 胡桃 (werewolf → WEREWOLF).
    # Malicious delta: claim 菲谢尔 is WEREWOLF (villager → should be rejected).
    game.round_number = 2
    game.seer_checked = wolves["胡桃"].id
    game.seer_checks[wolves["胡桃"].id] = "WEREWOLF"
    pipeline = ReflectionPipeline(
        MEMORY_BASE,
        game.game_id,
        llm_call_fn=make_fake_llm("胡桃", "WEREWOLF", "菲谢尔", "WEREWOLF"),
    )
    pipeline.run(seer, game, round_num=2)
    game.reset_round_data()

    # Round 3: check 旅行者·荧 (villager → GOOD).
    # Malicious delta: claim 旅行者·空 is WEREWOLF (villager → should be rejected).
    game.round_number = 3
    game.seer_checked = villagers["旅行者·荧"].id
    game.seer_checks[villagers["旅行者·荧"].id] = "GOOD"
    pipeline = ReflectionPipeline(
        MEMORY_BASE,
        game.game_id,
        llm_call_fn=make_fake_llm("旅行者·荧", "GOOD", "旅行者·空", "WEREWOLF"),
    )
    pipeline.run(seer, game, round_num=3)

    # === Audit ===
    summary = _read_seer_summary(seer, game.game_id)
    pairs = _parse_verified_lines(summary)

    # Build ground-truth map: role → expected result polarity.
    truth_role = {p.name: p.role for p in game.players}

    violations: list[str] = []
    for (name, result) in pairs:
        role = truth_role.get(name)
        if role is None:
            violations.append(f"unknown player name in summary: {name}")
            continue
        # villagers / seers / witches / guards / hunters are NOT werewolves
        if role != "werewolf" and result == "WEREWOLF":
            violations.append(f"{name} (role={role}) falsely labelled WEREWOLF")
        if role == "werewolf" and result == "GOOD":
            violations.append(f"{name} (werewolf) falsely labelled GOOD")

    assert not violations, (
        "SMOKING GUN: seer summary contains verified-identity rows contradicting "
        "ground truth.\nViolations:\n  - " + "\n  - ".join(violations) +
        f"\n\nFull parsed pairs: {pairs}"
    )

    # Cross-player leakage: no villager or werewolf should have seer-only
    # sections in their OWN role summary.
    for p in game.players:
        if p.role == "seer":
            continue
        other_storage = MemoryStorage(p.id, MEMORY_BASE, game.game_id)
        other_summary = other_storage.read_knowledge_summary(p.role)
        assert "### 验证身份" not in other_summary, (
            f"CROSS-PLAYER LEAK: {p.name} ({p.role}) summary contains seer-only "
            f"section '### 验证身份'. Memory isolation violated."
        )


def test_R4_speech_race_polluted_day_record_does_not_flip_seer_verdict():
    """R4: Poisoned day_record (simulating Bug C speech race) must NOT cause
    Curator to mark a villager as WEREWOLF.

    Setup: seer is 菲谢尔; she checked 旅行者·空 in round 1 → GOOD (ground
    truth). We then craft a poisoned day_record_shared round_1.md in which
    a speech attributed to 菲谢尔 falsely claims "我作为预言家查验了旅行者·空，
    他是狼人" — i.e., a Bug-C-style mis-attribution where hutao's speech got
    merged under 菲谢尔's name.

    The Reflector LLM, reading this poisoned context, could plausibly append
    a verified-identity entry labelling 旅行者·空 as WEREWOLF. The Curator
    MUST reject that entry because it contradicts the seer's own
    authoritative seer_checks.

    If this test FAILS: Curator has no grounding check against seer_checks,
    which is the same root cause as R1/R2 and the live Render bug.
    """
    _clean_memory_base()
    from backend.memory.reflection import ReflectionPipeline

    game = _make_game(
        [
            ("胡桃", "werewolf"),
            ("菲谢尔", "seer"),
            ("行秋", "villager"),
            ("旅行者·空", "villager"),
            ("旅行者·荧", "villager"),
            ("迪卢克", "witch"),
        ],
        game_id="test_R4_speech_race_poison",
        phase=GamePhase.DAY_DISCUSSION,
        round_number=1,
    )
    seer = game.players[1]  # 菲谢尔
    victim = game.players[3]  # 旅行者·空

    # Ground truth: seer actually checked 旅行者·空 and got GOOD.
    game.seer_checked = victim.id
    game.seer_checks[victim.id] = "GOOD"

    # Poison the shared day record: attribute wolf-style claim to seer.
    # (This simulates Bug C: hutao's speech got merged into 菲谢尔's slot.)
    poisoned_day_md = (
        "# 第1轮白天事件\n\n"
        "## 白天发言\n"
        "- **胡桃**: 我觉得大家都很可疑，我等着查验结果\n"
        # Misattributed line — reads like seer's own log but is hutao's claim.
        "- **菲谢尔**: 我作为预言家查验了旅行者·空，他是狼人\n"
        "- **行秋**: 那听预言家的\n"
    )
    shared_storage = MemoryStorage("day_record_shared", MEMORY_BASE, game.game_id)
    shared_storage.create_day_record_shared(round_num=1, content=poisoned_day_md)

    # Plausible LLM response when reading the poisoned context: the model
    # sees "菲谢尔: 我查验了旅行者·空，他是狼人" in the events text and
    # dutifully records it.
    polluted_delta = {
        "updates": [],
        "new_items": [
            {
                "section": "### 验证身份",
                "content": f"**{victim.name}**: WEREWOLF ✓（第1轮查验，我公开宣布）",
                "source": "round_1_discussion",
            }
        ],
        "strategy_shift": "已公开验人结果",
    }

    import json as _json

    def fake_llm(prompt, max_tokens=800, temperature=0.7):
        return _json.dumps(polluted_delta, ensure_ascii=False)

    pipeline = ReflectionPipeline(MEMORY_BASE, game.game_id, llm_call_fn=fake_llm)
    pipeline.run(seer, game, round_num=1)

    summary = _read_seer_summary(seer, game.game_id)
    pairs = _parse_verified_lines(summary)

    # The seer's OWN seer_checks says 旅行者·空 = GOOD. Any WEREWOLF row for
    # this victim is a false positive that originated from the polluted
    # day_record and passed through Curator unchecked.
    false_positives = [(n, r) for (n, r) in pairs if n == victim.name and r == "WEREWOLF"]
    assert not false_positives, (
        f"SMOKING GUN: Curator accepted WEREWOLF delta for {victim.name} even "
        f"though seer_checks[{victim.id}]='GOOD'. The delta was LLM-generated "
        f"from a poisoned day_record (Bug C race). Curator must cross-check "
        f"'### 验证身份' appends against game_state.seer_checks at "
        f"backend/memory/reflection.py::curate lines 472-516. "
        f"Offending lines: {false_positives}"
    )


# ---------------------------------------------------------------------------
# Bug N — Name ↔ ID dispatch invariants for every night skill
# ---------------------------------------------------------------------------
#
# User's theory: the seer's memory records "旅行者·空 = WEREWOLF" not because
# the Curator fabricates a line, but because app.py's name→ID lookup resolves
# to a DIFFERENT player than the one the agent internally picked. I.e. the
# agent writes "旅行者·空: GOOD" to knowledge_summary based on its internal
# `target.is_werewolf()`, but then returns target.name and app.py re-looks up
# by name, receives a different player (e.g. an AI werewolf), and feeds THAT
# id to process_night_seer. Memory and engine state then disagree.
#
# The invariant under test (for every night skill):
#
#     name_written_to_memory == get_player_by_name(returned_name).name
#     role_label_in_memory   == get_player_by_name(returned_name).is_werewolf()
#
# If EITHER side of the equality can break, you have a silent dispatch bug.
# The relevant code points:
#   - agent.py:707-728   (seer: substring match + update_knowledge_summary)
#   - agent.py:912-936   (guard: substring match + update_knowledge_summary)
#   - agent.py:502-567   (wolf collab: update_werewolf_strategy + personal)
#   - agent.py:813-825   (witch poison: target stored in knowledge_json)
#   - app.py:1568-1587   (AI night dispatch: get_player_by_name → process_*)
#   - app.py:1602-1611   (AI witch dispatch)
#   - models/game.py:131-136 (get_player_by_name: FIRST-match by exact equality)
#   - engine.py:609-638  (process_night_seer sets game_state.seer_checked)


def test_N1_name_uniqueness_invariant_across_full_player_list():
    """N1: freshly-created 6-player game must have pairwise-unique names.

    If this ever fails, every subsequent get_player_by_name lookup is a
    coin-flip — silent dispatch corruption is guaranteed.
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("旅行者·荧", "villager"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("行秋", "witch"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N1_name_unique",
    )
    names = [p.name for p in game.players]
    assert len(set(names)) == len(names) == 6, (
        f"Name uniqueness violated: {names} (duplicates would break "
        f"get_player_by_name at backend/models/game.py:131-136)"
    )


def test_N2_seer_returned_name_resolves_to_same_player_the_agent_picked():
    """N2: after seer_investigate, app.py's get_player_by_name(returned_name)
    must return the SAME player whose name was written to knowledge_summary,
    with the SAME is_werewolf() result as the label in memory.

    Exercises the full agent → name → lookup chain for a 2-human 6-player
    setup (with both 旅行者·空 and 旅行者·荧 alive). Invariant should hold
    today for exact-match tags.
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("旅行者·空", "seer"),       # human-named
            ("旅行者·荧", "villager"),   # human-named
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("行秋", "witch"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N2_seer_name_id_dispatch",
    )
    agent = _new_agent()
    seer = game.players[0]

    # Capture whatever update_knowledge_summary is invoked with (there
    # should be exactly one call inside seer_investigate).
    km = agent.__class__  # just to silence linter for km if unused
    captured: list[dict] = []
    original_update = KnowledgeManager.update_knowledge_summary

    def spy_update(self, *, player_id, role, operation, section, content="", old_content=""):
        captured.append(
            {
                "player_id": player_id,
                "role": role,
                "section": section,
                "content": content,
            }
        )
        return original_update(
            self,
            player_id=player_id,
            role=role,
            operation=operation,
            section=section,
            content=content,
            old_content=old_content,
        )

    response = "<target>旅行者·空</target>"  # seer checking self is nonsensical;
    # but the loop filters out seer.id (alive_others), so this will fall into
    # the substring loop, fail to match (seer excluded), and fall to random.
    # Use a concrete non-self target instead:
    response = "<target>菲谢尔</target>"

    with patch.object(agent, "_llm_call", return_value=response), \
            patch.object(KnowledgeManager, "update_knowledge_summary", spy_update):
        returned_name = agent.seer_investigate(seer, game)

    # There must be exactly one captured write to the seer's knowledge summary.
    seer_writes = [c for c in captured if c["player_id"] == seer.id and c["role"] == "seer"]
    assert len(seer_writes) == 1, f"Expected 1 seer knowledge write, got {len(seer_writes)}: {captured}"
    written = seer_writes[0]["content"]

    # Parse the name + label the agent wrote.
    import re
    mm = re.search(r"\*\*([^\*]+)\*\*:\s*(WEREWOLF|GOOD)", written)
    assert mm, f"Could not parse name/label from memory write: {written!r}"
    agent_name_written = mm.group(1)
    agent_label_written = mm.group(2)

    assert returned_name is not None, "seer_investigate returned None"

    # Simulate app.py:1585 lookup.
    resolved = game.get_player_by_name(returned_name)
    assert resolved is not None, (
        f"get_player_by_name({returned_name!r}) returned None — dispatch would drop silently "
        f"at app.py:1585-1587"
    )

    # THE INVARIANT: memory-written name matches resolved player name, and
    # memory label matches resolved player's role polarity.
    assert resolved.name == agent_name_written, (
        f"DISPATCH MISMATCH: memory says '{agent_name_written}' but "
        f"get_player_by_name({returned_name!r}) returned {resolved.name!r}"
    )
    expected_label = "WEREWOLF" if resolved.is_werewolf() else "GOOD"
    assert agent_label_written == expected_label, (
        f"LABEL MISMATCH: memory says {agent_name_written}={agent_label_written!r} but "
        f"resolved.is_werewolf()={resolved.is_werewolf()} → should be {expected_label!r}"
    )


def test_N3_process_night_seer_commits_same_player_as_agent_pick():
    """N3: end-to-end. seer_investigate → get_player_by_name → process_night_seer.
    Then assert:
      a) game.seer_checked points to player X
      b) knowledge_summary contains exactly X's name with X.is_werewolf() label
      c) generate_night_record (via write_night_records, which re-reads
         game.seer_checked) produces the SAME target + SAME label
    Any divergence here is the smoking-gun dispatch bug the user theorises.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("旅行者·空", "seer"),
            ("旅行者·荧", "villager"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("行秋", "witch"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N3_seer_full_pipeline",
    )
    agent = _new_agent()
    seer = game.players[0]

    # Seer intends to check 旅行者·空 (wait — that's himself). Use 旅行者·荧.
    response = "<target>旅行者·荧</target>"
    with patch.object(agent, "_llm_call", return_value=response):
        returned_name = agent.seer_investigate(seer, game)

    assert returned_name == "旅行者·荧", f"Exact-match <target> should return the name, got {returned_name!r}"

    target = game.get_player_by_name(returned_name)
    assert target is not None

    engine.process_night_seer(game, target.id)

    # (a) engine state
    assert game.seer_checked == target.id
    # The checked player is a villager (by setup above) → should label GOOD.
    expected_label = "WEREWOLF" if target.is_werewolf() else "GOOD"

    # (b) knowledge summary content
    storage = MemoryStorage(seer.id, MEMORY_BASE, game.game_id)
    summary = storage.read_knowledge_summary("seer")
    pairs = _parse_verified_lines(summary)
    assert (target.name, expected_label) in pairs, (
        f"knowledge_summary '### 验证身份' missing expected pair "
        f"({target.name!r}, {expected_label!r}). Found: {pairs}"
    )
    # Crucially: no OTHER name should appear with an opposite label that
    # contradicts reality. Specifically, 旅行者·荧 must not be labelled WEREWOLF
    # (she's a villager) anywhere in the verified section.
    for (n, r) in pairs:
        p = game.get_player_by_name(n)
        assert p is not None, f"Verified section references unknown name {n!r}"
        truth = "WEREWOLF" if p.is_werewolf() else "GOOD"
        assert r == truth, (
            f"DISPATCH SMOKING GUN: memory labels {n}={r!r} but real role gives {truth!r}. "
            f"This would reproduce the live Render bug."
        )

    # (c) night record (regenerated from game.seer_checked)
    snapshot = game.snapshot_round_data()
    engine.write_night_records(game, round_num=game.round_number, snapshot=snapshot)
    records = storage.read_night_record(num_nights=3)
    assert records, "No night record written"
    record_content = records[-1]
    assert target.name in record_content, (
        f"night_record missing target {target.name!r}:\n{record_content}"
    )
    assert expected_label in record_content, (
        f"night_record missing expected label {expected_label!r}:\n{record_content}"
    )


@pytest.mark.xfail(
    reason="Duplicate-name collision is silent: no WARNING is ever logged. "
    "Agent's substring loop and get_player_by_name BOTH first-match by "
    "game.players order so they happen to agree today, but any refactor to "
    "either traversal order would desynchronize silently. Defense-in-depth "
    "requires a loud collision log at models/game.py:131-136."
)
def test_N4_duplicate_player_name_collision_is_loud(caplog):
    """N4: force two players to share a name ('旅行者' with no suffix) and
    then run seer_investigate → get_player_by_name. Today both sides happen
    to agree (accidental first-match consistency) but NEITHER side logs a
    collision warning — which is brittle. Assert a WARNING is logged so
    future traversal-order changes can't silently desync agent vs engine.
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("钟离", "seer"),
            ("旅行者·荧", "villager"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("行秋", "witch"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N4_duplicate_names",
    )
    # Mutate two players to share a name — this is the degenerate case.
    game.players[1].name = "旅行者"
    game.players[3].name = "旅行者"   # collision: villager (alive) AND werewolf (alive)

    agent = _new_agent()
    seer = game.players[0]

    response = "<target>旅行者</target>"  # ambiguous
    with caplog.at_level(logging.WARNING):
        with patch.object(agent, "_llm_call", return_value=response):
            returned_name = agent.seer_investigate(seer, game)
        # Also run the lookup that app.py would do — collision should be loud here too.
        resolved = game.get_player_by_name(returned_name) if returned_name else None

    assert returned_name is not None
    assert resolved is not None

    collision_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and (
            "duplicate" in r.getMessage().lower()
            or "collision" in r.getMessage().lower()
            or "ambiguous" in r.getMessage().lower()
            or "multiple" in r.getMessage().lower()
        )
    ]
    assert collision_warnings, (
        "Duplicate-name dispatch path is silent. Expected WARNING about "
        "multiple players sharing a name, but none was logged. "
        f"caplog={[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


def test_N5_werewolf_kill_name_vs_id_invariant():
    """N5: werewolf_collaborate returns {"target": name}; app.py then does
    get_player_by_name(name) and process_night_werewolf(target.id). The name
    logged into werewolf_shared.strategy.md MUST match the player whose id
    ends up in game.night_kills.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("旅行者·空", "seer"),
            ("旅行者·荧", "villager"),
            ("行秋", "witch"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N5_wolf_name_id",
        phase=GamePhase.NIGHT_WEREWOLF,
    )
    agent = _new_agent()

    # Two wolves → collab path; LLM returns JSON with final_target = 旅行者·荧.
    response = (
        '{"wolf1_suggestion":"旅行者·荧","wolf1_reasoning":"R1",'
        '"wolf2_suggestion":"旅行者·荧","wolf2_reasoning":"R2",'
        '"final_target":"旅行者·荧","team_reasoning":"T",'
        '"wolf1_daytime_role":"X","wolf2_daytime_role":"Y",'
        '"strategy_summary":"S"}'
    )
    with patch.object(agent, "_llm_call", return_value=response):
        collab = agent.werewolf_collaborate(game)

    target_name = collab.get("target")
    assert target_name == "旅行者·荧", f"Wolf collab returned {target_name!r}"

    # Check that werewolf_shared strategy.md mentions this exact target.
    from backend.memory.storage import MemoryStorage as _MS
    shared_storage = _MS("werewolf_shared", MEMORY_BASE, game.game_id)
    strategy_md = shared_storage.read_file("strategy.md")
    assert target_name in strategy_md, (
        f"werewolf_shared/strategy.md missing target name {target_name!r}:\n{strategy_md}"
    )

    # Also check per-wolf 团队分工 section embeds the same target.
    for wolf in [p for p in game.players if p.role == "werewolf"]:
        wolf_storage = MemoryStorage(wolf.id, MEMORY_BASE, game.game_id)
        wolf_summary = wolf_storage.read_knowledge_summary("werewolf")
        assert target_name in wolf_summary, (
            f"wolf {wolf.name}'s knowledge_summary missing target {target_name!r}:\n{wolf_summary}"
        )

    # Simulate app.py dispatch.
    resolved = game.get_player_by_name(target_name)
    assert resolved is not None, (
        f"app.py:1578 get_player_by_name({target_name!r}) None — silent drop"
    )
    engine.process_night_werewolf(game, resolved.id)

    # Invariant: the id in night_kills must point to the player named in memory.
    assert len(game.night_kills) == 1
    killed_id = game.night_kills[0]
    killed_player = game.get_player_by_id(killed_id)
    assert killed_player is not None
    assert killed_player.name == target_name, (
        f"DISPATCH MISMATCH: werewolf_shared says kill={target_name!r} but "
        f"process_night_werewolf committed id={killed_id!r} (name={killed_player.name!r})"
    )


def test_N6_witch_poison_name_vs_id_invariant():
    """N6: witch_action returns {"action":"poison","target":name}; app.py
    looks up by name, passes target.id to process_night_witch(poison_target_id=).
    The name stored in witch's knowledge_json history must correspond to the
    player whose id reaches process_night_witch.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("行秋", "witch"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("旅行者·空", "seer"),
            ("旅行者·荧", "villager"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N6_witch_name_id",
        phase=GamePhase.NIGHT_WITCH,
    )
    # Simulate that wolves already killed someone (else save/poison semantics
    # don't quite fire the same way); pick 迪卢克 as the wolf victim.
    game.night_kills.append(game.players[5].id)

    agent = _new_agent()
    witch = game.players[0]
    killed_player = game.get_player_by_id(game.night_kills[0])

    # LLM: witch decides to poison 胡桃 (a wolf).
    response = '{"action": "poison", "target": "胡桃", "reasoning": "he\'s suspicious"}'
    with patch.object(agent, "_llm_call", return_value=response):
        action = agent.witch_action(witch, killed_player, game)

    assert action.get("action") == "poison"
    target_name = action.get("target")
    assert target_name == "胡桃"

    # Read witch's knowledge_json to verify the action was recorded with this name.
    # Note: the JSON stores history under 'usage_history' (see backend/memory/knowledge.py
    # update_knowledge_json → storage.update_knowledge_json, which appends to usage_history).
    km = KnowledgeManager(MEMORY_BASE, game.game_id)
    witch_json = km.read_knowledge_json(witch.id, "witch")
    history = []
    if isinstance(witch_json, dict):
        history = witch_json.get("usage_history") or witch_json.get("history") or []
    poison_entries = [h for h in history if isinstance(h, dict) and h.get("action") == "poison"]
    assert poison_entries, f"Witch knowledge_json missing poison history entry: {witch_json}"
    assert poison_entries[-1].get("target") == target_name, (
        f"Witch history logged target {poison_entries[-1].get('target')!r} ≠ agent return {target_name!r}"
    )

    # Simulate app.py:1602-1611 dispatch.
    poison_target = game.get_player_by_name(target_name)
    assert poison_target is not None, f"get_player_by_name({target_name!r}) None"
    engine.process_night_witch(game, use_save=False, use_poison=True, poison_target_id=poison_target.id)

    # Invariant: game.poisoned_player must point to the same player named in memory.
    assert game.poisoned_player == poison_target.id
    poisoned_player_obj = game.get_player_by_id(game.poisoned_player)
    assert poisoned_player_obj.name == target_name, (
        f"DISPATCH MISMATCH: witch history says poison={target_name!r} but "
        f"game.poisoned_player id {game.poisoned_player!r} → name {poisoned_player_obj.name!r}"
    )


def test_N7_guard_protect_name_vs_id_invariant():
    """N7: guard_protect returns a name; app.py looks up and
    process_night_guard(target.id). The name stored in guard's knowledge
    summary must match the player whose id ends up as game.guarded_player.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("迪卢克", "guard"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("旅行者·空", "seer"),
            ("旅行者·荧", "villager"),
            ("行秋", "witch"),
        ],
        game_id="test_N7_guard_name_id",
        phase=GamePhase.NIGHT_GUARD,
    )
    agent = _new_agent()
    guard = game.players[0]

    # Guard chooses to protect 旅行者·荧 (exact match).
    response = "<target>旅行者·荧</target>"
    with patch.object(agent, "_llm_call", return_value=response):
        returned_name = agent.guard_protect(guard, game)

    assert returned_name == "旅行者·荧"

    # Memory must say guard protected this exact name.
    storage = MemoryStorage(guard.id, MEMORY_BASE, game.game_id)
    summary = storage.read_knowledge_summary("guard")
    assert returned_name in summary, (
        f"Guard knowledge_summary missing protected name {returned_name!r}:\n{summary}"
    )

    # Simulate app.py:1568-1570.
    resolved = game.get_player_by_name(returned_name)
    assert resolved is not None
    engine.process_night_guard(game, resolved.id)

    # Invariant: guarded_player id → name must equal the name in memory.
    assert game.guarded_player == resolved.id
    guarded_obj = game.get_player_by_id(game.guarded_player)
    assert guarded_obj.name == returned_name, (
        f"DISPATCH MISMATCH: guard memory says protect={returned_name!r} but "
        f"game.guarded_player id {game.guarded_player!r} → name {guarded_obj.name!r}"
    )


def test_N8_concurrent_seer_investigate_produces_inconsistent_memory_vs_engine():
    """N8: Concurrent smoking gun. Two concurrent seer_investigate → lookup →
    process_night_seer cycles for the SAME seer in the SAME round, but each
    call picks a different target (one villager, one werewolf).

    Expected final state (if no concurrency bug):
      - game.seer_checked and game.seer_checks are consistent: whichever
        target_id wins, its entry in seer_checks must say WEREWOLF iff that
        id is a werewolf.
      - knowledge_summary may accumulate BOTH entries (append-only writes)
        but EACH entry must correctly reflect its own target's truth role.
      - generate_night_record (via write_night_records) sees last-writer-wins
        game.seer_checked → ONE target + CORRECT label for that target.

    What we FAIL on:
      - knowledge_summary ever contains '旅行者·空: WEREWOLF' when 旅行者·空
        is a villager, OR
      - generate_night_record's label contradicts the actual role of the
        player at game.seer_checked.

    This reproduces the shape of the Render symptom: two concurrent memory
    writes both grounded in correct per-call state produce an internally
    consistent accumulated memory — if they don't, we've found the bug.
    """
    _clean_memory_base()
    engine = GameEngine(MEMORY_BASE)
    game = _make_game(
        [
            ("钟离", "seer"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("旅行者·空", "villager"),
            ("旅行者·荧", "villager"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N8_concurrent_seer",
    )
    agent = _new_agent()
    seer = game.players[0]

    villager_name = "旅行者·空"
    wolf_name = "胡桃"

    call_counter = {"n": 0}
    call_lock = threading.Lock()

    def alternating_llm(prompt, *, max_tokens=500, temperature=0.7, system_prompt=None, **kwargs):
        with call_lock:
            call_counter["n"] += 1
            n = call_counter["n"]
        # Odd call → villager; even call → wolf.
        name = villager_name if n % 2 == 1 else wolf_name
        time.sleep(0.02)  # widen the race window
        return f"<target>{name}</target>"

    def run_full_pipeline():
        # Mirror app.py's dispatch: seer_investigate → get_player_by_name → process_night_seer.
        name = agent.seer_investigate(seer, game)
        target = game.get_player_by_name(name)
        if target:
            engine.process_night_seer(game, target.id)
        return name

    with patch.object(agent, "_llm_call", side_effect=alternating_llm):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(run_full_pipeline)
            f2 = pool.submit(run_full_pipeline)
            r1 = f1.result(timeout=20)
            r2 = f2.result(timeout=20)

    # Post-run audit.
    storage = MemoryStorage(seer.id, MEMORY_BASE, game.game_id)
    summary = storage.read_knowledge_summary("seer")
    pairs = _parse_verified_lines(summary)

    # (1) Each row in knowledge_summary must be internally truthful.
    truth_role = {p.name: p.role for p in game.players}
    misrows = []
    for (n, r) in pairs:
        role = truth_role.get(n)
        if role is None:
            misrows.append(f"unknown name '{n}' in summary")
            continue
        expected = "WEREWOLF" if role == "werewolf" else "GOOD"
        if r != expected:
            misrows.append(f"'{n}' (role={role}) labelled {r}, expected {expected}")
    assert not misrows, (
        "SMOKING GUN (concurrent per-call truth mismatch): even though each "
        "seer_investigate computes label from its own target.is_werewolf(), "
        "the accumulated memory shows contradictions:\n  - "
        + "\n  - ".join(misrows)
        + f"\nParsed pairs: {pairs}"
    )

    # (2) game.seer_checked is last-writer-wins; seer_checks[id] must match.
    last_id = game.seer_checked
    assert last_id is not None
    last_label = game.seer_checks.get(last_id)
    last_player = game.get_player_by_id(last_id)
    expected_last = "WEREWOLF" if last_player.is_werewolf() else "GOOD"
    assert last_label == expected_last, (
        f"SMOKING GUN: game.seer_checks[{last_id!r}]={last_label!r} but "
        f"player {last_player.name!r} is_werewolf()={last_player.is_werewolf()}"
    )

    # (3) generate_night_record re-reads game.seer_checked → one correct entry.
    snapshot = game.snapshot_round_data()
    engine.write_night_records(game, round_num=game.round_number, snapshot=snapshot)
    records = storage.read_night_record(num_nights=3)
    assert records
    record = records[-1]
    assert last_player.name in record, (
        f"night_record missing final target name {last_player.name!r}:\n{record}"
    )
    assert expected_last in record, (
        f"night_record missing correct label {expected_last!r} for {last_player.name!r}:\n{record}"
    )

    # Sanity: both targets were attempted.
    assert {r1, r2} == {villager_name, wolf_name}, (
        f"Expected both targets to be probed exactly once; got r1={r1!r}, r2={r2!r}"
    )


@pytest.mark.xfail(
    reason="Documents a race where game.players mutates between agent's "
    "is_werewolf() read and the memory write. Today Python's GIL protects "
    "the two statements at the bytecode level but not across the "
    "update_knowledge_summary I/O boundary — label can stick to stale role."
)
def test_N8b_role_flip_mid_call_label_mismatch():
    """N8b: Model a pathological race where the target's role changes between
    the `is_werewolf()` read and the memory write. Mirrors user's question:
    can knowledge_summary end up with '旅行者·空: WEREWOLF' even though
    roles never collide in the *final* game.players?
    """
    _clean_memory_base()
    game = _make_game(
        [
            ("钟离", "seer"),
            ("胡桃", "werewolf"),
            ("菲谢尔", "werewolf"),
            ("旅行者·空", "villager"),   # Will be flipped to werewolf mid-call.
            ("旅行者·荧", "villager"),
            ("迪卢克", "guard"),
        ],
        game_id="test_N8b_role_flip",
    )
    agent = _new_agent()
    seer = game.players[0]
    victim = game.players[3]  # 旅行者·空

    # Patch is_werewolf ON THE TARGET INSTANCE ONLY: read once returns True,
    # then role "reverts" on disk. This simulates the race.
    original_is_werewolf = victim.__class__.is_werewolf

    call_count = {"n": 0}

    def flipping(self):
        if self is victim:
            call_count["n"] += 1
            # First read during agent pick says WEREWOLF (race winner).
            if call_count["n"] == 1:
                return True
            # Subsequent reads (via engine / generators) see truth (villager).
            return False
        return original_is_werewolf(self)

    response = "<target>旅行者·空</target>"
    with patch.object(victim.__class__, "is_werewolf", flipping), \
            patch.object(agent, "_llm_call", return_value=response):
        returned_name = agent.seer_investigate(seer, game)

    assert returned_name == victim.name

    storage = MemoryStorage(seer.id, MEMORY_BASE, game.game_id)
    summary = storage.read_knowledge_summary("seer")
    pairs = _parse_verified_lines(summary)

    # After the race resolves, victim is still a villager. Memory should not
    # claim otherwise. If this FAILS, we've reproduced the mid-call flip bug.
    bad = [(n, r) for (n, r) in pairs if n == victim.name and r == "WEREWOLF"]
    assert not bad, (
        f"Race-induced label mismatch: knowledge_summary says {bad} but "
        f"{victim.name}'s real role is villager. "
        f"This is the race the user is worried about — if reproducible in "
        f"production, same symptom as the Render bug."
    )


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
