"""Output sanitization and parsing for LLM responses.

This module provides robust post-processing for LLM outputs:
- Speech text: strip English, tags, meta-information leaks
- JSON responses: extract, validate, and repair JSON with fallback parsing
"""

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)


# ── Speech Sanitizer ──────────────────────────────────────────────


def sanitize_speech(text: str) -> str | None:
    """Clean speech text: remove English, tags, and meta-info leaks.

    Args:
        text: Raw speech text from LLM

    Returns:
        Cleaned speech text, or None if nothing meaningful remains
    """
    if not text:
        return None

    # 1. Strip all XML/HTML-like tags (including content of <analysis> blocks)
    text = re.sub(r"<analysis>.*?</analysis>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]*[^>]*>", "", text)

    # 2. Remove markdown-style meta headers that shouldn't appear in speech
    text = re.sub(r"^#+\s.*$", "", text, flags=re.MULTILINE)

    # 3. Remove lines that are purely English (keep mixed Chinese+English lines)
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that are 100% ASCII (pure English/code)
        if stripped.isascii() and len(stripped) > 3:
            continue
        cleaned_lines.append(stripped)
    text = "\n".join(cleaned_lines)

    # 4. Remove English words/sentences embedded in Chinese text
    # Keep: numbers, punctuation, short English names (≤8 chars)
    # Remove: English phrases (>8 chars or multiple English words)
    text = re.sub(r'\b[A-Za-z]{9,}\b', '', text)
    text = re.sub(r'(?<![A-Za-z])[A-Za-z]+\s+[A-Za-z]+\s+[A-Za-z]+(?![A-Za-z])', '', text)

    # 5. Remove meta-information leaks (only the meta prefix + its clause, not the whole line)
    meta_patterns = [
        r"策略笔记[：:][^。！？\n]*[。]?",
        r"Playbook[：:][^。！？\n]*[。]?",
        r"知识库[：:][^。！？\n]*[。]?",
        r"\[?内部(?:推理|分析)\]?[：:][^。！？\n]*[。]?",
        r"声音检查[：:][^。！？\n]*[。]?",
    ]
    for pattern in meta_patterns:
        text = re.sub(pattern, "", text)

    # 6. Clean up extra whitespace and punctuation
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"[，。]{2,}", "。", text)
    text = text.strip().strip("，。 ")

    # 7. Validate: must have meaningful Chinese content
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    if len(chinese_chars) < 4:
        return None

    return text


# ── JSON Parser ───────────────────────────────────────────────────


def parse_json_response(
    response: str,
    required_fields: list[str] | None = None,
    field_validators: dict[str, list[str]] | None = None,
) -> dict[str, Any] | None:
    """Extract and validate JSON from LLM response.

    Tries multiple strategies:
    1. Direct JSON parse
    2. Extract JSON from markdown code blocks
    3. Find JSON object with regex
    4. Line-by-line field extraction as last resort

    Args:
        response: Raw LLM response text
        required_fields: List of field names that must be present
        field_validators: Dict mapping field names to lists of valid values
            (empty list means any non-empty value is ok)

    Returns:
        Parsed and validated dict, or None if all strategies fail
    """
    if not response or not response.strip():
        return None

    result = None

    # Strategy 1: Direct parse
    result = _try_direct_parse(response)

    # Strategy 2: Extract from code block
    if result is None:
        result = _try_code_block_parse(response)

    # Strategy 3: Regex extraction
    if result is None:
        result = _try_regex_parse(response)

    # Strategy 4: Line-by-line field extraction
    if result is None and required_fields:
        result = _try_field_extraction(response, required_fields)

    if result is None:
        return None

    # Validate required fields
    if required_fields:
        for field in required_fields:
            if field not in result or not result[field]:
                log.warning(f"[JSONParser] Missing required field: {field}")
                return None

    # Validate field values
    if field_validators:
        for field, valid_values in field_validators.items():
            if field in result and valid_values:
                if result[field] not in valid_values:
                    log.warning(
                        f"[JSONParser] Invalid value for {field}: "
                        f"{result[field]}, expected one of {valid_values}"
                    )
                    return None

    # Sanitize string values: remove English phrases from Chinese text fields
    for key, val in result.items():
        if isinstance(val, str):
            result[key] = _sanitize_json_value(val)

    return result


def _try_direct_parse(response: str) -> dict | None:
    """Try parsing the entire response as JSON."""
    text = response.strip()
    # Remove leading/trailing non-JSON content
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return None


def _try_code_block_parse(response: str) -> dict | None:
    """Extract JSON from markdown code blocks."""
    patterns = [
        r"```json\s*\n(.*?)\n\s*```",
        r"```\s*\n(.*?)\n\s*```",
    ]
    for pattern in patterns:
        match = re.search(pattern, response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue
    return None


def _try_regex_parse(response: str) -> dict | None:
    """Find a JSON object using regex."""
    # Find the first { ... } block
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            # Try fixing common issues
            text = match.group(0)
            # Fix trailing commas
            text = re.sub(r",\s*}", "}", text)
            text = re.sub(r",\s*]", "]", text)
            # Fix unquoted keys
            text = re.sub(r"(\w+)\s*:", r'"\1":', text)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
    return None


def _try_field_extraction(response: str, fields: list[str]) -> dict | None:
    """Last resort: extract field values from free-form text."""
    result = {}
    for field in fields:
        # Look for "field": "value" or field: value patterns
        patterns = [
            rf'"{re.escape(field)}"\s*:\s*"([^"]*)"',
            rf"{re.escape(field)}\s*[：:]\s*(.+?)(?:\n|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, response)
            if match:
                result[field] = match.group(1).strip()
                break

    # Only return if we found at least half the fields
    if len(result) >= len(fields) / 2:
        return result
    return None


def _sanitize_json_value(value: str) -> str:
    """Sanitize a string value from JSON response.

    Remove pure English phrases, tags, and duplicate field-name prefixes.
    """
    if not value:
        return value
    # Remove any residual XML tags
    value = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]*[^>]*>", "", value)
    # Remove field-name echo prefixes (e.g., "kill_reason: ..." or "kill_reason：...")
    value = re.sub(r"^[a-z_]+\s*[：:]\s*", "", value)
    # Remove markdown bold/italic markers
    value = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", value)
    return value.strip()
