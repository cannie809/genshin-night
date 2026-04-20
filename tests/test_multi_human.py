"""End-to-end multi-human correctness tests.

Covers the S1/S2/S3 bugs that motivated the v2.3 multi-human rewrite:

S1  One human clicking /advance-night must NOT unilaterally open the day
    for everyone else; morning-ack quorum gates /advance-discussion.
S2  Speech submissions must be serialized by current_speaker_id: a non-
    current human sending /human-speak gets 409 NOT_YOUR_TURN instead
    of polluting the event log out of order.
S3  Vote resolution waits for all alive humans before running AI votes
    and returning a tally — intermediate responses expose only a blind
    progress counter, never individual votes.

Plus a single-player regression test: 1 human + 5 AI must still work
end-to-end with no changes to the user-facing flow.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Project root on sys.path so `from backend.app import app` works when this
# test is run standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """Fresh TestClient per test. Resets the room manager's rooms in-place
    and clears the `games` dict so each test starts from a known state.
    Also points the engine's memory_base at tmp_path to isolate profiler
    side-effects."""
    from backend import app as app_mod
    from backend.models import RoomStatus

    app_mod.game_engine.memory_base = tmp_path
    app_mod.games.clear()
    # In-place reset of each room (avoid re-instantiating manager, which
    # would require re-wiring the router).
    for room in app_mod._room_manager._rooms.values():
        room.players = []
        room.host_identity = None
        room.status = RoomStatus.WAITING
        room.game_id = None
        room.started_at = None
        room.mode = "classic_6_witch"
    return TestClient(app_mod.app)


def _auth(identity: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {identity}"}


def _create_two_human_room(client) -> tuple[str, str, str, dict]:
    """Spin up a room with 2 humans. Returns (alice_id, bob_id, game_id,
    game_state)."""
    alice = str(uuid.uuid4())
    bob = str(uuid.uuid4())
    r = client.post(
        "/api/rooms/room_0/join", json={"display_name": "Alice"}, headers=_auth(alice)
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/rooms/room_0/join", json={"display_name": "Bob"}, headers=_auth(bob)
    )
    assert r.status_code == 200, r.text
    # Non-host must ready up before the host can /start.
    r = client.post(
        "/api/rooms/room_0/set-ready", json={"ready": True}, headers=_auth(bob)
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/rooms/room_0/start", headers=_auth(alice))
    assert r.status_code == 200, r.text
    game_id = r.json()["game_id"]
    assert game_id is not None
    r = client.get(f"/api/game/state/{game_id}", headers=_auth(alice))
    assert r.status_code == 200
    return alice, bob, game_id, r.json()


def _find_human_player_id(state: dict, identity: str) -> str:
    """Return the player_id (slot) belonging to the given identity."""
    for p in state["players"]:
        if p.get("is_human") and p.get("identity") == identity:
            return p["id"]
    raise AssertionError(f"identity {identity} not found in game players")


# ---------------------- S1: morning-ack quorum gate ----------------------


def test_s1_single_ack_does_not_unlock_discussion(client):
    """A single /advance-night call must NOT let /advance-discussion run
    AI speeches before the second human acks."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    # Force the phase to DAY_DISCUSSION to skip all night LLM calls, since
    # we only care about the ack gate for this test.
    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.phase = GamePhase.DAY_DISCUSSION
    # Mark morning already processed so advance_night doesn't re-run it.
    from backend.models import GameEvent

    g.events.append(
        GameEvent(
            type="morning_safe",
            round=g.round_number,
            phase="DAY_DISCUSSION",
            message="(test setup)",
        )
    )

    # Alice acks morning via explicit /enter-day.
    r = client.post(
        "/api/game/enter-day",
        json={"game_id": game_id, "player_id": alice_pid},
        headers=_auth(alice),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["morning_acks"] == 1
    assert body["morning_acks_needed"] == 2
    assert body["caller_morning_acked"] is True

    # Alice tries to advance discussion. The morning quorum is 1/2, so
    # the gate must hold — no AI speeches should land yet.
    speeches_before = sum(1 for e in body["events"] if e["type"] == "speech")
    r = client.post(
        "/api/game/advance-discussion",
        json={"game_id": game_id, "player_id": alice_pid},
        headers=_auth(alice),
    )
    assert r.status_code == 200
    body2 = r.json()
    speeches_after = sum(1 for e in body2["events"] if e["type"] == "speech")
    assert speeches_after == speeches_before, (
        "advance-discussion must not run AI speeches before morning-ack quorum"
    )

    # Bob acks. From Bob's viewpoint, he has now acked.
    r = client.post(
        "/api/game/enter-day",
        json={"game_id": game_id, "player_id": bob_pid},
        headers=_auth(bob),
    )
    assert r.status_code == 200
    body3 = r.json()
    assert body3["morning_acks"] == 2
    assert body3["caller_morning_acked"] is True


def test_s1_non_participant_cannot_ack(client):
    """An identity that never joined the room must not be able to pass
    the caller gate on /enter-day."""
    alice, bob, game_id, state = _create_two_human_room(client)
    intruder = str(uuid.uuid4())

    r = client.post(
        "/api/game/enter-day",
        json={"game_id": game_id, "player_id": state["players"][0]["id"]},
        headers=_auth(intruder),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "NOT_A_PARTICIPANT"


# ---------------------- S2: turn token on human-speak ----------------------


def test_s2_wrong_speaker_gets_409(client):
    """If Bob submits /human-speak while current_speaker_id points at
    Alice, Bob must get 409 NOT_YOUR_TURN and the event log must NOT
    gain Bob's speech."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.phase = GamePhase.DAY_DISCUSSION
    # Deterministic speech order: Alice first, Bob later. Use direct
    # construction so we don't depend on shuffle RNG.
    g.speech_order = [alice_pid, bob_pid] + [
        p.id for p in g.players if p.id not in (alice_pid, bob_pid)
    ]
    g.current_speaker_id = alice_pid
    # Pre-ack morning so the speech gate is solely about turn.
    g.morning_acks = {alice, bob}

    # Bob jumps the queue.
    events_before = len(g.events)
    r = client.post(
        "/api/game/human-speak",
        json={"game_id": game_id, "player_id": bob_pid, "content": "抢发言"},
        headers=_auth(bob),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "NOT_YOUR_TURN"
    assert r.json()["detail"]["current_speaker_id"] == alice_pid
    # No event should have been appended by the failed submission.
    assert len(g.events) == events_before, "failed speak must not mutate events"

    # Alice speaks successfully.
    r = client.post(
        "/api/game/human-speak",
        json={"game_id": game_id, "player_id": alice_pid, "content": "我先说"},
        headers=_auth(alice),
    )
    assert r.status_code == 200, r.text
    # Turn token moved.
    assert g.current_speaker_id != alice_pid


def test_s2_wrong_identity_gets_403(client):
    """Even if Bob submits with alice_pid (trying to impersonate Alice),
    the identity-to-slot check must reject with 403."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.phase = GamePhase.DAY_DISCUSSION
    g.current_speaker_id = alice_pid
    g.morning_acks = {alice, bob}
    g.speech_order = [p.id for p in g.players]

    r = client.post(
        "/api/game/human-speak",
        json={"game_id": game_id, "player_id": alice_pid, "content": "X"},
        headers=_auth(bob),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "NOT_YOUR_SLOT"


# ---------------------- S3: blind vote barrier ----------------------


def test_s3_vote_waits_for_all_humans(client):
    """When Alice votes, the response must be blind progress (1/2),
    AI votes must NOT be run, and no vote_result event must land until
    Bob also votes."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.phase = GamePhase.DAY_VOTE
    # Pick any alive target (first non-Alice, non-Bob alive player).
    target_id = next(
        p.id for p in g.alive_players if p.id not in (alice_pid, bob_pid)
    )

    # Alice votes first.
    r = client.post(
        "/api/game/vote",
        json={"game_id": game_id, "player_id": alice_pid, "target_id": target_id},
        headers=_auth(alice),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Waiting indicator present; no individual vote info leaked.
    assert body["data"]["waiting"] is True
    # `total` in vote_progress reflects all alive voters (humans + AI),
    # matching the engine's blind counter semantics. UI renders "已投票 N/M"
    # without distinguishing who the voters are.
    # vote_progress counts alive humans only (AI vote is prefetched but
    # not applied to game.votes until all humans submit — counting AIs
    # would make the UI show "2/6 (still missing AIs)" which is a lie).
    assert body["data"]["submitted"] == 1
    alive_humans = [p for p in g.alive_players if p.is_human]
    assert body["data"]["total"] == len(alive_humans)
    # Only Alice's vote recorded so far.
    assert alice_pid in g.votes
    assert bob_pid not in g.votes
    # AI votes have NOT been computed yet (only Alice's vote in the map).
    ai_votes_present = [pid for pid in g.votes if pid != alice_pid]
    assert not ai_votes_present, (
        f"AI votes must not be computed before all humans vote, got {ai_votes_present}"
    )
    # No vote_result event yet.
    assert not any(e.type == "vote_result" for e in g.events)


def test_s3_vote_returns_blind_progress_in_state(client):
    """GameStateResponse must expose vote_submitted/vote_total but never
    voter→target mapping."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.phase = GamePhase.DAY_VOTE
    target_id = next(
        p.id for p in g.alive_players if p.id not in (alice_pid, bob_pid)
    )
    client.post(
        "/api/game/vote",
        json={"game_id": game_id, "player_id": alice_pid, "target_id": target_id},
        headers=_auth(alice),
    )

    # Fetch state as Bob — must see blind counter, no per-voter events.
    r = client.get(
        f"/api/game/state/{game_id}",
        params={"player_id": bob_pid},
        headers=_auth(bob),
    )
    body = r.json()
    assert body["vote_submitted"] == 1
    alive_humans = [p for p in g.alive_players if p.is_human]
    assert body["vote_total"] == len(alive_humans)
    assert not any(e["type"] == "vote_result" for e in body["events"])


# ---------------------- Single-player non-regression ----------------------


def test_singleplayer_one_human_full_setup(client):
    """1 human + 5 AI game must still boot and reach NIGHT_WEREWOLF with a
    single human in the expected slot. This is the non-regression canary
    for solo play."""
    solo = str(uuid.uuid4())
    r = client.post(
        "/api/rooms/room_0/join",
        json={"display_name": "Solo"},
        headers=_auth(solo),
    )
    assert r.status_code == 200
    r = client.post("/api/rooms/room_0/start", headers=_auth(solo))
    assert r.status_code == 200, r.text
    game_id = r.json()["game_id"]
    r = client.get(f"/api/game/state/{game_id}", headers=_auth(solo))
    assert r.status_code == 200
    state = r.json()
    humans = [p for p in state["players"] if p.get("is_human")]
    assert len(humans) == 1
    # Single-player quorum is 1.
    assert state["morning_acks_needed"] == 1
    # No pending morning acks yet at the very start.
    assert state["morning_acks"] == 0
    assert state["phase"] == "NIGHT_WEREWOLF"


def test_singleplayer_ack_quorum_is_one(client):
    """With one human, a single /enter-day call should bring morning_acks
    to quorum (1/1) — single-player non-regression."""
    solo = str(uuid.uuid4())
    client.post(
        "/api/rooms/room_0/join",
        json={"display_name": "Solo"},
        headers=_auth(solo),
    )
    r = client.post("/api/rooms/room_0/start", headers=_auth(solo))
    game_id = r.json()["game_id"]
    state = client.get(
        f"/api/game/state/{game_id}", headers=_auth(solo)
    ).json()
    solo_pid = _find_human_player_id(state, solo)

    from backend.app import games
    from backend.models import GamePhase, GameEvent

    g = games[game_id]
    g.phase = GamePhase.DAY_DISCUSSION
    g.events.append(
        GameEvent(
            type="morning_safe",
            round=g.round_number,
            phase="DAY_DISCUSSION",
            message="(test setup)",
        )
    )
    r = client.post(
        "/api/game/enter-day",
        json={"game_id": game_id, "player_id": solo_pid},
        headers=_auth(solo),
    )
    assert r.status_code == 200, r.text
    assert r.json()["morning_acks"] == 1
    assert r.json()["morning_acks_needed"] == 1
    assert r.json()["caller_morning_acked"] is True


# ---------------------- Wolf-kill barrier (2 human wolves) ----------------------


def test_wolf_kill_waits_for_both_human_wolves(client):
    """With two human werewolves, the first to submit werewolf_kill must
    not immediately commit the kill — we wait for the second wolf's
    intent, then random-pick among their targets."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    # Force both humans to be wolves + set NIGHT_WEREWOLF.
    for p in g.players:
        p.role = "villager"
    g.get_player_by_id(alice_pid).role = "werewolf"
    g.get_player_by_id(bob_pid).role = "werewolf"
    g.phase = GamePhase.NIGHT_WEREWOLF
    g.night_acks = {alice, bob}  # both humans already clicked 进入夜晚

    victim_a = next(p.id for p in g.alive_players if p.id not in (alice_pid, bob_pid))
    victim_b = next(
        p.id for p in g.alive_players if p.id not in (alice_pid, bob_pid, victim_a)
    )

    # Alice submits her target; kill should NOT commit yet.
    r = client.post(
        "/api/game/night-action",
        json={
            "game_id": game_id,
            "player_id": alice_pid,
            "action_type": "werewolf_kill",
            "target_id": victim_a,
        },
        headers=_auth(alice),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]["waiting"] is True
    assert body["data"]["submitted"] == 1
    assert body["data"]["total"] == 2
    assert g.phase == GamePhase.NIGHT_WEREWOLF, "phase must not advance before both wolves submit"
    assert not g.night_kills, "kill must not commit before both wolves submit"

    # Bob submits — now the kill commits and phase advances.
    r = client.post(
        "/api/game/night-action",
        json={
            "game_id": game_id,
            "player_id": bob_pid,
            "action_type": "werewolf_kill",
            "target_id": victim_b,
        },
        headers=_auth(bob),
    )
    assert r.status_code == 200, r.text
    assert g.night_kills, "kill must commit after both wolves submit"
    # Target should be one of the two submissions.
    assert g.night_kills[0] in (victim_a, victim_b), (
        f"killed {g.night_kills[0]} must be one of the submitted targets {victim_a}, {victim_b}"
    )
    assert g.phase != GamePhase.NIGHT_WEREWOLF, "phase must advance after commit"


@pytest.mark.parametrize(
    "alice_role,bob_role,label",
    [
        # One human is a wolf — the other human is a different role.
        # These are the scenarios where a non-wolf caller's auto-advance
        # would historically have run the AI wolf collab on behalf of the
        # human wolf, which is the bug class the gate defends against.
        ("werewolf", "seer", "wolf+seer"),
        ("werewolf", "witch", "wolf+witch"),
        ("werewolf", "hunter", "wolf+hunter"),
        ("werewolf", "guard", "wolf+guard"),
        ("werewolf", "villager", "wolf+villager"),
        ("seer", "werewolf", "seer+wolf"),
        ("witch", "werewolf", "witch+wolf"),
        ("hunter", "werewolf", "hunter+wolf"),
        ("guard", "werewolf", "guard+wolf"),
        ("villager", "werewolf", "villager+wolf"),
    ],
)
def test_any_role_combo_non_wolf_caller_preserves_wolf_turn(
    client, alice_role, bob_role, label
):
    """Regression (generalized): whenever ANY human is a werewolf, a non-
    wolf human caller's /advance-night must NOT commit an AI wolf collab
    + kill on the human wolf's behalf. Phase stays at NIGHT_WEREWOLF until
    the human wolf submits their own kill intent.

    Why not all 12 combos: when neither human is a wolf, advance-night
    legitimately runs AI (villager/seer/etc. don't gate), which requires
    role-specific knowledge files that our role-reassignment stub skips
    generating. Those pure-AI-wolf paths are covered by single-player
    tests; they aren't the regression case the mixed gate targets."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.get_player_by_id(alice_pid).role = alice_role
    g.get_player_by_id(bob_pid).role = bob_role
    # Ensure at least one AI wolf teammate exists so the gate is meaningful.
    ai_slots = [p for p in g.players if not p.is_human]
    ai_slots[0].role = "werewolf"
    ai_slots[1].role = "witch"
    ai_slots[2].role = "seer"
    ai_slots[3].role = "villager"
    g.phase = GamePhase.NIGHT_WEREWOLF
    g.night_acks = {alice, bob}

    # Call from the non-wolf human to stress the gate.
    non_wolf_id, non_wolf_pid = (
        (alice, alice_pid) if alice_role != "werewolf" else (bob, bob_pid)
    )
    r = client.post(
        "/api/game/advance-night",
        json={"game_id": game_id, "player_id": non_wolf_pid},
        headers=_auth(non_wolf_id),
    )
    assert r.status_code == 200, f"[{label}] {r.text}"
    assert g.phase == GamePhase.NIGHT_WEREWOLF, (
        f"[{label}] non-wolf caller must NOT advance past NIGHT_WEREWOLF"
        f" when a human wolf exists; got phase={g.phase.value}"
    )
    assert not g.night_kills, f"[{label}] no kill should commit yet"


def test_mixed_wolves_seer_human_non_wolf_caller_preserves_human_turns(client):
    """Regression: when Alice is a human wolf (with an AI wolf teammate)
    and Bob is a human seer, a /advance-night call from Bob must NOT
    run the AI wolf's kill on behalf of Alice, and must NOT run the AI
    seer action (Bob is the seer)."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    # Force the role layout: Alice=wolf, Bob=seer, find an AI wolf.
    g.get_player_by_id(alice_pid).role = "werewolf"
    g.get_player_by_id(bob_pid).role = "seer"
    # Make one AI slot a wolf; ensure we have witch/guard/villager/villager.
    ai_slots = [p for p in g.players if not p.is_human]
    ai_slots[0].role = "werewolf"
    ai_slots[1].role = "witch"
    ai_slots[2].role = "guard"
    ai_slots[3].role = "villager"
    g.phase = GamePhase.NIGHT_WEREWOLF
    g.night_acks = {alice, bob}

    # Bob (seer, NOT a wolf) calls /advance-night. The old bug would run
    # AI wolf collab + commit a kill here.
    r = client.post(
        "/api/game/advance-night",
        json={"game_id": game_id, "player_id": bob_pid},
        headers=_auth(bob),
    )
    assert r.status_code == 200, r.text
    assert g.phase == GamePhase.NIGHT_WEREWOLF, (
        f"non-wolf caller must not advance past NIGHT_WEREWOLF (got phase={g.phase.value})"
    )
    assert not g.night_kills, "non-wolf caller must not commit a wolf kill"
    # No werewolf_chat events should have been appended by an AI collab run.
    wolf_chats_before = [e for e in g.events if e.type == "werewolf_chat"]
    assert not wolf_chats_before, (
        f"non-wolf caller must not trigger AI wolf collab events (got {len(wolf_chats_before)})"
    )


def test_night_action_pending_before_night_ack(client):
    """A human action during NIGHT_* before every human has called
    /enter-night must be stored as a pending intent (HTTP 200 with
    pending=true payload) rather than a 409. The intent auto-fires
    when /enter-night completes quorum."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.get_player_by_id(alice_pid).role = "werewolf"
    g.phase = GamePhase.NIGHT_WEREWOLF
    # Neither human has called /enter-night.

    target_id = next(p.id for p in g.alive_players if p.id not in (alice_pid, bob_pid))
    r = client.post(
        "/api/game/night-action",
        json={
            "game_id": game_id,
            "player_id": alice_pid,
            "action_type": "werewolf_kill",
            "target_id": target_id,
        },
        headers=_auth(alice),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["pending"] is True
    assert body["data"]["reason"] == "WAITING_NIGHT_ACK"
    assert body["data"]["action_type"] == "werewolf_kill"
    assert body["data"]["target_id"] == target_id
    # Intent must be stored so /enter-night can replay it.
    alice_identity = g.get_player_by_id(alice_pid).identity
    assert g.wolf_kill_intents.get(alice_identity) == target_id

    # After both humans ack night, the action is allowed.
    client.post(
        "/api/game/enter-night",
        json={"game_id": game_id, "player_id": alice_pid},
        headers=_auth(alice),
    )
    client.post(
        "/api/game/enter-night",
        json={"game_id": game_id, "player_id": bob_pid},
        headers=_auth(bob),
    )
    r = client.post(
        "/api/game/night-action",
        json={
            "game_id": game_id,
            "player_id": alice_pid,
            "action_type": "werewolf_kill",
            "target_id": target_id,
        },
        headers=_auth(alice),
    )
    # Alice alone submitted — she's the lone wolf in this test; kill commits.
    # But the action must NOT 409 anymore.
    assert r.status_code == 200, r.text


def test_enter_night_replays_seer_intent(client):
    """Pre-quorum seer_check click is stored, then /enter-night replays
    it when the second human ack completes quorum. No re-click needed."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    alice_player = g.get_player_by_id(alice_pid)
    alice_player.role = "seer"
    # Pin everyone else to non-seer roles so the replay path is deterministic.
    for p in g.players:
        if p.id == alice_pid:
            continue
        if p.role == "seer":
            p.role = "villager"
    g.phase = GamePhase.NIGHT_SEER

    # Pick a target that's alive and not alice herself.
    target_id = next(p.id for p in g.alive_players if p.id != alice_pid)

    # Alice clicks her seer_check BEFORE either human has ack'd /enter-night.
    r = client.post(
        "/api/game/night-action",
        json={
            "game_id": game_id,
            "player_id": alice_pid,
            "action_type": "seer_check",
            "target_id": target_id,
        },
        headers=_auth(alice),
    )
    assert r.status_code == 200
    assert r.json()["data"]["pending"] is True
    assert g.seer_check_intents[alice_player.identity] == target_id
    # Engine must NOT have committed the check yet.
    assert g.seer_checked is None

    # Alice acks /enter-night → still waiting on Bob.
    client.post(
        "/api/game/enter-night",
        json={"game_id": game_id, "player_id": alice_pid},
        headers=_auth(alice),
    )
    assert g.seer_checked is None

    # Bob acks → quorum reached → _replay_night_intents fires → seer commits.
    client.post(
        "/api/game/enter-night",
        json={"game_id": game_id, "player_id": bob_pid},
        headers=_auth(bob),
    )
    assert g.seer_checked == target_id
    # Phase should have advanced past NIGHT_SEER.
    assert g.phase != GamePhase.NIGHT_SEER


def test_night_action_after_replay_is_idempotent_noop(client):
    """If a human re-clicks their role button after /enter-night has
    already replayed the stored intent, the second click must be a
    no-op — not a double-commit that advances the phase twice."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.get_player_by_id(alice_pid).role = "seer"
    for p in g.players:
        if p.id == alice_pid:
            continue
        if p.role == "seer":
            p.role = "villager"
    g.phase = GamePhase.NIGHT_SEER

    target_id = next(p.id for p in g.alive_players if p.id != alice_pid)
    # Pre-quorum click stores intent.
    client.post(
        "/api/game/night-action",
        json={
            "game_id": game_id,
            "player_id": alice_pid,
            "action_type": "seer_check",
            "target_id": target_id,
        },
        headers=_auth(alice),
    )
    # Complete quorum → replay fires.
    client.post(
        "/api/game/enter-night",
        json={"game_id": game_id, "player_id": alice_pid},
        headers=_auth(alice),
    )
    client.post(
        "/api/game/enter-night",
        json={"game_id": game_id, "player_id": bob_pid},
        headers=_auth(bob),
    )
    phase_after_replay = g.phase
    seer_checked_after_replay = g.seer_checked

    # Alice clicks again — should be recognised as stale and return no-op.
    r = client.post(
        "/api/game/night-action",
        json={
            "game_id": game_id,
            "player_id": alice_pid,
            "action_type": "seer_check",
            "target_id": target_id,
        },
        headers=_auth(alice),
    )
    assert r.status_code == 200
    assert r.json()["data"].get("already_processed") is True
    # Phase and seer_checked must not move.
    assert g.phase == phase_after_replay
    assert g.seer_checked == seer_checked_after_replay


# ---------------------- Dead player can't skip vote ----------------------


def test_host_start_blocked_when_non_host_not_ready(client):
    """Backend must refuse /start when any non-host is not ready. Single-
    player case (solo host, no non-host) still starts trivially."""
    alice = str(uuid.uuid4())
    bob = str(uuid.uuid4())
    client.post(
        "/api/rooms/room_0/join", json={"display_name": "Alice"}, headers=_auth(alice)
    )
    client.post(
        "/api/rooms/room_0/join", json={"display_name": "Bob"}, headers=_auth(bob)
    )
    # Alice is host by default. Bob has NOT clicked 准备.
    r = client.post("/api/rooms/room_0/start", headers=_auth(alice))
    assert r.status_code == 400, r.text
    assert "not all players are ready" in r.json()["detail"]["message"]

    # Bob readies up → start succeeds.
    r = client.post(
        "/api/rooms/room_0/set-ready", json={"ready": True}, headers=_auth(bob)
    )
    assert r.status_code == 200
    r = client.post("/api/rooms/room_0/start", headers=_auth(alice))
    assert r.status_code == 200, r.text


def test_join_finished_room_auto_resets_to_waiting(client):
    """Regression: a room in FINISHED state with a vacant seat must
    accept a new joiner by auto-resetting to WAITING, not return
    ROOM_IN_PROGRESS."""
    alice = str(uuid.uuid4())
    new_player = str(uuid.uuid4())

    client.post("/api/rooms/room_6/join", json={"display_name": "Alice"}, headers=_auth(alice))
    client.post("/api/rooms/room_6/start", headers=_auth(alice))

    from backend.app import _room_manager
    import asyncio
    asyncio.run(_room_manager.mark_game_finished("room_6"))

    # New player joins the (solo Alice + 1 vacant seat) FINISHED room.
    r = client.post(
        "/api/rooms/room_6/join",
        json={"display_name": "Newcomer"},
        headers=_auth(new_player),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "waiting"
    # Existing member Alice's ready flag should be cleared by the reset.
    alice_view = next(p for p in r.json()["players"] if p["identity"] == alice)
    assert alice_view["ready"] is False


def test_leave_empty_room_resets_to_waiting(client):
    """Regression: when every member leaves a FINISHED room, the room
    must auto-reset to WAITING so the lobby shows it as available."""
    alice = str(uuid.uuid4())
    client.post("/api/rooms/room_4/join", json={"display_name": "Alice"}, headers=_auth(alice))
    client.post("/api/rooms/room_4/start", headers=_auth(alice))

    from backend.app import _room_manager
    import asyncio
    asyncio.run(_room_manager.mark_game_finished("room_4"))

    # Alice leaves (she was the sole member) → room should reset.
    r = client.post("/api/rooms/room_4/leave", headers=_auth(alice))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "waiting", f"empty room must reset, got {body['status']}"
    assert body["game_id"] is None
    assert body["players"] == []


def test_host_leaves_transfers_host_to_next_member(client):
    """Regression: when the host leaves (including from a FINISHED room),
    the next-earliest joiner must be auto-promoted to host so they can
    restart the room without first re-joining."""
    alice = str(uuid.uuid4())
    bob = str(uuid.uuid4())
    client.post("/api/rooms/room_5/join", json={"display_name": "Alice"}, headers=_auth(alice))
    client.post("/api/rooms/room_5/join", json={"display_name": "Bob"}, headers=_auth(bob))

    # Verify Alice is host.
    r = client.get("/api/rooms/room_5", headers=_auth(alice)).json()
    assert r["host_identity"] == alice

    # Alice leaves → Bob auto-promoted.
    r = client.post("/api/rooms/room_5/leave", headers=_auth(alice))
    assert r.status_code == 200, r.text
    r = client.get("/api/rooms/room_5", headers=_auth(bob)).json()
    assert r["host_identity"] == bob, f"host transfer failed, got {r['host_identity']}"

    # Bob (now host, alone) can start the game since there are no non-host
    # members to wait on for ready.
    r = client.post("/api/rooms/room_5/start", headers=_auth(bob))
    assert r.status_code == 200, r.text


def test_solo_host_can_start_without_ready(client):
    """Regression: a solo host (single-player) must be able to /start
    immediately — no non-host means no ready quorum to block on."""
    solo = str(uuid.uuid4())
    client.post(
        "/api/rooms/room_1/join", json={"display_name": "Solo"}, headers=_auth(solo)
    )
    r = client.post("/api/rooms/room_1/start", headers=_auth(solo))
    assert r.status_code == 200, r.text
    assert r.json()["game_id"]


def test_ready_player_cannot_change_role(client):
    """Regression: once ready=True, set-role must 400 with a helpful
    message. Toggling ready off must restore the ability to pick."""
    alice = str(uuid.uuid4())
    bob = str(uuid.uuid4())
    client.post(
        "/api/rooms/room_2/join", json={"display_name": "Alice"}, headers=_auth(alice)
    )
    client.post(
        "/api/rooms/room_2/join", json={"display_name": "Bob"}, headers=_auth(bob)
    )
    client.post(
        "/api/rooms/room_2/set-ready", json={"ready": True}, headers=_auth(bob)
    )
    r = client.post(
        "/api/rooms/room_2/set-role",
        json={"preferred_role": "seer"},
        headers=_auth(bob),
    )
    assert r.status_code == 400
    assert "ready" in r.json()["detail"]["message"]
    # Cancel ready → role change allowed.
    client.post(
        "/api/rooms/room_2/set-ready", json={"ready": False}, headers=_auth(bob)
    )
    r = client.post(
        "/api/rooms/room_2/set-role",
        json={"preferred_role": "seer"},
        headers=_auth(bob),
    )
    assert r.status_code == 200


def test_mode_change_clears_ready(client):
    """Regression: host changing mode must reset everyone's ready flag."""
    alice = str(uuid.uuid4())
    bob = str(uuid.uuid4())
    client.post(
        "/api/rooms/room_3/join", json={"display_name": "Alice"}, headers=_auth(alice)
    )
    client.post(
        "/api/rooms/room_3/join", json={"display_name": "Bob"}, headers=_auth(bob)
    )
    # Bob readies.
    client.post(
        "/api/rooms/room_3/set-ready", json={"ready": True}, headers=_auth(bob)
    )
    # Host Alice changes mode.
    r = client.post(
        "/api/rooms/room_3/set-mode",
        json={"mode": "classic_6_hunter"},
        headers=_auth(alice),
    )
    assert r.status_code == 200
    # Bob's ready must be cleared.
    bob_view = next(p for p in r.json()["players"] if p["identity"] == bob)
    assert bob_view["ready"] is False


def test_dead_player_ai_vote_blocked_before_alive_humans_vote(client):
    """Regression: a dead player clicking "观看投票" used to hit /ai-vote,
    which ran AI votes + resolved the round + advanced to NIGHT, even
    though the alive human hadn't voted yet. Now /ai-vote is barrier-gated."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    # Alice is dead, Bob is alive. Phase = DAY_VOTE. Nobody has voted.
    g.get_player_by_id(alice_pid).alive = False
    g.phase = GamePhase.DAY_VOTE

    # Dead Alice pushes /ai-vote (the "观看投票" button).
    r = client.post(
        "/api/game/ai-vote",
        json={"game_id": game_id, "player_id": alice_pid},
        headers=_auth(alice),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"].get("waiting") is True
    # Phase must still be DAY_VOTE — alive Bob hasn't voted.
    assert g.phase == GamePhase.DAY_VOTE
    # No vote_result event.
    assert not any(e.type == "vote_result" for e in g.events)
    # No AI votes should have been recorded either (barrier is before AI vote).
    assert not any(pid for pid in g.votes if pid != alice_pid)

    # Bob votes → vote resolves via normal /vote flow.
    target_id = next(
        p.id for p in g.alive_players if p.id not in (alice_pid, bob_pid)
    )
    r = client.post(
        "/api/game/vote",
        json={"game_id": game_id, "player_id": bob_pid, "target_id": target_id},
        headers=_auth(bob),
    )
    assert r.status_code == 200
    # Now phase has advanced past DAY_VOTE.
    assert g.phase != GamePhase.DAY_VOTE


# ---------------------- Barrier universality (双活/单活/双死) ----------------------


@pytest.mark.parametrize(
    "alive_alice,alive_bob,label",
    [
        (True, True, "both_alive"),
        (True, False, "alice_alive_bob_dead"),
        (False, True, "alice_dead_bob_alive"),
        (False, False, "both_dead"),
    ],
)
def test_morning_ack_barrier_universal(client, alive_alice, alive_bob, label):
    """Morning-ack barrier must hold no matter the alive/dead composition.

    - 双活: both clicks required → 2/2 to unblock
    - 单活: only the alive human's ack is required (dead one excluded); 1/1
    - 双死: both dead humans must still click through (spectator coordination); 2/2

    The backend contract the frontend relies on: /advance-discussion gates
    on morning_acks ⊇ quorum. Quorum is alive humans when any alive, else
    all humans.
    """
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase, GameEvent

    g = games[game_id]
    g.get_player_by_id(alice_pid).alive = alive_alice
    g.get_player_by_id(bob_pid).alive = alive_bob
    g.phase = GamePhase.DAY_DISCUSSION
    g.events.append(
        GameEvent(
            type="morning_safe",
            round=g.round_number,
            phase="DAY_DISCUSSION",
            message="(test)",
        )
    )

    # Expected quorum composition.
    expected_quorum = set()
    if alive_alice:
        expected_quorum.add(alice)
    if alive_bob:
        expected_quorum.add(bob)
    if not expected_quorum:
        # Pure spectator mode — fallback to all humans.
        expected_quorum = {alice, bob}

    # First ack: single click must NOT satisfy a 2-quorum.
    first_id, first_pid = (
        (alice, alice_pid) if alice in expected_quorum else (bob, bob_pid)
    )
    r = client.post(
        "/api/game/enter-day",
        json={"game_id": game_id, "player_id": first_pid},
        headers=_auth(first_id),
    )
    assert r.status_code == 200, f"[{label}] {r.text}"
    body = r.json()
    assert body["morning_acks_needed"] == len(expected_quorum), label
    if len(expected_quorum) > 1:
        assert body["morning_acks"] == 1, label
        # /advance-discussion must still be gated — no speeches.
        speeches_before = sum(1 for e in body["events"] if e["type"] == "speech")
        r = client.post(
            "/api/game/advance-discussion",
            json={"game_id": game_id, "player_id": first_pid},
            headers=_auth(first_id),
        )
        body2 = r.json()
        speeches_after = sum(1 for e in body2["events"] if e["type"] == "speech")
        assert speeches_after == speeches_before, (
            f"[{label}] advance-discussion must not run AI before quorum"
        )

        # Second identity acks → quorum met.
        second_id = (bob if first_id == alice else alice)
        second_pid = bob_pid if first_id == alice else alice_pid
        r = client.post(
            "/api/game/enter-day",
            json={"game_id": game_id, "player_id": second_pid},
            headers=_auth(second_id),
        )
        assert r.status_code == 200, f"[{label}] {r.text}"
        assert r.json()["morning_acks"] == len(expected_quorum), label
    else:
        # Single-quorum: the one click fills it.
        assert body["morning_acks"] == 1, label


@pytest.mark.parametrize(
    "alive_alice,alive_bob,label",
    [
        (True, True, "both_alive"),
        (True, False, "alice_alive_bob_dead"),
        (False, False, "both_dead"),
    ],
)
def test_night_ack_barrier_universal(client, alive_alice, alive_bob, label):
    """Same universal property for the NIGHT-ack barrier."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.get_player_by_id(alice_pid).alive = alive_alice
    g.get_player_by_id(bob_pid).alive = alive_bob
    g.phase = GamePhase.NIGHT_WEREWOLF

    expected_quorum = set()
    if alive_alice:
        expected_quorum.add(alice)
    if alive_bob:
        expected_quorum.add(bob)
    if not expected_quorum:
        expected_quorum = {alice, bob}

    first_id, first_pid = (
        (alice, alice_pid) if alice in expected_quorum else (bob, bob_pid)
    )
    r = client.post(
        "/api/game/enter-night",
        json={"game_id": game_id, "player_id": first_pid},
        headers=_auth(first_id),
    )
    assert r.status_code == 200, f"[{label}] {r.text}"
    body = r.json()
    assert body["night_acks_needed"] == len(expected_quorum), label
    if len(expected_quorum) > 1:
        assert body["night_acks"] == 1, label
        # /advance-night driver must refuse until quorum.
        r = client.post(
            "/api/game/advance-night",
            json={"game_id": game_id, "player_id": first_pid},
            headers=_auth(first_id),
        )
        assert r.status_code == 200
        assert r.json()["phase"] == "NIGHT_WEREWOLF", (
            f"[{label}] phase must not advance before night quorum"
        )


# ---------------------- Human hunter identity gate ----------------------


def test_human_hunter_shoot_requires_hunter_identity(client):
    """Regression: when a human hunter dies at night, another human's
    client used to auto-fire /hunter-shoot with the hunter's player_id
    before the hunter got to choose a target. Backend now rejects with
    403 NOT_THE_HUNTER if the caller's identity doesn't match the
    hunter's identity."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import games
    from backend.models import GamePhase

    g = games[game_id]
    g.get_player_by_id(bob_pid).role = "hunter"
    # Bob is the hunter and has just died.
    g.get_player_by_id(bob_pid).alive = False
    g.phase = GamePhase.HUNTER_SHOOT

    # Alice (not the hunter) tries to shoot on Bob's behalf.
    r = client.post(
        "/api/game/hunter-shoot",
        json={
            "game_id": game_id,
            "player_id": bob_pid,
            "action_type": "hunter_shoot",
        },
        headers=_auth(alice),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "NOT_THE_HUNTER"

    # Bob himself can shoot.
    r = client.post(
        "/api/game/hunter-shoot",
        json={
            "game_id": game_id,
            "player_id": bob_pid,
            "action_type": "hunter_shoot",
        },
        headers=_auth(bob),
    )
    assert r.status_code == 200, r.text


# ---------------------- Speech order shuffle (not rotate) ----------------------


def test_speech_order_shuffles_humans(client, monkeypatch):
    """Regression for the old rotate bug: two adjacent human slots must
    be scatterable by the shuffle. We run the order-setup function many
    times and assert that the humans are NOT always adjacent."""
    alice, bob, game_id, state = _create_two_human_room(client)
    alice_pid = _find_human_player_id(state, alice)
    bob_pid = _find_human_player_id(state, bob)

    from backend.app import _ensure_speech_order, games

    g = games[game_id]
    adjacent_count = 0
    trials = 200
    for _ in range(trials):
        g.speech_order = []
        _ensure_speech_order(g)
        idx_a = g.speech_order.index(alice_pid)
        idx_b = g.speech_order.index(bob_pid)
        if abs(idx_a - idx_b) == 1:
            adjacent_count += 1
    # For 2 humans in 6 slots, P(adjacent) = 2*5 / C(6,2) / 1 = 10/15 = 1/3.
    # Rotate would give P(adjacent) ≈ 1 (always adjacent → 200/200).
    # Shuffle must be well below that.
    assert adjacent_count < int(trials * 0.6), (
        f"humans were adjacent in {adjacent_count}/{trials} trials — "
        "shuffle is not scattering them (regression of the old rotate bug?)"
    )
