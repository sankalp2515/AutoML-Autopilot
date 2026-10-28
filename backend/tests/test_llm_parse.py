"""Regression: the recurring 'LLM write failed: Extra data' crash.

The LLM appended a second object / trailing prose after valid JSON; the old parser
called json.loads on the whole thing and raised "Extra data". The parser now uses
raw_decode and takes the FIRST complete JSON value.
"""
import pytest

from app.core.llm import LLMClient


@pytest.fixture
def client():
    return LLMClient()


def test_extra_data_after_object(client):
    assert client._parse_json('{"a": 1}\n{"b": 2}') == {"a": 1}


def test_trailing_prose(client):
    assert client._parse_json('{"a": 1, "b": [1,2]}\nThat is my answer.') == {"a": 1, "b": [1, 2]}


def test_leading_prose(client):
    assert client._parse_json('Here is the JSON you asked for:\n{"x": 5}') == {"x": 5}


def test_code_fence_with_extra(client):
    assert client._parse_json('```json\n{"a": 1}\n```\nextra text after') == {"a": 1}


def test_plain_object(client):
    assert client._parse_json('{"ok": true}') == {"ok": True}


def test_invalid_raises_valueerror(client):
    with pytest.raises(ValueError):
        client._parse_json("not json at all")
