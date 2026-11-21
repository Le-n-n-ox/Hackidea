"""Unit tests: JSON extraction, validation/retry, URL normalization, dedup."""
import pytest

from hackidea.llm import extract_json, MockLLM, LLMError
from hackidea.search import normalize_url
from hackidea.stages.ideation import dedup_ideas, idea_similarity, SIMILARITY_THRESHOLD
from hackidea.stages.adversarial import tournament_wins, _scaled_scores, _distinct_finalists, _fallback_review
from hackidea.stages.scoring import DEFAULT_WEIGHTS


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


def test_dedup_uses_user_problem_not_only_names():
    a = {"name": "AgentMilk", "problem": "Dairy coops lose payout records between collection and settlement",
         "target_user": "dairy collection clerks", "pitch": "reconcile farmer payouts",
         "ai_core": "entity resolution across voice notes and ledgers"}
    near = {**a, "name": "MilkAssist", "pitch": "automatically reconcile farmer payouts"}
    different = {"name": "SchoolFees", "problem": "Schools cannot match remittances to student balances",
         "target_user": "school bursars", "pitch": "match student fees", "ai_core": "receipt OCR"}
    assert idea_similarity(a, near) >= .42
    assert len(dedup_ideas([a, near, different])) == 2


def test_dedup_rejects_near_duplicate_mobile_money_prompt_scam_ideas():
    ping_guard = {
        "name": "PINGuard",
        "problem": "Scammers send unsolicited M-Pesa PIN prompts to trick customers into authorizing fraudulent transactions, causing massive financial loss.",
        "target_user": "Low-literacy M-Pesa users in Kenya who receive frequent PIN prompts and lack the ability to verify legitimacy independently.",
        "pitch": "An AI agent that monitors incoming M-Pesa PIN prompts, verifies legitimacy against user history and context, and auto-decides approve or block, notifying the user and Safaricom in real time.",
        "ai_core": "Multi-step agent that receives PIN prompt notification, pulls recent transaction history and device context, then runs a decision model to approve/flag.",
    }
    pesa_shield = {
        "name": "PesaShield",
        "problem": "Low-literacy M-Pesa users in Kenya cannot verify if a received PIN prompt is legitimate, leading to fraudulent transaction authorization",
        "target_user": "Low-literacy M-Pesa customers in Kenya who receive unsolicited PIN prompts on basic phones",
        "pitch": "Real-time fraud prevention for M-Pesa users on cheap phones without internet",
        "ai_core": "On-device lightweight decision engine that runs locally on the phone, using recent USSD transaction history stored in a local SQLite database and simple rule-based checks against known fraud patterns, with SMS-based alerts via Twilio for fraud notifications",
    }
    assert idea_similarity(ping_guard, pesa_shield) >= SIMILARITY_THRESHOLD
    assert dedup_ideas([ping_guard, pesa_shield]) == [ping_guard]


def test_duplicate_names_are_always_collapsed():
    assert len(dedup_ideas([{"name": "FloatGuard", "pitch": "one"},
                            {"name": " floatguard ", "pitch": "different"}])) == 1


def test_tournament_wins_ignores_invalid():
    matches = [
        {"a": "c01", "b": "c02", "winner": "c01"},
        {"a": "c01", "b": "c03", "winner": "c03"},
        {"a": "c02", "b": "c03", "winner": "c99"},  # invalid winner id
        {"a": "c02", "b": "c03", "winner": "c01"},  # winner not in pair
    ]
    wins = tournament_wins(matches, ["c01", "c02", "c03"])
    assert wins == {"c01": 1, "c02": 0, "c03": 1}


def test_stability_scores_correct_entire_zero_to_one_response():
    dimensions = list(DEFAULT_WEIGHTS)
    data = {"scores": [{"id": identity, **{d: value for d in dimensions}}
                       for identity, value in (("c01", .898), ("c02", .783))]}
    totals, corrected = _scaled_scores(data, {"c01", "c02"}, DEFAULT_WEIGHTS)
    assert corrected is True
    assert totals == {"c01": 8.98, "c02": 7.83}


def test_stability_rejects_missing_candidate_and_unique_finalists_skip_duplicates():
    with pytest.raises(ValueError, match="omitted"):
        _scaled_scores({"scores": [{"id": "c01", **{d: 8 for d in DEFAULT_WEIGHTS}}]},
                       {"c01", "c02"}, DEFAULT_WEIGHTS)
    candidates = [{"id": "c01", "name": "FloatGuard", "pitch": "voice float alerts"},
                  {"id": "c02", "name": "FloatGuard", "pitch": "same name different pitch"},
                  {"id": "c03", "name": "ClinicLens", "pitch": "medicine expiry from shelf photos"}]
    rows = [{"id": x, "weighted_total": 9-i} for i, x in enumerate(("c01", "c02", "c03"))]
    assert [x["id"] for x in _distinct_finalists(rows, candidates, 2)] == ["c01", "c03"]


def test_invalid_redteam_response_keeps_all_risk_categories_reviewable():
    review = _fallback_review("c03")
    assert review["id"] == "c03"
    assert {risk["category"] for risk in review["risks"]} == {
        "technical", "api_rate_limit", "data", "demo_failure", "already_exists"
    }
    assert all(risk["severity"] == "medium" for risk in review["risks"])
