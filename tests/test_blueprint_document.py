import copy
import re

import pytest

from hackidea.blueprint_document import (
    ARCHITECTURE,
    BLUEPRINT_SECTIONS,
    enforce_document,
    event_date_from_context,
    section_defaults,
    team_aliases,
    validate_timeline,
)
from hackidea.report import _architecture_svg
from hackidea import schemas


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
