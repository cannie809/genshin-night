"""REAL LLM probe: does Reflector+Curator fabricate seer identity claims?

Manual probe -- NOT a pytest test. Invoked via:
    python tests/probe_real_llm_reflection.py

Purpose
-------
Bug 2 hypothesis: the seer ends up with a fabricated `**旅行者·空**: WEREWOLF`
entry in her knowledge_summary.md even though her authoritative seer_checks
says GOOD. Previous "proof" of this was a test that mocked the Reflector LLM
to return a malicious delta -- circular reasoning.

This probe runs the REAL ReflectionPipeline with the REAL configured LLM
(openrouter + google/gemini-3-flash-preview) under three controlled inputs
and prints the raw LLM responses + final knowledge_summary state. The user
draws their own conclusion from the observed behaviour.

Scenarios
---------
A) baseline: clean speeches, no misattribution.
B) pollution attributed to the seer herself ("I as the seer checked X -> wolf").
C) pollution attributed to someone else ("I think X is a wolf").

Each scenario is run independently; MEMORY_BASE is cleaned + reseeded between
them so no cross-scenario contamination.

Cost
----
1 LLM call per scenario (Reflector), 3 scenarios -> 3 LLM calls total.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

# --- sys.path + env setup (must happen before backend imports) ---
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

env_path = ROOT / ".env.local"
if not env_path.exists():
    print(f"[FATAL] .env.local not found at {env_path}", file=sys.stderr)
    sys.exit(1)
load_dotenv(env_path)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

if not LLM_PROVIDER:
    print("[FATAL] LLM_PROVIDER not set in .env.local", file=sys.stderr)
    sys.exit(1)
if LLM_PROVIDER == "openrouter" and not OPENROUTER_API_KEY:
    print("[FATAL] OPENROUTER_API_KEY not set in .env.local", file=sys.stderr)
    sys.exit(1)

# --- Now it's safe to import backend modules ---
from backend.ai.agent import UnifiedGameAgent
from backend.memory.generators import (
    generate_initial_knowledge_summary,
    generate_day_record,
    generate_night_record,
)
from backend.memory.knowledge import KnowledgeManager
from backend.memory.reflection import ReflectionPipeline
from backend.memory.storage import MemoryStorage
from backend.models import GameState, GamePhase
from backend.models.player import Player

# Quiet down info-level noise from backend loggers, keep warnings visible
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s %(name)s] %(message)s")


MEMORY_BASE = ROOT / "backend" / ".memory_probe_real_llm"
GAME_ID = "probe_seer_pollution"
GAME_MODE = "classic_6_witch"


# ------------------------- game state factory -------------------------


def build_game_state() -> tuple[GameState, dict[str, Player]]:
    """Build a 6-player game state. Returns (state, name->player map)."""
    players = [
        Player(id="player_1", name="胡桃", role="werewolf", personality="hutao"),
        Player(id="player_2", name="温迪", role="werewolf", personality="venti"),
        Player(id="player_3", name="菲谢尔", role="seer", personality="fischl"),
        Player(id="player_4", name="行秋", role="villager", personality="xingqiu"),
        Player(id="player_5", name="旅行者·空", role="villager", personality="aether"),
        Player(id="player_6", name="迪卢克", role="witch", personality="diluc"),
    ]
    by_name = {p.name: p for p in players}

    state = GameState(
        game_id=GAME_ID,
        mode=GAME_MODE,
        phase=GamePhase.DAY_VOTE,
        round_number=1,
        players=players,
        memory_base=MEMORY_BASE,
    )
    return state, by_name


# ------------------------- seeding helpers ----------------------------


def clean_memory_base() -> None:
    target = MEMORY_BASE / GAME_ID
    if target.exists():
        shutil.rmtree(target)


def seed_seer_authoritative_state(state: GameState, seer: Player, victim: Player) -> None:
    """Set seer_checks (authoritative) + write a summary containing the real GOOD check."""
    state.seer_checked = victim.id
    state.seer_checks = {victim.id: "GOOD"}

    # 1. Initialize every player's knowledge summary (role-specific).
    km = KnowledgeManager(MEMORY_BASE, GAME_ID)
    for p in state.players:
        km.initialize_player_knowledge(p.id, p.role, GAME_MODE)

    # 2. For the seer, overwrite summary.md so that the authoritative GOOD check
    #    is already present in the "### 验证身份" section -- this is exactly
    #    what the real game writes after a successful check.
    storage = MemoryStorage(seer.id, MEMORY_BASE, GAME_ID)
    base = generate_initial_knowledge_summary("seer", GAME_MODE)
    # Replace the placeholder line in the 验证身份 section.
    seeded = base.replace(
        "（开局为空，随着验人逐步填充）",
        f"- **{victim.name}**: GOOD ✓（第1轮查验）",
    )
    storage.create_knowledge_summary("seer", seeded)

    # 3. Write the seer's night_record for round 1 (truthful GOOD check).
    storage.create_night_record(
        1,
        generate_night_record(
            seer.name,
            "seer",
            1,
            action={
                "target": victim.name,
                "result": "GOOD",
                "thoughts": f"查验{victim.name}，结果GOOD，确认好人。",
            },
        ),
    )


def write_day_record_shared(state: GameState, speeches: list[dict[str, str]]) -> None:
    """Write day_record_shared/round_1.md from structured speeches (for realism)."""
    votes_human_readable = {}
    for voter_id, target_id in state.votes.items():
        voter = state.get_player_by_id(voter_id)
        target = state.get_player_by_id(target_id) if target_id != "ABSTAIN" else None
        if voter:
            votes_human_readable[voter.name] = target.name if target else "弃权"

    storage = MemoryStorage("day_record_shared", MEMORY_BASE, GAME_ID)
    storage.create_day_record_shared(
        state.round_number,
        generate_day_record(
            round_num=state.round_number,
            morning_deaths=[],  # no one died night 1
            speeches=speeches,
            votes=votes_human_readable,
            eliminated=None,
        ),
    )


# ------------------------- scenario speeches --------------------------

SCENARIO_A_SPEECHES = [
    {"player": "胡桃", "content": "大家好，第一天我也没啥信息，先观察。"},
    {"player": "温迪", "content": "我是村民，这把跟好人一起找狼。"},
    {"player": "菲谢尔", "content": "在下先聆听众人之言，再作判断。"},
    {"player": "行秋", "content": "我觉得第一轮大家发言都挺平静。"},
    {"player": "旅行者·空", "content": "我是普通村民，愿意配合大家。"},
    {"player": "迪卢克", "content": "保持警惕，别乱跳身份。"},
]

# B: offending line ATTRIBUTED TO THE SEER (菲谢尔) herself.
# This is the exact pattern the user is worried about.
SCENARIO_B_SPEECHES = [
    {"player": "胡桃", "content": "嗨嗨~ 嘿，第一天都没啥线索啊。"},
    {"player": "温迪", "content": "诗人温迪在此，今日乃是平安夜。"},
    {
        "player": "菲谢尔",
        "content": "余乃预言家菲谢尔！昨夜余查验了旅行者·空——他是狼人！请诸位放逐他！",
    },
    {"player": "行秋", "content": "菲谢尔跳预言家了？那就先信一手吧。"},
    {"player": "旅行者·空", "content": "我不是狼！这是污蔑！我是纯村民！"},
    {"player": "迪卢克", "content": "先看菲谢尔的查杀能不能稳住。"},
]

# C: same accusation content, but attributed to 胡桃 (NOT to the seer).
SCENARIO_C_SPEECHES = [
    {"player": "胡桃", "content": "我觉得旅行者·空就是狼人，他发言闪烁其词！"},
    {"player": "温迪", "content": "胡桃这口咬得够狠啊，有证据吗？"},
    {"player": "菲谢尔", "content": "在下保留意见，不轻易下定论。"},
    {"player": "行秋", "content": "胡桃这么冲，反而有点可疑。"},
    {"player": "旅行者·空", "content": "我不是狼！胡桃你别乱带节奏！"},
    {"player": "迪卢克", "content": "冷静点，大家各自陈述观点。"},
]


# ------------------------- LLM capture hook ---------------------------


def run_scenario(label: str, speeches: list[dict[str, str]], agent: UnifiedGameAgent) -> dict:
    """Run one scenario end-to-end; return result dict for printing."""
    print(f"\n{'=' * 72}\n=== Scenario {label} ===\n{'=' * 72}")

    clean_memory_base()
    state, by_name = build_game_state()
    seer = by_name["菲谢尔"]
    victim = by_name["旅行者·空"]

    # Seed authoritative seer state + all players' knowledge.
    seed_seer_authoritative_state(state, seer, victim)

    # Put speeches into game state -> snapshot will capture them.
    state.speeches = list(speeches)
    # Benign votes (no one votes for anyone specific -- not important for reflection).
    state.votes = {p.id: "ABSTAIN" for p in state.players}

    # Also write a day_record_shared/round_1.md file (for realism --
    # Reflector does NOT read it, but this matches the real game's artefacts).
    write_day_record_shared(state, speeches)

    # Take snapshot (this is what the engine hands to ReflectionPipeline.run).
    snapshot = state.snapshot_round_data()

    # Intercept LLM response by wrapping the llm_call_fn.
    captured = {"prompt": None, "response": None}
    base_fn = agent.get_reflection_llm_fn()

    def capturing_fn(prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:
        captured["prompt"] = prompt
        resp = base_fn(prompt, max_tokens=max_tokens, temperature=temperature)
        captured["response"] = resp
        return resp

    pipeline = ReflectionPipeline(
        memory_base=MEMORY_BASE,
        game_id=GAME_ID,
        llm_call_fn=capturing_fn,
    )

    # Real LLM call happens here. If it fails, let the exception bubble --
    # per the user's instruction: do not silently catch.
    ok = pipeline.run(seer, state, round_num=1, snapshot=snapshot)

    # Read back the final summary.
    storage = MemoryStorage(seer.id, MEMORY_BASE, GAME_ID)
    final_summary = storage.read_knowledge_summary("seer")

    return {
        "label": label,
        "ok": ok,
        "prompt": captured["prompt"] or "",
        "response": captured["response"] or "",
        "final_summary": final_summary,
    }


# ------------------------- summary parsing ----------------------------


def extract_verified_section(summary: str) -> list[str]:
    """Pull out the lines under '### 验证身份' until the next ### header."""
    lines = summary.splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        if ln.strip().startswith("### 验证身份"):
            in_section = True
            out.append(ln)
            continue
        if in_section:
            # Stop at next ### header (same or higher level).
            if ln.strip().startswith("### ") and not ln.strip().startswith("### 验证身份"):
                break
            # Also stop at ## headers.
            if ln.strip().startswith("## "):
                break
            out.append(ln)
    return out


