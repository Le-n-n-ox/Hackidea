import copy
import re

import pytest

from hackidea.blueprint_document import (
    ARCHITECTURE,
    BLUEPRINT_SECTIONS,
    enforce_document,
    enforce_idea,
    event_date_from_context,
    section_defaults,
    team_aliases,
    validate_timeline,
)
from hackidea.report import _architecture_svg, replace_idea_section
from hackidea import schemas
from hackidea.prompts import blueprint_section


@pytest.fixture
def document_parts():
    ctx = {
        "duration_hours": 12,
        "raw_context": "Africa's Talking hospitality hackathon, November 26, team of three",
        "team": [{"role": "Generalist", "skills": ["Python"]},
                 {"role": "Generalist", "skills": ["design"]},
                 {"role": "Generalist", "skills": ["pitching"]}],
    }
    winner = {"id": "c01", "name": "Rural Tour Logistics Agent",
              "target_user": "small rural tour operators",
              "pitch": "Build a booking desk that checks tour availability and sends a test confirmation."}
    team = team_aliases(ctx)
    date_value = event_date_from_context(ctx)
    content = {name: section_defaults(name, ctx, winner, team, date_value)
               for name in BLUEPRINT_SECTIONS}
    decision = {"confidence_level": "low"}
    content = enforce_document(content, ctx, winner, {"name": "Tour Match"}, decision)
    for name, schema in BLUEPRINT_SECTIONS.items():
        schemas.validate(content[name], schema)
    return ctx, winner, content


def test_timeline_covers_duration_and_uses_consistent_team_aliases(document_parts):
    _, _, content = document_parts
    validate_timeline(content)
    aliases = set(content["team_aliases"])
    assert {row["owner"] for row in content["timeline"]["calendar"]} <= aliases
    assert {row["owner"] for row in content["team_workstreams"]} == aliases
    assert content["data"]["fixtures"][0]["date"] == "2026-11-26"


def test_timeline_validator_rejects_gaps_and_unknown_owners(document_parts):
    _, _, content = document_parts
    broken = copy.deepcopy(content)
    broken["timeline"]["calendar"][2]["start"] += 0.5
    with pytest.raises(ValueError, match="gap or overlap"):
        validate_timeline(broken)
    broken = copy.deepcopy(content)
    broken["timeline"]["calendar"][0]["owner"] = "Member 99"
    with pytest.raises(ValueError, match="owner"):
        validate_timeline(broken)


def test_architecture_svg_is_generated_from_all_nodes_and_edges():
    svg = _architecture_svg()
    assert svg.count("<rect ") == len(ARCHITECTURE["nodes"])
    assert svg.count("marker-end=\"url(#arrow)\"") == len(ARCHITECTURE["edges"])
    for node in ARCHITECTURE["nodes"]:
        assert node["label"] in svg


def test_model_latex_arrows_are_normalized(document_parts):
    _, winner, content = document_parts
    content["timeline"]["milestones"][0] = r"Parse request $\rightarrow$ check seats"
    fixed = enforce_document(content, {"duration_hours": 12, "team": [{"role": "generalist"}]},
                             winner, None, {"confidence_level": "medium"})
    assert "\\rightarrow" not in fixed["timeline"]["calendar"][0]["task"]
    assert re.search(r"Parse request to check seats", fixed["timeline"]["calendar"][0]["task"])


def test_idea_default_uses_the_winning_candidate_instead_of_generic_safety_copy(document_parts):
    ctx, winner, content = document_parts
    content["idea"] = {
        "explanation": "A prototype that reviews synthetic examples related to this idea and flags cases for a person to review; it does not access real accounts or take external actions.",
        "example_input": "A synthetic example.", "example_output": "A person reviews a flag.",
        "ai_action": "Provide an advisory explanation.", "scope": "Use synthetic examples.",
    }
    fixed = enforce_document(content, ctx, winner, None, {"confidence_level": "medium"})
    assert winner["pitch"] in fixed["idea"]["explanation"]
    assert "flags cases for a person to review" not in fixed["idea"]["explanation"]


def test_blueprint_idea_prompt_omits_stale_demo_scope_and_preserves_domain():
    winner = {
        "id": "c10", "name": "Local Language Concept Explainer via SMS",
        "pitch": "An AI tutor explains STEM concepts in simple Kiswahili by SMS.",
        "problem": "Learners may struggle with complex STEM wording.",
        "target_user": "Working women in Nairobi studying STEM",
        "ai_core": "Rewrite a short concept note in plain Kiswahili.",
        "demo_scope": "A prototype that reviews synthetic examples and flags cases for a person to review.",
    }
    ctx = {"theme": "Edtech Solutions", "duration_hours": 11,
           "sponsors": ["Africa's Talking"], "team": []}
    stack = {key: "unused" for key in ("frontend", "backend", "database", "primary_ai", "sms", "hosting")}
    _, prompt = blueprint_section(ctx, winner, "idea", stack, [], None)
    assert winner["pitch"] in prompt
    assert "A prototype that reviews synthetic examples" not in prompt
    assert "synthetic tour fixtures" not in prompt
    assert "what the named user does" in prompt


def test_older_idea_section_can_be_regenerated_from_structured_content(document_parts):
    ctx, winner, _ = document_parts
    winner = {**winner, "name": "Local Language Concept Explainer via SMS",
              "target_user": "working women in Nairobi studying STEM"}
    idea = {"explanation": "A learner texts a STEM topic and gets a short explanation in Kiswahili.",
            "example_input": "EXPLAIN PHOTOSYNTHESIS SW",
            "example_output": "Plants use sunlight and water to make food.",
            "ai_action": "The AI rewrites a reviewed concept note in simpler words.",
            "scope": "Demo five educator-reviewed topics through the SMS sandbox."}
    legacy = ("## 1. Idea\nName: Local Language Concept Explainer\n"
              "Pitch: A prototype that reviews synthetic examples and flags cases.\n\n"
              "## 2. Why it wins\nThe existing report continues here.\n")
    updated = replace_idea_section(legacy, winner, idea)
    assert "A learner texts a STEM topic" in updated
    assert "**Example input:** EXPLAIN PHOTOSYNTHESIS SW" in updated
    assert "prototype that reviews synthetic examples" not in updated.lower()
    assert "## 2. Why it wins" in updated
