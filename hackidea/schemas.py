"""JSON schemas for every structured LLM output + validation helpers."""
from __future__ import annotations

import jsonschema

from hackidea.config import DEFAULT_WEIGHTS

_NUM_0_10 = {"type": "number", "minimum": 0, "maximum": 10}

CONTEXT_SCHEMA = {
    "type": "object",
    "required": ["theme"],
    "properties": {
        "theme": {"type": "string"},
        "hackathon_name": {"type": "string"},
        "tracks": {"type": "array", "items": {"type": "string"}},
        "sponsors": {"type": "array", "items": {"type": "string"}},
        "sponsor_apis": {"type": "array", "items": {"type": "string"}},
        "judging_criteria": {"type": "object", "additionalProperties": {"type": "number"}},
        "duration_hours": {"type": ["number", "null"]},
        "team": {"type": "array", "items": {
            "type": "object",
            "required": ["role"],
            "properties": {"role": {"type": "string"}, "skills": {"type": "array", "items": {"type": "string"}}},
        }},
        "preferred_tech": {"type": "array", "items": {"type": "string"}},
        "banned_tech": {"type": "array", "items": {"type": "string"}},
        "budget_constraints": {"type": "string"},
        "audience_region": {"type": "string"},
        "extra_notes": {"type": "string"},
    },
}

QUERY_PLAN_SCHEMA = {
    "type": "object",
    "required": ["queries"],
    "properties": {"queries": {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "required": ["query", "source_type"],
        "properties": {
            "query": {"type": "string", "minLength": 5},
            "source_type": {"type": "string"},
            "rationale": {"type": "string"},
        },
    }}},
}

CANDIDATES_SCHEMA = {
    "type": "object",
    "required": ["candidates"],
    "properties": {"candidates": {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "required": ["name", "pitch", "target_user", "ai_core", "why_now", "angle"],
        "properties": {
            "name": {"type": "string", "minLength": 2},
            "pitch": {"type": "string", "minLength": 10},
            "target_user": {"type": "string"},
            "ai_core": {"type": "string", "minLength": 10},
            "why_now": {"type": "string"},
            "angle": {"type": "string"},
            "data_needed": {"type": "string"},
            "sponsor_fit": {"type": "string"},
        },
    }}},
}

FILTER_SCHEMA = {
    "type": "object",
    "required": ["decisions"],
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "required": ["id", "keep", "reason"],
        "properties": {
            "id": {"type": "string"},
            "keep": {"type": "boolean"},
            "reason": {"type": "string"},
            "category": {"type": "string", "enum": ["ok", "infeasible", "wrapper", "saturated", "offtheme"]},
            "needs_saturation_check": {"type": "boolean"},
        },
    }}},
}

WEIGHTS_SCHEMA = {
    "type": "object",
    "required": ["weights", "rationale"],
    "properties": {
        "weights": {"type": "object", "required": list(DEFAULT_WEIGHTS),
                    "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1}},
        "rationale": {"type": "string"},
    },
}

SCORES_SCHEMA = {
    "type": "object",
    "required": ["scores"],
    "properties": {"scores": {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "required": ["id", "theme_fit", "ai_depth", "feasibility", "novelty", "demo_impact", "real_world_impact", "rationale"],
        "properties": {
            "id": {"type": "string"},
            "theme_fit": _NUM_0_10, "ai_depth": _NUM_0_10, "feasibility": _NUM_0_10,
            "novelty": _NUM_0_10, "demo_impact": _NUM_0_10, "real_world_impact": _NUM_0_10,
            "rationale": {"type": "string"},
        },
    }}},
}

REDTEAM_SCHEMA = {
    "type": "object",
    "required": ["reviews"],
    "properties": {"reviews": {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "required": ["id", "risks", "strongest_objection"],
        "properties": {
            "id": {"type": "string"},
            "risks": {"type": "array", "minItems": 1, "items": {
                "type": "object",
                "required": ["category", "risk", "severity", "mitigation"],
                "properties": {
                    "category": {"type": "string", "enum": ["technical", "api_rate_limit", "data", "demo_failure", "already_exists", "other"]},
                    "risk": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "mitigation": {"type": "string"},
                },
            }},
            "strongest_objection": {"type": "string"},
        },
    }}},
}

TOURNAMENT_SCHEMA = {
    "type": "object",
    "required": ["matches"],
    "properties": {"matches": {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "required": ["a", "b", "winner", "reasoning"],
        "properties": {
            "a": {"type": "string"}, "b": {"type": "string"},
            "winner": {"type": "string"},
            "reasoning": {"type": "string", "minLength": 20},
        },
    }}},
}

DECISION_SCHEMA = {
    "type": "object",
    "required": ["winner_id", "confidence_level", "confidence_pct", "key_assumption", "why_beats_others", "rationale"],
    "properties": {
        "winner_id": {"type": "string"},
        "confidence_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "key_assumption": {"type": "string", "minLength": 10},
        "why_beats_others": {"type": "array", "minItems": 3, "maxItems": 5, "items": {
            "type": "object",
            "required": ["point"],
            "properties": {"point": {"type": "string"}, "evidence_url": {"type": "string"}},
        }},
        "rationale": {"type": "string"},
    },
}


def validate(data: dict, schema: dict) -> None:
    """Raise jsonschema.ValidationError with a short message on failure."""
    jsonschema.validate(data, schema)