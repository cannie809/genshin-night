import pytest
import json
from backend.ai.output_sanitizer import parse_json_response

def test_parse_json_response_unquoted_keys():
    """Test that unquoted keys are correctly repaired."""
    response = '{name: "Jules", role: "Engineer"}'
    expected = {"name": "Jules", "role": "Engineer"}
    result = parse_json_response(response)
    assert result == expected

def test_parse_json_response_with_url():
    """Test that URLs are not corrupted during JSON repair.

    This specifically tests that 'http:' in a URL value is not treated as an unquoted key.
    We include a trailing comma to force the repair logic to trigger.
    """
    response = '{"url": "http://example.com",}'
    expected = {"url": "http://example.com"}
    result = parse_json_response(response)
    assert result == expected

def test_parse_json_response_trailing_comma():
    """Test that trailing commas are correctly repaired."""
    response = '{"key": "value",}'
    expected = {"key": "value"}
    result = parse_json_response(response)
    assert result == expected

def test_parse_json_response_nested():
    """Test repair logic on nested objects."""
    response = '{outer: {inner: "value",},}'
    expected = {"outer": {"inner": "value"}}
    result = parse_json_response(response)
    assert result == expected

def test_parse_json_response_valid():
    """Test that valid JSON is parsed correctly without changes."""
    response = '{"key": "value"}'
    expected = {"key": "value"}
    result = parse_json_response(response)
    assert result == expected
