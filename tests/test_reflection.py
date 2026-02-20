"""Test script for ReflectionPipeline functionality."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import shutil
from dataclasses import dataclass, field

from backend.memory.reflection import ReflectionPipeline
from backend.memory.storage import MemoryStorage
from backend.memory.generators import generate_initial_knowledge_summary

MEMORY_BASE = Path(__file__).parent.parent / "backend" / ".memory_test"
GAME_ID = "test_reflection"


# Minimal mock classes for testing
@dataclass
class MockPlayer:
    id: str = "player_1"
    name: str = "Alice"
    role: str = "seer"
    personality: str = "test"
    is_human: bool = False
    alive: bool = True

    def is_werewolf(self):
        return self.role == "werewolf"


@dataclass
class MockEvent:
    type: str = ""
    round: int = 1
    phase: str = ""
    data: dict = field(default_factory=dict)
    message: str = ""
    round_number: int = 1


@dataclass
class MockGameState:
    game_id: str = GAME_ID
    round_number: int = 1
    speeches: list = field(default_factory=list)
    votes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    night_kills: list = field(default_factory=list)
    saved_player: str = None
    poisoned_player: str = None
    seer_checked: str = None
    guarded_player: str = None
    seer_checks: dict = field(default_factory=dict)
    alive_players: list = field(default_factory=list)
    players: list = field(default_factory=list)

    def get_player_by_id(self, player_id):
        for p in self.players:
            if p.id == player_id:
                return p
        return None


def setup():
    """Clean up and initialize test directory."""
    test_dir = MEMORY_BASE / GAME_ID
    if test_dir.exists():
        shutil.rmtree(test_dir)

    # Initialize player knowledge
    player = MockPlayer()
    storage = MemoryStorage(player.id, MEMORY_BASE, GAME_ID)
    summary = generate_initial_knowledge_summary(player.role)
    storage.create_knowledge_summary(player.role, summary)


def test_generate_events():
    """Test event generation (Generator step)."""
    print("\n=== Testing generate_events (Generator) ===")
    setup()

    player = MockPlayer()
    game_state = MockGameState(
        speeches=[
            {"player": "Alice", "content": "我觉得Bob很可疑"},
            {"player": "Bob", "content": "我不是狼人，我是好人"},
        ],
        votes={"player_1": "player_2", "player_2": "player_3"},
        players=[
            MockPlayer(id="player_1", name="Alice"),
            MockPlayer(id="player_2", name="Bob"),
            MockPlayer(id="player_3", name="Charlie"),
        ],
        seer_checked="player_2",
        seer_checks={"player_2": "WEREWOLF"},
    )

    pipeline = ReflectionPipeline(MEMORY_BASE, GAME_ID)
    events_text = pipeline.generate_events(player, game_state)

    assert "第1轮事件摘要" in events_text
    assert "Alice" in events_text
    assert "Bob" in events_text
    assert "验人" in events_text or "WEREWOLF" in events_text
    print("PASS: Events generated correctly")
    print(f"Output preview: {events_text[:200]}...")


def test_parse_json_response():
    """Test JSON parsing from LLM response."""
    print("\n=== Testing _parse_json_response ===")

    pipeline = ReflectionPipeline(MEMORY_BASE, GAME_ID)

    # Clean JSON
    result = pipeline._parse_json_response('{"new_items": [], "updates": [], "strategy_shift": "test"}')
    assert result is not None
    assert result["strategy_shift"] == "test"
    print("PASS: Clean JSON parsed")

    # Markdown code block
    result = pipeline._parse_json_response(
        '```json\n{"new_items": [{"section": "## Test", "content": "hello world content", "source": "test"}], "updates": [], "strategy_shift": ""}\n```'
    )
    assert result is not None
    assert len(result["new_items"]) == 1
    print("PASS: Markdown code block parsed")

    # With extra text
    result = pipeline._parse_json_response(
        'Here is my analysis:\n{"new_items": [], "updates": [], "strategy_shift": "changed"}\nThat is all.'
    )
    assert result is not None
    assert result["strategy_shift"] == "changed"
    print("PASS: JSON with surrounding text parsed")

    # Invalid JSON
    result = pipeline._parse_json_response("This is not JSON at all")
    assert result is None
    print("PASS: Invalid JSON returns None")


def test_curate_validation():
    """Test Curator validation rules."""
    print("\n=== Testing curate (Curator validation) ===")
    setup()

    player = MockPlayer()
    game_state = MockGameState(round_number=2)

    pipeline = ReflectionPipeline(MEMORY_BASE, GAME_ID)

    # Rule 2: Content too short
    result = {
        "new_items": [{"section": "### 策略笔记", "content": "short", "source": "test"}],
        "updates": [],
        "strategy_shift": "",
    }
    success = pipeline.curate(player, game_state, result)
    assert success is False, "Short content should be rejected"
    print("PASS: Rule 2 - Short content rejected")

    # Rule 3: Too many items (>5 should be truncated)
    result = {
        "new_items": [
            {
                "section": "### 策略笔记",
                "content": f"This is item number {i} with enough content to pass validation",
                "source": "test",
            }
            for i in range(7)
        ],
        "updates": [],
        "strategy_shift": "",
    }
    success = pipeline.curate(player, game_state, result)
    # At most 5 should be merged
    storage = MemoryStorage(player.id, MEMORY_BASE, GAME_ID)
    meta = storage.read_json(f"knowledge/role/{player.role}/strategy_meta.json")
    assert len(meta.get("items", {})) <= 5, f"Should have at most 5 items, got {len(meta.get('items', {}))}"
    print("PASS: Rule 3 - Items limited to 5")


def test_curate_merge():
    """Test Curator delta merge into summary.md."""
    print("\n=== Testing curate (merge) ===")
    setup()

    player = MockPlayer()
    game_state = MockGameState(round_number=1)

    pipeline = ReflectionPipeline(MEMORY_BASE, GAME_ID)

    result = {
        "new_items": [
            {
                "section": "### 策略笔记",
                "content": "player_2 发言时非常紧张，可能是狼人伪装",
                "source": "round_1_discussion",
            }
        ],
        "updates": [],
        "strategy_shift": "决定下一轮重点关注 player_2",
    }

    success = pipeline.curate(player, game_state, result)
    assert success is True, "Should merge successfully"

    # Check summary.md was updated
    storage = MemoryStorage(player.id, MEMORY_BASE, GAME_ID)
    summary = storage.read_knowledge_summary(player.role)
    assert "[#s1r1]" in summary, f"Summary should contain item ID tag"
    assert "player_2 发言时非常紧张" in summary
    print("PASS: Delta merged into summary.md")

    # Check strategy_meta.json was created
    meta = storage.read_json(f"knowledge/role/{player.role}/strategy_meta.json")
    assert "s1r1" in meta.get("items", {}), "Metadata should contain item"
    print("PASS: strategy_meta.json updated")

    # Check reflection archive was saved
    reflection_path = MEMORY_BASE / GAME_ID / player.id / "reflections" / "round_1_reflection.md"
    assert reflection_path.exists(), "Reflection archive should be saved"
    print("PASS: Reflection archived")


def test_full_pipeline_with_mock_llm():
    """Test full pipeline with mock LLM."""
    print("\n=== Testing full pipeline with mock LLM ===")
    setup()

    mock_response = json.dumps(
        {
            "new_items": [
                {
                    "section": "### 策略笔记",
                    "content": "根据投票结果分析，player_3 可能与 player_2 是同伙",
                    "source": "round_1_vote_analysis",
                }
            ],
            "updates": [],
            "strategy_shift": "开始重点关注 player_3 和 player_2 的关系",
        }
    )

    def mock_llm_call(prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:
        return mock_response

    player = MockPlayer()
    game_state = MockGameState(
        speeches=[
            {"player": "Alice", "content": "我觉得应该仔细分析"},
            {"player": "Bob", "content": "我同意Alice的说法"},
        ],
        votes={"player_1": "player_3", "player_2": "player_3"},
        players=[
            MockPlayer(id="player_1", name="Alice"),
            MockPlayer(id="player_2", name="Bob"),
            MockPlayer(id="player_3", name="Charlie"),
        ],
    )

    pipeline = ReflectionPipeline(MEMORY_BASE, GAME_ID, llm_call_fn=mock_llm_call)
    success = pipeline.run(player, game_state)

    assert success is True, "Full pipeline should succeed"

    # Verify results
    storage = MemoryStorage(player.id, MEMORY_BASE, GAME_ID)
    summary = storage.read_knowledge_summary(player.role)
    assert "player_3 可能与 player_2 是同伙" in summary
    print("PASS: Full pipeline completed successfully")


def test_pipeline_without_llm():
    """Test pipeline gracefully handles missing LLM."""
    print("\n=== Testing pipeline without LLM ===")
    setup()

    player = MockPlayer()
    game_state = MockGameState()

    pipeline = ReflectionPipeline(MEMORY_BASE, GAME_ID)  # No llm_call_fn
    success = pipeline.run(player, game_state)

    assert success is False, "Should return False without LLM"
    print("PASS: Pipeline handles missing LLM gracefully")


def test_hunter_shot_in_reflection():
    """Test that hunter shot events appear in reflection event summary."""
    print("\n=== Testing hunter_shot visibility in reflection ===")
    setup()

    from backend.memory.event_index import EventIndexManager

    # Write a hunter_shot death and hunter identity into event_index
    eim = EventIndexManager(MEMORY_BASE, GAME_ID)
    eim.initialize()
    eim.record_death(round_num=1, player_name="Bob", cause="vote_elimination", is_night=False)
    eim.record_death(round_num=1, player_name="Charlie", cause="hunter_shot", is_night=False)
    eim.record_claim(round_num=1, player_name="Bob", claimed_role="hunter", status="revealed")

    player = MockPlayer(id="player_4", name="Dave", role="villager")
    game_state = MockGameState(
        speeches=[
            {"player": "Alice", "content": "我怀疑Bob是狼"},
            {"player": "Bob", "content": "我不是狼人"},
        ],
        votes={"player_1": "player_2", "player_2": "player_3", "player_4": "player_2"},
        players=[
            MockPlayer(id="player_1", name="Alice"),
            MockPlayer(id="player_2", name="Bob"),
            MockPlayer(id="player_3", name="Charlie"),
            MockPlayer(id="player_4", name="Dave", role="villager"),
        ],
    )

    pipeline = ReflectionPipeline(MEMORY_BASE, GAME_ID)
    events_text = pipeline.generate_events(player, game_state)

    # Hunter shot section must be present
    assert "猎人开枪" in events_text, f"Missing '猎人开枪' section in:\n{events_text}"
    assert "Charlie" in events_text and "被猎人开枪带走" in events_text, (
        f"Missing hunter shot detail in:\n{events_text}"
    )
    # Vote elimination should also be visible
    assert "Bob" in events_text and "被放逐出局" in events_text, f"Missing vote elimination in:\n{events_text}"
    print("PASS: Hunter shot events visible in reflection")

    # Verify get_key_facts shows hunter identity
    key_facts = eim.get_key_facts()
    assert "Charlie 被猎人击杀" in key_facts, f"Missing hunter_shot in key_facts:\n{key_facts}"
    assert "猎人是Bob" in key_facts, f"Missing hunter identity in key_facts:\n{key_facts}"
    # Hunter reveal should NOT appear in 身份声明 section
    assert "声称hunter" not in key_facts, f"Hunter reveal leaked to claims section:\n{key_facts}"
    print("PASS: get_key_facts shows hunter identity correctly")
    print(f"Key facts preview:\n{key_facts}")


if __name__ == "__main__":
    test_generate_events()
    test_parse_json_response()
    test_curate_validation()
    test_curate_merge()
    test_full_pipeline_with_mock_llm()
    test_pipeline_without_llm()
    test_hunter_shot_in_reflection()
    print("\n=== All ReflectionPipeline tests passed! ===")
