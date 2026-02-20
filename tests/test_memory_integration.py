"""Integration test for the memory system end-to-end.

Simulates multi-round, multi-game flows to verify:
1. Prompt generation includes all memory layers
2. Memory evolves correctly across rounds (create -> use -> verify -> high-value)
3. Complete prompt inspection for manual review
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import shutil
import re
from dataclasses import dataclass, field

from backend.ai.prompts import build_base_prompt
from backend.ai.agent import UnifiedGameAgent
from backend.memory.reflection import ReflectionPipeline
from backend.memory.strategy_tracker import StrategyTracker
from backend.memory.event_index import EventIndexManager
from backend.memory.storage import MemoryStorage
from backend.memory.knowledge import KnowledgeManager
from backend.memory.generators import (
    generate_initial_knowledge_summary,
    generate_night_record,
    generate_day_record,
)

MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test_integration"
GAME_1 = "integ_game_1"
GAME_2 = "integ_game_2"


# ─── Mock Classes ──────────────────────────────────────────────────


class MockLLM:
    """Programmable mock LLM that returns preset responses in order."""

    def __init__(self):
        self.responses = []
        self.call_log = []
        self.call_count = 0

    def add_response(self, response: str):
        self.responses.append(response)

    def __call__(self, prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:
        self.call_log.append({"prompt": prompt, "max_tokens": max_tokens})
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return '{"new_items": [], "updates": [], "strategy_shift": ""}'


@dataclass
class MockPlayer:
    id: str = "player_1"
    name: str = "Alice"
    role: str = "seer"
    personality: str = "LOGICAL"
    is_human: bool = False
    alive: bool = True

    def is_werewolf(self):
        return self.role == "werewolf"


@dataclass
class MockEvent:
    type: str = ""
    round_number: int = 1
    data: dict = field(default_factory=dict)


@dataclass
class MockGameState:
    game_id: str = GAME_1
    round_number: int = 1
    speeches: list = field(default_factory=list)
    votes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    night_kills: list = field(default_factory=list)
    saved_player: str = None
    poisoned_player: str = None
    seer_checked: str = None
    seer_checks: dict = field(default_factory=dict)
    alive_players: list = field(default_factory=list)
    alive_werewolves: list = field(default_factory=list)
    players: list = field(default_factory=list)
    witch_save_available: bool = True
    witch_poison_available: bool = True

    def get_player_by_id(self, player_id):
        for p in self.players:
            if p.id == player_id:
                return p
        return None


# ─── Preset Mock Responses ─────────────────────────────────────────

ROUND_1_REFLECTION = json.dumps(
    {
        "new_items": [
            {
                "section": "### 策略笔记",
                "content": "player_3 在发言时频繁观察其他玩家反应，行为模式可疑，可能是狼人",
                "source": "round_1_discussion",
            },
            {
                "section": "### 怀疑对象",
                "content": "根据投票走向，player_4 和 player_3 可能是同伙关系",
                "source": "round_1_vote",
            },
        ],
        "updates": [],
        "strategy_shift": "首轮保持低调，优先查验 player_3 确认身份",
    }
)

ROUND_2_REFLECTION = json.dumps(
    {
        "new_items": [
            {
                "section": "### 验证身份",
                "content": "查验 player_3 结果为狼人，之前的行为分析判断正确",
                "source": "round_2_investigation",
            },
        ],
        "updates": [
            {"id": "s1r1", "delta_helpful": 1, "reason": "player_3 确认为狼人，之前的行为观察是正确的"},
        ],
        "strategy_shift": "已确认一名狼人，准备在适当时机跳预言家身份带节奏",
    }
)

ROUND_3_REFLECTION = json.dumps(
    {
        "new_items": [
            {
                "section": "### 策略笔记",
                "content": "player_4 在 player_3 被放逐后发言混乱，高概率是另一名狼人",
                "source": "round_3_discussion",
            },
        ],
        "updates": [
            {"id": "s1r1", "delta_helpful": 1, "reason": "player_3 被放逐证实了之前的分析方法有效"},
            {"id": "s2r1", "delta_helpful": 1, "reason": "同伙关系分析被证实"},
        ],
        "strategy_shift": "跳预言家公开身份，引导村民投票 player_4",
    }
)


# ─── Helpers ───────────────────────────────────────────────────────


def create_players():
    """Create a standard 6-player game."""
    return [
        MockPlayer(id="player_1", name="Alice", role="seer", personality="LOGICAL"),
        MockPlayer(id="player_2", name="Bob", role="werewolf", personality="AGGRESSIVE"),
        MockPlayer(id="player_3", name="Charlie", role="werewolf", personality="CAUTIOUS"),
        MockPlayer(id="player_4", name="Diana", role="witch", personality="STRATEGIC"),
        MockPlayer(id="player_5", name="Eve", role="hunter", personality="BRAVE"),
        MockPlayer(id="player_6", name="Frank", role="villager", personality="ANALYTICAL"),
    ]


def cleanup():
    if MEMORY_BASE.exists():
        shutil.rmtree(MEMORY_BASE)


def setup_game_memory(game_id: str, players: list):
    """Initialize all memory layers for a game."""
    km = KnowledgeManager(MEMORY_BASE, game_id)

    for p in players:
        storage = MemoryStorage(p.id, MEMORY_BASE, game_id)
        profile = (
            f"# {p.name} - 角色档案\n\n"
            f"## 核心特质\n- **类型**: {p.personality}\n- **原型**: 玩家\n- **自信度**: 中等\n\n"
            f"## 行为模式\n\n### 沟通风格\n- 随机应变\n\n### 决策风格\n- 权衡利弊后决定"
        )
        storage.create_profile_personality(profile)
        km.initialize_player_knowledge(p.id, p.role)

    km.initialize_werewolf_shared()

    ei = EventIndexManager(MEMORY_BASE, game_id)
    ei.initialize()


def run_three_rounds(game_id: str, seer, players: list, mock_llm: MockLLM):
    """Run 3 rounds of reflection for the seer player. Returns the storage object."""
    ei = EventIndexManager(MEMORY_BASE, game_id)
    storage = MemoryStorage(seer.id, MEMORY_BASE, game_id)

    # --- Round 1 ---
    day1 = generate_day_record(
        round_num=1,
        morning_deaths=[{"name": "Eve"}],
        speeches=[
            {"player": "Alice", "content": "第一轮先观察"},
            {"player": "Bob", "content": "我觉得大家应该冷静分析"},
            {"player": "Charlie", "content": "我同意Bob的说法"},
        ],
        votes={"Alice": "Charlie", "Frank": "Charlie", "Bob": "Frank"},
        eliminated={"name": "Charlie", "votes": 2},
    )
    storage.create_day_record_shared(1, day1)
    night1 = generate_night_record(
        "Alice",
        "seer",
        1,
        {
            "target": "Charlie",
            "result": "WEREWOLF",
            "thoughts": "首夜验证",
        },
    )
    storage.create_night_record(1, night1)
    ei.record_death(1, "Eve", "werewolf_kill", is_night=True)

    gs1 = MockGameState(
        game_id=game_id,
        round_number=1,
        speeches=[
            {"player": "Alice", "content": "第一轮先观察"},
            {"player": "Bob", "content": "冷静分析"},
            {"player": "Charlie", "content": "我同意Bob"},
        ],
        votes={"player_1": "player_3", "player_6": "player_3", "player_2": "player_6"},
        players=players,
        alive_players=players,
        events=[MockEvent(type="NIGHT_DEATH", round_number=1, data={"victim": "Eve", "cause": "狼人击杀"})],
        night_kills=["player_5"],
        seer_checked="player_3",
        seer_checks={"player_3": "WEREWOLF"},
    )
    pipeline1 = ReflectionPipeline(MEMORY_BASE, game_id, llm_call_fn=mock_llm)
    pipeline1.run(seer, gs1)

    # --- Round 2 ---
    day2 = generate_day_record(
        round_num=2,
        morning_deaths=[{"name": "Frank"}],
        speeches=[
            {"player": "Alice", "content": "我查验了Charlie，他是狼人"},
            {"player": "Bob", "content": "如果Alice是预言家那Frank也可疑"},
        ],
        votes={"Alice": "Bob", "Diana": "Bob"},
        eliminated={"name": "Bob", "votes": 2},
    )
    storage.create_day_record_shared(2, day2)
    night2 = generate_night_record(
        "Alice",
        "seer",
        2,
        {
            "target": "Bob",
            "result": "WEREWOLF",
            "thoughts": "确认第二狼",
        },
    )
    storage.create_night_record(2, night2)
    ei.record_death(2, "Frank", "werewolf_kill", is_night=True)
    ei.record_claim(1, "Alice", "seer", status="unverified")

    alive_r2 = [p for p in players if p.name not in ("Eve", "Charlie")]
    gs2 = MockGameState(
        game_id=game_id,
        round_number=2,
        speeches=[
            {"player": "Alice", "content": "我查验了Charlie，他是狼人"},
            {"player": "Bob", "content": "Alice可疑"},
        ],
        votes={"player_1": "player_2", "player_4": "player_2"},
        players=players,
        alive_players=alive_r2,
        events=[MockEvent(type="NIGHT_DEATH", round_number=2, data={"victim": "Frank", "cause": "狼人击杀"})],
        night_kills=["player_6"],
        seer_checked="player_2",
        seer_checks={"player_2": "WEREWOLF"},
    )
    pipeline2 = ReflectionPipeline(MEMORY_BASE, game_id, llm_call_fn=mock_llm)
    pipeline2.run(seer, gs2)

    # --- Round 3 ---
    day3 = generate_day_record(
        round_num=3,
        morning_deaths=[],
        speeches=[
            {"player": "Alice", "content": "Bob也是狼人，我已经验过了"},
            {"player": "Diana", "content": "那我们赢了"},
        ],
        votes={"Alice": "Bob", "Diana": "Bob"},
        eliminated={"name": "Bob", "votes": 2},
    )
    storage.create_day_record_shared(3, day3)

    alive_r3 = [p for p in players if p.name not in ("Eve", "Charlie", "Frank", "Bob")]
    gs3 = MockGameState(
        game_id=game_id,
        round_number=3,
        speeches=[
            {"player": "Alice", "content": "Bob也是狼人"},
            {"player": "Diana", "content": "我们赢了"},
        ],
        votes={"player_1": "player_2", "player_4": "player_2"},
        players=players,
        alive_players=alive_r3,
        events=[],
        night_kills=[],
    )
    pipeline3 = ReflectionPipeline(MEMORY_BASE, game_id, llm_call_fn=mock_llm)
    pipeline3.run(seer, gs3)

    return storage


# ═══════════════════════════════════════════════════════════════════
# TEST 1: Prompt generation with all memory layers
# ═══════════════════════════════════════════════════════════════════


def test_1_prompt_generation_with_memories():
    """Verify build_base_prompt() includes all memory layers."""
    print("\n" + "=" * 60)
    print("TEST 1: Prompt generation with all memory layers")
    print("=" * 60)

    cleanup()
    players = create_players()
    seer = players[0]
    wolf = players[1]
    setup_game_memory(GAME_1, players)

    storage = MemoryStorage(seer.id, MEMORY_BASE, GAME_1)
    km = KnowledgeManager(MEMORY_BASE, GAME_1)
    ei = EventIndexManager(MEMORY_BASE, GAME_1)

    # Day records
    day = generate_day_record(
        round_num=1,
        morning_deaths=[],
        speeches=[
            {"player": "Alice", "content": "我觉得Charlie很可疑"},
            {"player": "Bob", "content": "我同意Alice的看法"},
        ],
        votes={"Alice": "Charlie", "Bob": "Charlie"},
        eliminated={"name": "Charlie", "votes": 2},
    )
    storage.create_day_record_shared(1, day)

    # Night records
    night = generate_night_record(
        "Alice",
        "seer",
        1,
        {
            "target": "Charlie",
            "result": "WEREWOLF",
            "thoughts": "验证怀疑",
        },
    )
    storage.create_night_record(1, night)

    # Event index
    ei.record_death(1, "Eve", "werewolf_kill", is_night=True)
    ei.record_claim(1, "Alice", "seer", status="unverified")
    ei.record_contradiction(1, ["Alice", "Frank"], "seer_conflict")

    # Strategy meta with high-value item (H:2)
    meta_path = MEMORY_BASE / GAME_1 / seer.id / "knowledge" / "role" / seer.role / "strategy_meta.json"
    tracker = StrategyTracker(meta_path)
    tracker.initialize()
    tracker.add_item("s1r1", 1, "round_1_reflection")
    tracker.update_helpful("s1r1", 1)
    tracker.update_helpful("s1r1", 1)  # H:2 → high value

    # Add item to summary so _load_player_memories can find it
    km.update_knowledge_summary(
        player_id=seer.id,
        role=seer.role,
        operation="append",
        section="### 策略笔记",
        content="[#s1r1] [H:0] player_3 行为可疑分析准确，验证有效",
    )

    # Reflection archive (for last_reflection_shift)
    ref_dir = MEMORY_BASE / GAME_1 / seer.id / "reflections"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "round_1_reflection.md").write_text(
        "# 第1轮复盘 - Alice（seer）\n\n## 策略调整\n准备下一轮跳预言家带节奏\n\n## 新发现\n- 发现Charlie可疑\n",
        encoding="utf-8",
    )

    # Werewolf shared memory
    km.update_werewolf_strategy("\n### 第1轮\n- 击杀Eve\n- Bob跳预言家\n")
    km.update_werewolf_threats(
        threat_updates={"Alice": 8, "Diana": 5},
        history_entry={"round": 1, "target": "Eve", "reasoning": "消除威胁"},
    )

    # ── Load memories and build prompt for seer ──
    agent = UnifiedGameAgent(memory_base=MEMORY_BASE)
    gs = MockGameState(
        game_id=GAME_1,
        round_number=1,
        alive_players=players,
        players=players,
    )

    memories = agent._load_player_memories(seer, gs)

    assert memories["profile"], "Profile should be populated"
    assert memories["knowledge_summary"], "Knowledge summary should be populated"
    assert memories["day_record"], "Day records should be populated"
    assert memories["night_record"], "Night records should be populated"
    assert memories["event_index_facts"], "Event index facts should be populated"
    assert memories["high_value_insights"], "High value insights should be populated"
    assert memories["last_reflection_shift"], "Reflection shift should be populated"
    print("PASS: All memory keys populated for seer")

    assert "s1r1" in memories["high_value_insights"]
    print("PASS: High-value insight s1r1 extracted")

    prompt = build_base_prompt(seer, memories, gs)
    assert "# 你的角色: SEER" in prompt
    assert "高效策略洞察" in prompt
    assert "关键事实（不受窗口限制）" in prompt
    assert "白天记录" in prompt
    assert "我的夜间行动" in prompt
    assert "上轮复盘" in prompt
    print("PASS: Seer prompt contains all memory sections")

    # ── Werewolf memories ──
    wolf_mem = agent._load_player_memories(wolf, gs)
    assert "shared" in wolf_mem
    assert wolf_mem["shared"]["strategy"]
    assert wolf_mem["shared"]["threats"]
    print("PASS: Werewolf shared memory loaded")

    wolf_prompt = build_base_prompt(wolf, wolf_mem, gs)
    assert "狼队共享记忆" in wolf_prompt
    assert "威胁评分" in wolf_prompt
    print("PASS: Werewolf prompt contains shared memory sections")

    print("\nTEST 1: ALL PASS")


# ═══════════════════════════════════════════════════════════════════
# TEST 2: Memory lifecycle across 3 rounds
# ═══════════════════════════════════════════════════════════════════


def test_2_memory_lifecycle_across_rounds():
    """Simulate 3 rounds, verify memory evolution."""
    print("\n" + "=" * 60)
    print("TEST 2: Memory lifecycle across 3 rounds")
    print("=" * 60)

    cleanup()
    players = create_players()
    seer = players[0]
    setup_game_memory(GAME_1, players)

    mock_llm = MockLLM()
    mock_llm.add_response(ROUND_1_REFLECTION)
    mock_llm.add_response(ROUND_2_REFLECTION)
    mock_llm.add_response(ROUND_3_REFLECTION)

    ei = EventIndexManager(MEMORY_BASE, GAME_1)
    km = KnowledgeManager(MEMORY_BASE, GAME_1)
    storage = MemoryStorage(seer.id, MEMORY_BASE, GAME_1)

    # ── Round 1 ──
    print("\n--- Round 1 ---")
    day1 = generate_day_record(
        round_num=1,
        morning_deaths=[{"name": "Eve"}],
        speeches=[
            {"player": "Alice", "content": "第一轮先观察"},
            {"player": "Bob", "content": "冷静分析"},
            {"player": "Charlie", "content": "同意Bob"},
        ],
        votes={"Alice": "Charlie", "Frank": "Charlie", "Bob": "Frank"},
        eliminated={"name": "Charlie", "votes": 2},
    )
    storage.create_day_record_shared(1, day1)
    night1 = generate_night_record(
        "Alice",
        "seer",
        1,
        {
            "target": "Charlie",
            "result": "WEREWOLF",
        },
    )
    storage.create_night_record(1, night1)
    ei.record_death(1, "Eve", "werewolf_kill", is_night=True)

    gs1 = MockGameState(
        game_id=GAME_1,
        round_number=1,
        speeches=[
            {"player": "Alice", "content": "第一轮先观察"},
            {"player": "Bob", "content": "冷静分析"},
            {"player": "Charlie", "content": "同意Bob"},
        ],
        votes={"player_1": "player_3", "player_6": "player_3", "player_2": "player_6"},
        players=players,
        alive_players=players,
        events=[MockEvent(type="NIGHT_DEATH", round_number=1, data={"victim": "Eve", "cause": "狼人击杀"})],
        night_kills=["player_5"],
        seer_checked="player_3",
        seer_checks={"player_3": "WEREWOLF"},
    )
    pipeline1 = ReflectionPipeline(MEMORY_BASE, GAME_1, llm_call_fn=mock_llm)
    success1 = pipeline1.run(seer, gs1)
    assert success1, "Round 1 reflection should succeed"

    summary1 = km.read_knowledge_summary(seer.id, seer.role)
    assert "[#s1r1]" in summary1, "Summary should contain s1r1"
    assert "[#s2r1]" in summary1, "Summary should contain s2r1"
    assert "[H:0]" in summary1, "Initial helpfulness should be 0"
    print("PASS: Round 1 - summary.md has [#s1r1] [H:0] and [#s2r1]")

    meta1 = storage.read_json(f"knowledge/role/{seer.role}/strategy_meta.json")
    assert "s1r1" in meta1["items"]
    assert "s2r1" in meta1["items"]
    assert meta1["items"]["s1r1"]["helpful_count"] == 0
    print("PASS: Round 1 - strategy_meta.json created correctly")

    ref1 = MEMORY_BASE / GAME_1 / seer.id / "reflections" / "round_1_reflection.md"
    assert ref1.exists(), "Round 1 reflection archive should exist"
    print("PASS: Round 1 - reflection archive saved")

    # ── Round 2 ──
    print("\n--- Round 2 ---")
    day2 = generate_day_record(
        round_num=2,
        morning_deaths=[{"name": "Frank"}],
        speeches=[
            {"player": "Alice", "content": "Charlie是狼人"},
            {"player": "Bob", "content": "Alice可疑"},
        ],
        votes={"Alice": "Bob", "Diana": "Bob"},
        eliminated={"name": "Bob", "votes": 2},
    )
    storage.create_day_record_shared(2, day2)
    night2 = generate_night_record(
        "Alice",
        "seer",
        2,
        {
            "target": "Bob",
            "result": "WEREWOLF",
        },
    )
    storage.create_night_record(2, night2)
    ei.record_death(2, "Frank", "werewolf_kill", is_night=True)
    ei.record_claim(1, "Alice", "seer", status="unverified")

    alive_r2 = [p for p in players if p.name not in ("Eve", "Charlie")]
    gs2 = MockGameState(
        game_id=GAME_1,
        round_number=2,
        speeches=[
            {"player": "Alice", "content": "Charlie是狼人"},
            {"player": "Bob", "content": "Alice可疑"},
        ],
        votes={"player_1": "player_2", "player_4": "player_2"},
        players=players,
        alive_players=alive_r2,
        events=[MockEvent(type="NIGHT_DEATH", round_number=2, data={"victim": "Frank", "cause": "狼人击杀"})],
        night_kills=["player_6"],
        seer_checked="player_2",
        seer_checks={"player_2": "WEREWOLF"},
    )
    pipeline2 = ReflectionPipeline(MEMORY_BASE, GAME_1, llm_call_fn=mock_llm)
    success2 = pipeline2.run(seer, gs2)
    assert success2, "Round 2 reflection should succeed"

    meta2 = storage.read_json(f"knowledge/role/{seer.role}/strategy_meta.json")
    assert meta2["items"]["s1r1"]["helpful_count"] == 1, (
        f"s1r1 helpful should be 1, got {meta2['items']['s1r1']['helpful_count']}"
    )
    print("PASS: Round 2 - s1r1 helpful_count == 1")

    assert "s3r2" in meta2["items"], "Round 2 should add s3r2"
    print("PASS: Round 2 - new item s3r2 added")

    ref2 = MEMORY_BASE / GAME_1 / seer.id / "reflections" / "round_2_reflection.md"
    assert ref2.exists()
    print("PASS: Round 2 - reflection archive saved")

    # ── Round 3 ──
    print("\n--- Round 3 ---")
    day3 = generate_day_record(
        round_num=3,
        morning_deaths=[],
        speeches=[
            {"player": "Alice", "content": "Bob也是狼人"},
            {"player": "Diana", "content": "我们赢了"},
        ],
        votes={"Alice": "Bob", "Diana": "Bob"},
        eliminated={"name": "Bob", "votes": 2},
    )
    storage.create_day_record_shared(3, day3)

    alive_r3 = [p for p in players if p.name not in ("Eve", "Charlie", "Frank", "Bob")]
    gs3 = MockGameState(
        game_id=GAME_1,
        round_number=3,
        speeches=[
            {"player": "Alice", "content": "Bob也是狼人"},
            {"player": "Diana", "content": "我们赢了"},
        ],
        votes={"player_1": "player_2", "player_4": "player_2"},
        players=players,
        alive_players=alive_r3,
        events=[],
        night_kills=[],
    )
    pipeline3 = ReflectionPipeline(MEMORY_BASE, GAME_1, llm_call_fn=mock_llm)
    success3 = pipeline3.run(seer, gs3)
    assert success3, "Round 3 reflection should succeed"

    meta3 = storage.read_json(f"knowledge/role/{seer.role}/strategy_meta.json")
    assert meta3["items"]["s1r1"]["helpful_count"] == 2, (
        f"s1r1 helpful should be 2, got {meta3['items']['s1r1']['helpful_count']}"
    )
    print("PASS: Round 3 - s1r1 helpful_count == 2 (high value!)")

    assert meta3["items"]["s2r1"]["helpful_count"] == 1, (
        f"s2r1 helpful should be 1, got {meta3['items']['s2r1']['helpful_count']}"
    )
    print("PASS: Round 3 - s2r1 helpful_count == 1")

    # High-value items check
    tracker = StrategyTracker(MEMORY_BASE / GAME_1 / seer.id / "knowledge" / "role" / seer.role / "strategy_meta.json")
    high_items = tracker.get_high_value_items(min_helpful=2)
    assert "s1r1" in high_items, "s1r1 should be in high_value_items"
    print("PASS: Round 3 - s1r1 in high_value_items")

    # EventIndex accumulated facts
    facts = ei.get_key_facts()
    assert "Eve" in facts, "Should record Eve's death"
    assert "Frank" in facts, "Should record Frank's death"
    print("PASS: EventIndex accumulated all death records")

    # Reflection archives
    all_refs = sorted((MEMORY_BASE / GAME_1 / seer.id / "reflections").glob("*.md"))
    assert len(all_refs) == 3, f"Should have 3 reflection archives, got {len(all_refs)}"
    print("PASS: 3 reflection archives exist")

    # _load_player_memories returns high-value insights
    agent = UnifiedGameAgent(memory_base=MEMORY_BASE)
    memories = agent._load_player_memories(seer, gs3)
    assert memories["high_value_insights"], "High value insights should not be empty"
    assert "s1r1" in memories["high_value_insights"]
    print("PASS: _load_player_memories returns high_value_insights with s1r1")

    # Prompt includes high-value section
    prompt = build_base_prompt(seer, memories, gs3)
    assert "高效策略洞察" in prompt
    print("PASS: build_base_prompt includes high-value section")

    # LLM call count
    assert mock_llm.call_count == 3, f"LLM should be called 3 times, got {mock_llm.call_count}"
    print("PASS: Mock LLM called exactly 3 times")

    print("\nTEST 2: ALL PASS")


# ═══════════════════════════════════════════════════════════════════
# TEST 3: Full prompt inspection (manual review)
# ═══════════════════════════════════════════════════════════════════


def test_3_full_game_prompt_inspection():
    """Generate and print prompts for each role for manual review."""
    print("\n" + "=" * 60)
    print("TEST 3: Full prompt inspection (manual review)")
    print("=" * 60)

    cleanup()
    players = create_players()
    game_id = "inspect_game"
    setup_game_memory(game_id, players)

    km = KnowledgeManager(MEMORY_BASE, game_id)
    ei = EventIndexManager(MEMORY_BASE, game_id)

    # Add rich memory data
    for p in players:
        storage = MemoryStorage(p.id, MEMORY_BASE, game_id)
        day = generate_day_record(
            round_num=1,
            morning_deaths=[{"name": "Eve"}],
            speeches=[
                {"player": "Alice", "content": "我觉得Charlie行为可疑"},
                {"player": "Bob", "content": "我同意，Charlie确实有问题"},
                {"player": "Charlie", "content": "我是好人，别冤枉我"},
                {"player": "Diana", "content": "先不急着下结论"},
            ],
            votes={"Alice": "Charlie", "Bob": "Charlie", "Charlie": "Alice", "Diana": "Charlie"},
            eliminated={"name": "Charlie", "votes": 3},
        )
        storage.create_day_record_shared(1, day)

        if p.role == "seer":
            night = generate_night_record(
                p.name,
                p.role,
                1,
                {
                    "target": "Charlie",
                    "result": "WEREWOLF",
                },
            )
        elif p.role == "witch":
            night = generate_night_record(
                p.name,
                p.role,
                1,
                {
                    "victim": "Eve",
                    "save_used": False,
                    "poison_used": False,
                    "reasoning": "留药到关键时刻",
                },
            )
        elif p.role == "werewolf":
            night = generate_night_record(
                p.name,
                p.role,
                1,
                {
                    "target": "Eve",
                    "discussion": "团队决定先杀Eve",
                    "reasoning": "Eve可能是预言家",
                },
            )
        else:
            night = generate_night_record(p.name, p.role, 1)
        storage.create_night_record(1, night)

    ei.record_death(1, "Eve", "werewolf_kill")
    ei.record_claim(1, "Alice", "seer", status="unverified")
    ei.record_death(1, "Charlie", "vote_elimination", is_night=False)

    km.update_werewolf_strategy("\n### 第1轮\n- 击杀Eve，理由是她可能是预言家\n- Bob潜水，Charlie跳预言家\n")
    km.update_werewolf_threats(
        threat_updates={"Alice": 7, "Diana": 4, "Frank": 2},
    )

    # Build and print prompts
    agent = UnifiedGameAgent(memory_base=MEMORY_BASE)
    gs = MockGameState(
        game_id=game_id,
        round_number=1,
        players=players,
        alive_players=[p for p in players if p.name != "Eve"],
    )

    for role in ["seer", "werewolf", "witch", "villager"]:
        player = next(p for p in players if p.role == role)
        memories = agent._load_player_memories(player, gs)
        prompt = build_base_prompt(player, memories, gs)

        print(f"\n{'─' * 50}")
        print(f"ROLE: {role.upper()} ({player.name})")
        print(f"Prompt length: {len(prompt)} chars")
        print(f"Memory keys: {[k for k, v in memories.items() if v]}")
        print(f"{'─' * 50}")

        # Print abbreviated prompt sections
        sections = prompt.split("---")
        for i, section in enumerate(sections):
            section = section.strip()
            if section:
                preview = section[:300] + "..." if len(section) > 300 else section
                print(f"\n  [Section {i + 1}]")
                for line in preview.split("\n"):
                    print(f"  {line}")

    print(f"\n{'─' * 50}")
    print("TEST 3: Prompt inspection complete (review above)")
    print("(No assertions - this test is for manual review)")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        test_1_prompt_generation_with_memories()
        test_2_memory_lifecycle_across_rounds()
        test_3_full_game_prompt_inspection()

        print("\n" + "=" * 60)
        print("ALL INTEGRATION TESTS PASSED!")
        print("=" * 60)
    finally:
        cleanup()