def fabricated_verdict(summary: str, victim_name: str = "旅行者·空") -> bool:
    """Return True if summary claims the victim is a WEREWOLF / 狼人 anywhere."""
    lower = summary  # CJK -- don't lowercase
    bad_tokens = ["WEREWOLF", "werewolf", "狼人", "WOLF"]
    # The fabrication signature: the victim's name co-occurring with a wolf label
    # on the same line.
    for ln in lower.splitlines():
        if victim_name in ln and any(tok in ln for tok in bad_tokens):
            return True
    return False


# ------------------------- main ---------------------------------------


def main() -> int:
    print("=" * 72)
    print("REAL LLM PROBE: Bug 2 (seer knowledge pollution)")
    print("=" * 72)
    print(f"Provider: {LLM_PROVIDER}")
    print(f"Model:    {LLM_MODEL}")
    print(f"This probe will call the real LLM ~3 times (1 per scenario).")
    print(f"Memory base (wiped between scenarios): {MEMORY_BASE}")
    print()

    # Build agent once.
    agent = UnifiedGameAgent(memory_base=MEMORY_BASE, llm_provider=LLM_PROVIDER)

    results = []
    for label, speeches in [
        ("A", SCENARIO_A_SPEECHES),
        ("B", SCENARIO_B_SPEECHES),
        ("C", SCENARIO_C_SPEECHES),
    ]:
        r = run_scenario(label, speeches, agent)
        results.append(r)

        print(f"\n--- [{label}] raw Reflector LLM response (first 1500 chars) ---")
        resp = r["response"]
        if not resp:
            print("(empty -- LLM call failed)")
        else:
            print(resp[:1500])
            if len(resp) > 1500:
                print(f"... [truncated, total {len(resp)} chars]")

        print(f"\n--- [{label}] '### 验证身份' section of final summary ---")
        for ln in extract_verified_section(r["final_summary"]):
            print(ln)

        fab = fabricated_verdict(r["final_summary"])
        print(f"\n--- [{label}] FABRICATED: {'yes' if fab else 'no'} ---")

    # Final overview.
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for r in results:
        fab = fabricated_verdict(r["final_summary"])
        print(f"  [{r['label']}] pipeline_ok={r['ok']}  FABRICATED={fab}")

    # Also dump the full final_summary for B -- that's the smoking gun if present.
    for r in results:
        if r["label"] == "B":
            print("\n--- [B] FULL final knowledge_summary.md ---")
            print(r["final_summary"])
            print("--- [B] END ---")

    return 0


if __name__ == "__main__":
    sys.exit(main())
