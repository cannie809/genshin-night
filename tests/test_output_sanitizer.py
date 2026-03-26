import pytest
from backend.ai.output_sanitizer import parse_json_response, sanitize_speech

def test_parse_json_response_direct():
    """Test Strategy 1: Direct JSON parse."""
    response = '{"key": "value", "number": 123}'
    result = parse_json_response(response)
    assert result == {"key": "value", "number": 123}

    # Should fail for invalid JSON
    assert parse_json_response('{"key": "value"') is None

def test_parse_json_response_code_block():
    """Test Strategy 2: Extract JSON from markdown code blocks."""
    # With json tag
    response = "Here is the result:\n```json\n{\"key\": \"value\"}\n```\nHope it helps."
    assert parse_json_response(response) == {"key": "value"}

    # Without json tag
    response = "```\n{\"foo\": \"bar\"}\n```"
    assert parse_json_response(response) == {"foo": "bar"}

def test_parse_json_response_regex():
    """Test Strategy 3: Find JSON object with regex and repair."""
    # SURROUNDED by text
    response = "The answer is {\"key\": \"value\"} strictly."
    assert parse_json_response(response) == {"key": "value"}

    # Repair: trailing comma in object
    response = '{"key": "value",}'
    assert parse_json_response(response) == {"key": "value"}

    # Repair: trailing comma in array (though regex search finds first { block, it still tests repair)
    response = '{"list": [1, 2,]}'
    assert parse_json_response(response) == {"list": [1, 2]}

    # Repair: unquoted keys
    response = '{key: "value", another_key: 123}'
    assert parse_json_response(response) == {"key": "value", "another_key": 123}

def test_parse_json_response_field_extraction():
    """Test Strategy 4: Line-by-line field extraction."""
    required_fields = ["name", "role"]

    # Standard format
    response = "name: 凯亚\nrole: 骑士"
    result = parse_json_response(response, required_fields=required_fields)
    assert result == {"name": "凯亚", "role": "骑士"}

    # Quoted format
    response = '"name": "迪卢克"\n"role": "正义人"'
    result = parse_json_response(response, required_fields=required_fields)
    assert result == {"name": "迪卢克", "role": "正义人"}

    # Chinese colon
    response = "name：琴\nrole：团长"
    result = parse_json_response(response, required_fields=required_fields)
    assert result == {"name": "琴", "role": "团长"}

    # Partial match (at least half)
    response = "name: 芭芭拉\nother: stuff"
    result = parse_json_response(response, required_fields=required_fields)
    assert result is None # Only 1 out of 2 found

def test_parse_json_response_validation():
    """Test validation of required fields and field validators."""
    required_fields = ["action", "target"]
    field_validators = {"action": ["kill", "save", "investigate"]}

    # Valid
    response = '{"action": "kill", "target": "player1"}'
    assert parse_json_response(response, required_fields, field_validators) == {"action": "kill", "target": "player1"}

    # Missing required field
    response = '{"action": "kill"}'
    assert parse_json_response(response, required_fields, field_validators) is None

    # Invalid value
    response = '{"action": "dance", "target": "player1"}'
    assert parse_json_response(response, required_fields, field_validators) is None

def test_parse_json_response_value_sanitization():
    """Test sanitization of string values in JSON."""
    response = '{"thought": "kill_reason: <analysis>I suspect them</analysis> **They are suspicious**"}'
    result = parse_json_response(response)
    # 1. tags removed: <analysis>...</analysis> is removed by _sanitize_json_value using re.sub(r"</?[a-zA-Z]...>", "", value)
    # Note: _sanitize_json_value only removes tags, not content of analysis block UNLESS it's using the same regex as sanitize_speech.
    # Looking at _sanitize_json_value in output_sanitizer.py:
    # value = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]*[^>]*>", "", value)
    # It removes tags, so <analysis> and </analysis> are gone, but "I suspect them" remains.
    # 2. kill_reason: prefix removed
    # 3. ** markers removed
    assert result == {"thought": "I suspect them They are suspicious"}


def test_sanitize_speech_tags_and_headers():
    """Test removal of tags and markdown headers."""
    # Analysis block removal
    text = "<analysis>I suspect Bob</analysis>我不确定谁是狼人。"
    assert sanitize_speech(text) == "我不确定谁是狼人"

    # Other tags removal
    text = "<i>你好</i>，我是凯亚。"
    assert sanitize_speech(text) == "你好，我是凯亚"

    # Markdown headers removal
    text = "# Speech\n我是迪卢克。"
    assert sanitize_speech(text) == "我是迪卢克"

def test_sanitize_speech_english_filtering():
    """Test filtering of English content."""
    # Pure English lines
    text = "Hello world\n我是西风骑士团的琴。\nI am the Acting Grand Master."
    assert sanitize_speech(text) == "我是西风骑士团的琴"

    # Long English words
    text = "我是西风骑士团的琴，supercalifragilistic。"
    assert sanitize_speech(text) == "我是西风骑士团的琴"

    # English phrases
    text = "我是西风骑士团的琴，i suspect you are a werewolf。"
    assert sanitize_speech(text) == "我是西风骑士团的琴"

def test_sanitize_speech_meta_prefixes():
    """Test removal of meta-information prefixes."""
    # It removes the meta prefix AND the clause following it until punctuation
    text = "策略笔记：我们要先投出迪卢克。所以我会带节奏。"
    assert sanitize_speech(text) == "所以我会带节奏"

    text = "Playbook: Keep low profile. 我是西风骑士团的芭芭拉。"
    # Playbook: Keep low profile. (1 phrase)
    # 我是西风骑士团的芭芭拉。 (7 chars)
    # Wait, "Keep low profile" has 3 spaces, so it's a 3-word phrase? No, it's 3 words.
    # Regex: r'(?<![A-Za-z])[A-Za-z]+\s+[A-Za-z]+\s+[A-Za-z]+(?![A-Za-z])'
    # "Keep low profile" matches this.
    text = "策略笔记：我们要先投出迪卢克。我是西风骑士团的芭芭拉。"
    assert sanitize_speech(text) == "我是西风骑士团的芭芭拉"

    text = "声音检查：一切正常。你好呀朋友。"
    assert sanitize_speech(text) == "你好呀朋友"

def test_sanitize_speech_cleanup_and_validation():
    """Test whitespace, punctuation cleanup and Chinese validation."""
    # Cleanup
    text = "  你好呀朋友   。 。 "
    assert sanitize_speech(text) == "你好呀朋友"

    # Chinese content validation (needs at least 4 chars)
    assert sanitize_speech("你好。") is None
    assert sanitize_speech("你好呀。") is None
    assert sanitize_speech("你好呀朋友。") == "你好呀朋友"
