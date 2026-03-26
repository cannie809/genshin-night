import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.memory.generators import generate_day_record, generate_night_record


def test_generate_day_record_standard():
    round_num = 1
    morning_deaths = [{"name": "Player 3"}]
    speeches = [
        {"player": "Player 1", "content": "I am a villager."},
        {"player": "Player 2", "content": "I think Player 3 was suspicious."},
    ]
    votes = {"Player 1": "Player 2", "Player 2": "Player 5", "Player 4": "Player 2"}
    eliminated = {"name": "Player 2", "votes": 2}

    result = generate_day_record(round_num, morning_deaths, speeches, votes, eliminated)

    assert f"# 第{round_num}轮 - 白天记录" in result
    assert "## 晨间公告" in result
    assert "- **昨晚倒牌**: Player 3 死亡" in result
    assert "## 白天讨论" in result
    assert '**Player 1**: "I am a villager."' in result
    assert '**Player 2**: "I think Player 3 was suspicious."' in result
    assert "## 投票" in result
    assert "| Player 1 | Player 2 |" in result
    assert "## 投票结果" in result
    assert "- **放逐**: Player 2，获得 2 票" in result
    assert f"## 第{round_num}轮结束" in result


def test_generate_day_record_peaceful_night():
    round_num = 2
    morning_deaths = []
    speeches = []
    votes = {}
    eliminated = None

    result = generate_day_record(round_num, morning_deaths, speeches, votes, eliminated)

    assert "- **昨晚**: 平安夜，无人死亡" in result
    assert "- **平票**: 无人被放逐" in result


def test_generate_day_record_multiple_deaths():
    round_num = 1
    morning_deaths = [{"name": "Player 3"}, {"name": "Player 4"}]
    speeches = []
    votes = {}
    eliminated = None

    result = generate_day_record(round_num, morning_deaths, speeches, votes, eliminated)

    assert "- **昨晚倒牌**: Player 3 死亡" in result
    assert "- **昨晚倒牌**: Player 4 死亡" in result


def test_generate_night_record_seer():
    action = {"target": "Player 2", "result": "WEREWOLF", "thoughts": "I knew it!"}
    result = generate_night_record("Player 1", "seer", 1, action)
    assert "# 第1夜 - 预言家行动" in result
    assert "- **查验目标**: Player 2" in result
    assert "- **查验结果**: WEREWOLF" in result
    assert "- I knew it!" in result


def test_generate_night_record_witch():
    action = {"victim": "Player 3", "save_used": True, "poison_used": False, "reasoning": "Save the king."}
    result = generate_night_record("Player 2", "witch", 1, action)
    assert "# 第1夜 - 女巫行动" in result
    assert "- **今晚受害者**: Player 3" in result
    assert "- **解药**: 已使用（救下 Player 3）" in result
    assert "- **毒药**: 未使用" in result


def test_generate_night_record_werewolf():
    action = {"target": "Player 5", "discussion": "Kill the loud one.", "reasoning": "Strategy."}
    result = generate_night_record("Player 3", "werewolf", 1, action)
    assert "# 第1夜 - 狼人行动" in result
    assert "- Kill the loud one." in result
    assert "- **击杀目标**: Player 5" in result


def test_generate_night_record_guard():
    action = {"target": "Player 1", "thoughts": "Protecting the seer."}
    result = generate_night_record("Player 4", "guard", 1, action)
    assert "# 第1夜 - 守卫行动" in result
    assert "- **守护目标**: Player 1" in result


def test_generate_night_record_villager():
    result = generate_night_record("Player 5", "villager", 1)
    assert "# 第1夜 - 村民行动" in result
    assert "夜间，无特殊能力，等待天亮。" in result
