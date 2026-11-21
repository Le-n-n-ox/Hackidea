"""Unit tests: JSON extraction, validation/retry, URL normalization, dedup."""
import pytest

from hackidea.llm import extract_json, MockLLM, LLMError
from hackidea.search import normalize_url
from hackidea.stages.ideation import dedup_ideas, idea_similarity
from hackidea.stages.adversarial import tournament_wins


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('Here you go:\n```json\n{"a": [1, 2]}\n```\nEnjoy!') == {"a": [1, 2]}


def test_extract_json_prose_wrapped():
    assert extract_json('Sure! {"a": {"b": "x}y"}} done') == {"a": {"b": "x}y"}}


def test_extract_json_array():
    assert extract_json("prefix [1, 2, 3] suffix") == [1, 2, 3]


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError):
        extract_json("no json here at all")
    with pytest.raises(ValueError):
        extract_json("")


def test_complete_json_retries_on_malformed():
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        return "oops not json" if calls["n"] == 1 else '{"x": 5}'

    llm = MockLLM({"demo": flaky})
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "number"}}}
    out = llm.complete_json("sys", "user", schema, stage="demo")
    assert out == {"x": 5}
    assert calls["n"] == 2


def test_complete_json_gives_up():
    llm = MockLLM({"demo": "always broken"})
    schema = {"type": "object", "required": ["x"]}
    with pytest.raises(LLMError):
        llm.complete_json("sys", "user", schema, stage="demo", tries=2)


def test_normalize_url():
    a = normalize_url("http://www.Example.com/path/?utm_source=x&id=7#frag")
    b = normalize_url("https://example.com/path?id=7")
    assert a == b == "https://example.com/path?id=7"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_dedup_ideas():
    a = {"name": "MilkFlow", "pitch": "voice AI for dairy cooperative logistics"}
    b = {"name": "MilkFlow II", "pitch": "voice AI for dairy cooperative logistics"}
    c = {"name": "SchoolFee", "pitch": "USSD agent negotiating school fee payments"}
    assert idea_similarity(a, b) > 0.75
    assert idea_similarity(a, c) < 0.5
    assert dedup_ideas([a, b, c]) == [a, c]


def test_tournament_wins_ignores_invalid():
    matches = [
        {"a": "c01", "b": "c02", "winner": "c01"},
        {"a": "c01", "b": "c03", "winner": "c03"},
        {"a": "c02", "b": "c03", "winner": "c99"},  # invalid winner id
        {"a": "c02", "b": "c03", "winner": "c01"},  # winner not in pair
    ]
    wins = tournament_wins(matches, ["c01", "c02", "c03"])
    assert wins == {"c01": 1, "c02": 0, "c03": 1}