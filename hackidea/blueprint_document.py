"""Validated structured content and code-enforced checks for the build document."""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from hackidea import schemas


ARCHITECTURE = {
    "nodes": [
        {"id": "request", "label": "Guest request", "kind": "user"},
        {"id": "app", "label": "FastAPI booking desk", "kind": "app"},
        {"id": "ai", "label": "Gemini field extraction", "kind": "ai"},
        {"id": "db", "label": "SQLite availability", "kind": "data"},
        {"id": "sms", "label": "AT SMS sandbox", "kind": "external"},
        {"id": "result", "label": "Simulator confirmation", "kind": "user"},
    ],
    "edges": [["request", "app"], ["app", "ai"], ["ai", "app"],
               ["app", "db"], ["db", "app"], ["app", "sms"], ["sms", "result"]],
}


def _string(minimum: int = 1, maximum: int = 900) -> dict:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def _list(item: dict, minimum: int = 1, maximum: int = 8) -> dict:
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}


BLUEPRINT_SECTIONS = {
    "idea": {
        "type": "object", "required": ["explanation", "example_input", "example_output", "ai_action", "scope"],
        "properties": {
            "explanation": _string(40, 420),
            "example_input": _string(8, 180),
            "example_output": _string(12, 260),
            "ai_action": _string(20, 300),
            "scope": _string(20, 260),
        },
    },
    "summary": {
        "type": "object", "required": ["pitch", "audience", "demo_steps", "reasons", "biggest_risk", "risk_plan", "confidence_note", "first_action"],
        "properties": {
            "pitch": _string(20, 240), "audience": _string(10, 140),
            "demo_steps": _list(_string(10, 150), 3, 3),
            "reasons": _list(_string(15, 160), 2, 3),
            "biggest_risk": _string(10, 180), "risk_plan": _string(10, 180),
            "confidence_note": _string(10, 220), "first_action": _string(10, 180),
            "runner_up_name": _string(2, 120),
            "confidence_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "confidence_action": _string(10, 220),
        },
    },
    "frontend": {
        "type": "object", "required": ["flow", "screens", "polish"],
        "properties": {
            "flow": _string(40, 700),
            "screens": _list({"type": "object", "required": ["name", "purpose"], "properties": {
                "name": _string(2, 70), "purpose": _string(10, 200)}}, 2, 5),
            "polish": _list(_string(10, 150), 3, 5),
        },
    },
    "backend": {
        "type": "object", "required": ["files", "routes", "flow", "errors"],
        "properties": {
            "files": _list({"type": "object", "required": ["path", "purpose"], "properties": {
                "path": _string(2, 90), "purpose": _string(10, 180)}}, 3, 8),
            "routes": _list({"type": "object", "required": ["method", "path", "purpose"], "properties": {
                "method": {"type": "string", "enum": ["GET", "POST"]},
                "path": _string(1, 80), "purpose": _string(10, 180)}}, 2, 5),
            "flow": _list(_string(10, 180), 3, 6), "errors": _string(20, 400),
        },
    },
    "ai": {
        "type": "object", "required": ["job", "prompt", "output_fields", "evaluation", "fallback"],
        "properties": {
            "job": _string(20, 450), "prompt": _string(40, 1200),
            "output_fields": _list(_string(2, 80), 3, 8),
            "evaluation": _list({"type": "object", "required": ["case", "expected"], "properties": {
                "case": _string(10, 140), "expected": _string(10, 140)}}, 3, 5),
            "fallback": _string(20, 350),
        },
    },
    "data": {
        "type": "object", "required": ["source", "fields", "fixtures", "validation"],
        "properties": {
            "source": _string(20, 350),
            "fields": _list({"type": "object", "required": ["name", "type", "purpose"], "properties": {
                "name": _string(2, 50), "type": _string(2, 40), "purpose": _string(10, 120)}}, 4, 10),
            "fixtures": _list({"type": "object", "required": ["tour_id", "name", "location", "date", "capacity", "available"], "properties": {
                "tour_id": _string(2, 20), "name": _string(3, 80), "location": _string(3, 80),
                "date": {"type": "string", "format": "date"}, "capacity": {"type": "integer", "minimum": 1},
                "available": {"type": "integer", "minimum": 0}}}, 3, 5),
            "validation": _string(20, 350),
        },
    },
    "timeline": {
        "type": "object", "required": ["milestones", "cut_line"],
        "properties": {
            "milestones": _list(_string(10, 150), 6, 10),
            "cut_line": _string(20, 250),
            "calendar": _list({"type": "object", "required": ["start", "end", "task", "owner"],
                               "properties": {"start": {"type": "number", "minimum": 0},
                                              "end": {"type": "number", "minimum": 0},
                                              "task": _string(1, 150), "owner": _string(1, 80)}}, 1, 10),
            "total_hours": {"type": "number", "exclusiveMinimum": 0},
        },
    },
    "demo": {
        "type": "object", "required": ["steps", "pitch", "questions"],
        "properties": {
            "steps": _list({"type": "object", "required": ["minute", "show", "say"], "properties": {
                "minute": _string(3, 25), "show": _string(10, 150), "say": _string(10, 170)}}, 3, 5),
            "pitch": {"type": "object", "required": ["problem", "insight", "demo", "impact", "ask"], "properties": {
                "problem": _string(10, 160), "insight": _string(10, 160), "demo": _string(10, 160),
                "impact": _string(10, 160), "ask": _string(10, 160)}},
            "questions": _list({"type": "object", "required": ["question", "answer"], "properties": {
                "question": _string(8, 130), "answer": _string(15, 190)}}, 3, 5),
        },
    },
    "risks": {
        "type": "object", "required": ["items"],
        "properties": {"items": _list({"type": "object", "required": ["risk", "mitigation"], "properties": {
            "risk": _string(12, 180), "mitigation": _string(12, 200)}}, 3, 5)},
    },
    "appendix": {
        "type": "object", "required": ["selection"],
        "properties": {"selection": _string(40, 450)},
    },
}


def _close_schema_objects(node: dict) -> None:
    if node.get("type") == "object":
        node["additionalProperties"] = False
        for child in node.get("properties", {}).values():
            _close_schema_objects(child)
    if node.get("type") == "array":
        _close_schema_objects(node["items"])


for _section_schema in BLUEPRINT_SECTIONS.values():
    _close_schema_objects(_section_schema)


def section_defaults(name: str, ctx: dict, winner: dict, team: list[str], event_date: str) -> dict:
    """Conservative, internally consistent content when one model section fails."""
    title = winner.get("name", "the project")
    user = winner.get("target_user", "the intended user")
    duration = float(ctx.get("duration_hours") or 12)
    if name == "idea":
        pitch = str(winner.get("pitch") or winner.get("name") or "This project").strip()
        return {
            "explanation": pitch if len(pitch) >= 40 else f"{pitch} is designed for {user}.",
            "example_input": f"A sample request from {user} about the problem described in this idea.",
            "example_output": "The service returns the result described in the project pitch; verify the exact wording with a user.",
            "ai_action": str(winner.get("ai_core") or "The AI handles the task described in the project pitch."),
            "scope": "Build one end-to-end example of this specific idea, using its named user, input, and output.",
        }
    if name == "summary":
        pitch = winner.get("pitch", f"Build {title} for {user}.")
        return {"pitch": pitch, "audience": user,
                "demo_steps": ["Enter one sample request that matches the winning idea.",
                               "Show the app's AI step working on that request.",
                               "Show the specific result the intended user receives."],
                "reasons": [f"It addresses the task described in the winning idea for {user}.",
                    "One complete user request and response can be shown within the event time."],
                "biggest_risk": "The intended user may not find this workflow useful.",
                "risk_plan": "Ask one intended user or educator to try the example before adding features.",
                "confidence_note": "Treat this as a promising testable idea, not a validated business case.",
                "first_action": "Check the example with an intended user, then build the smallest complete version."}
    if name == "frontend":
        return {"flow": "One phone-sized page accepts a booking request, shows the matched tour, and displays the SMS result.",
                "screens": [{"name": "Booking desk", "purpose": "Submit a sample request and review the availability decision."},
                            {"name": "Message result", "purpose": "Show the exact text returned by the SMS sandbox."}],
                "polish": ["Use one large request box and one primary action.", "Show the matched fixture beside the result.", "Keep a visible sandbox badge beside the SMS status."]}
    if name == "backend":
        return {"files": [{"path": "main.py", "purpose": "FastAPI routes, availability check, Gemini call, and sandbox SMS."},
                          {"path": "data/tours.json", "purpose": "Small, hand-reviewed fixture set."},
                          {"path": "prompts/booking.txt", "purpose": "Strict request-to-JSON instruction."}],
                "routes": [{"method": "GET", "path": "/", "purpose": "Serve the booking desk."},
                           {"method": "GET", "path": "/api/tours", "purpose": "List the sample tours."},
                           {"method": "POST", "path": "/api/bookings", "purpose": "Parse, validate, save, and send a sandbox confirmation."}],
                "flow": ["Validate request shape.", "Ask Gemini to extract tour, date, and party size.",
                         "Check availability in SQLite before confirming.", "Save the booking and call Africa's Talking sandbox SMS."],
                "errors": "Return a readable 4xx for invalid requests; if the model or SMS call fails, keep the booking state visible and show a retry action."}
    if name == "ai":
        return {"job": "Extract only tour_id, requested_date, and party_size from the booking text; Python owns availability and confirmation decisions.",
                "prompt": "Extract a booking request from the user's text. Return JSON only with tour_id, requested_date (YYYY-MM-DD), and party_size. Use null for any missing field. Never invent an inventory match.",
                "output_fields": ["tour_id", "requested_date", "party_size"],
                "evaluation": [{"case": "Known tour, two people, fixture date", "expected": "Correct tour, date, and party size"},
                               {"case": "No date supplied", "expected": "requested_date is null"},
                               {"case": "Unknown tour", "expected": "Keep the supplied name; Python returns no match"}],
                "fallback": "If the model call fails, ask the operator to choose a tour and date from the visible form; do not claim a booking was confirmed."}
    if name == "data":
        return {"source": "Three hand-authored demonstration rows in data/tours.json; replace them with operator-approved sample listings after the demo.",
                "fields": [{"name": "tour_id", "type": "text", "purpose": "Stable fixture key."},
                           {"name": "name", "type": "text", "purpose": "Tour label shown to the operator."},
                           {"name": "location", "type": "text", "purpose": "Destination text."},
                           {"name": "date", "type": "date", "purpose": "One demo departure date."},
                           {"name": "capacity", "type": "integer", "purpose": "Seats available before requests."},
                           {"name": "available", "type": "integer", "purpose": "Seats still available."}],
                "fixtures": _default_fixtures(event_date),
                "validation": "Parse dates as ISO YYYY-MM-DD; reject party sizes below one; confirm only when requested seats are no greater than available seats."}
    if name == "timeline":
        return {"milestones": ["Agree on the one request and success condition.", "Create fixtures and database checks.",
                               "Build the booking page and API route.", "Connect structured model extraction.",
                               "Connect and test the SMS sandbox.", "Run the full demo, fix rough edges, and rehearse."],
                "cut_line": "At 7 hours, drop extra screens and languages; keep one request, one availability check, and one sandbox SMS."}
    if name == "demo":
        return {"steps": [{"minute": "0:00-0:40", "show": "The operator booking desk and the three sample tours.", "say": "This is one proposed workflow; we are testing it with realistic sample data."},
                           {"minute": "0:40-1:50", "show": "A request, extracted fields, and the availability check.", "say": "The model extracts fields; the app checks the fixture before it confirms."},
                           {"minute": "1:50-3:00", "show": "The sandbox SMS in the simulator.", "say": "This is a test message in the sponsor simulator, not a production delivery."}],
                "pitch": {"problem": "Operators may have to coordinate availability and guest messages by hand.",
                          "insight": "Separate language understanding from a deterministic capacity check.",
                          "demo": "Enter a request, verify seats, and send a sandbox confirmation.",
                          "impact": "Make one booking workflow easier to demonstrate and assess.",
                          "ask": "Help us test the flow with two local operators."},
                "questions": [{"question": "How do you prevent a false confirmation?", "answer": "Python checks the saved capacity after extraction; the model cannot approve a booking."},
                              {"question": "What happens if the model is unavailable?", "answer": "Use the visible manual selection flow and do not claim AI processed the request."},
                              {"question": "Is this connected to live customers?", "answer": "The event demo uses the Africa's Talking simulator and synthetic records."}]}
    if name == "risks":
        return {"items": [{"risk": "The operator pain is assumed because the run did not fetch direct interview or complaint evidence.", "mitigation": "Ask an operator to walk through the request before locking the pitch."},
                           {"risk": "The model may extract the wrong tour or date.", "mitigation": "Show the extracted fields and require Python's fixture check before any confirmation."},
                           {"risk": "The sandbox account or simulator may not be ready at the event.", "mitigation": "Create the sandbox app early and keep a saved request/response trace for rehearsal."}]}
    if name == "appendix":
        return {"selection": f"The recorded selection process picked {title} because it was the top idea after comparing the finalists and reviewing their main risks. The choice is still a hypothesis: test the operator workflow before adding features."}
    raise KeyError(name)


_GENERIC_IDEA_COPY = (
    "prototype that reviews synthetic examples",
    "flags cases for a person to review",
    "does not access real accounts or take external actions",
    "verify the supporting trend",
)


def enforce_idea(idea: dict, ctx: dict, winner: dict) -> dict:
    """Reject boilerplate idea prose and fall back to the actual candidate record."""
    normalized = json.loads(json.dumps(idea, ensure_ascii=False))
    text = json.dumps(normalized, ensure_ascii=False).casefold()
    if any(phrase in text for phrase in _GENERIC_IDEA_COPY):
        normalized = section_defaults(
            "idea", ctx, winner, team_aliases(ctx), event_date_from_context(ctx))
    return normalized


def _default_fixtures(event_date: str) -> list[dict]:
    return [
        {"tour_id": "TOUR-01", "name": "Forest ridge walk", "location": "Nairobi region", "date": event_date, "capacity": 6, "available": 4},
        {"tour_id": "TOUR-02", "name": "Community food trail", "location": "Nairobi region", "date": event_date, "capacity": 4, "available": 0},
        {"tour_id": "TOUR-03", "name": "Birding morning", "location": "Nairobi region", "date": event_date, "capacity": 5, "available": 3},
    ]


def event_date_from_context(ctx: dict) -> str:
    """Choose an ISO sample date near the date named in the brief, else today UTC."""
    raw = str(ctx.get("raw_context", ""))
    patterns = (r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})(?:,?\s+(20\d{2}))?\b",
                r"\b(20\d{2})-(\d{2})-(\d{2})\b")
    today = datetime.now(timezone.utc).date()
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if not match:
            continue
        try:
            if len(match.groups()) == 3 and match.group(1).isdigit():
                chosen = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            else:
                month_text, day_text, year_text = match.groups()
                month = datetime.strptime(month_text[:3].title(), "%b").month
                year = int(year_text) if year_text else today.year
                chosen = date(year, month, int(day_text))
                if not year_text and (chosen - today).days < -180:
                    chosen = chosen.replace(year=year + 1)
            return chosen.isoformat()
        except ValueError:
            continue
    return today.isoformat()


def team_aliases(ctx: dict) -> list[str]:
    team = ctx.get("team") or [{"role": "generalist"}]
    return [f"{(m.get('role') or 'Teammate').strip().title()} {i + 1}" for i, m in enumerate(team)]


def build_config(ctx: dict, docs: dict) -> dict:
    sponsor_names = " ".join(ctx.get("sponsors") or []).casefold()
    use_at = "africa's talking" in sponsor_names or "africas talking" in sponsor_names
    return {
        "frontend": "FastAPI page with one responsive HTML form and small inline JavaScript",
        "backend": "Python 3.11+ with FastAPI and Uvicorn",
        "database": "SQLite through Python's standard-library sqlite3 module",
        "primary_ai": "Google Gemini 3.5 Flash-Lite through the google-genai Python SDK",
        "sms": "Africa's Talking SMS sandbox through its official Python SDK" if use_at else "Sponsor's documented test/sandbox messaging API",
        "hosting": "Run locally on one team laptop for the event demo",
        "auth": "No sign-in screen for the synthetic-data demo",
        "research_source_ids": [s["id"] for s in docs.get("sources", [])],
    }


def normalize_model_text(value: str) -> str:
    value = re.sub(r"\$\\rightarrow\$|\\rightarrow", "to", str(value))
    value = value.replace("\r\n", "\n").strip()
    return value


def enforce_document(content: dict, ctx: dict, winner: dict, runner_up: dict | None,
                     decision: dict) -> dict:
    """Apply rules code can verify before any output format is rendered."""
    content = json.loads(json.dumps(content, ensure_ascii=False))
    team = team_aliases(ctx)
    hours = float(ctx.get("duration_hours") or 12)
    expected_count = len(team)
    schedule = content["timeline"]
    milestones = list(schedule.get("milestones", []))
    defaults = section_defaults("timeline", ctx, winner, team, event_date_from_context(ctx))["milestones"]
    if len(milestones) < 6:
        milestones.extend(defaults[len(milestones):])
    milestones = milestones[:6]
    calendar = []
    for i, task in enumerate(milestones):
        start = round(hours * i / len(milestones), 2)
        end = round(hours * (i + 1) / len(milestones), 2)
        calendar.append({"start": start, "end": end, "task": normalize_model_text(task),
                         "owner": team[i % expected_count]})
    schedule["calendar"] = calendar
    schedule["total_hours"] = hours

    fixtures = content["data"].get("fixtures", [])
    event_date = event_date_from_context(ctx)
    valid_fixture_ids = [str(row.get("tour_id", "")) for row in fixtures]
    if (len(fixtures) < 3 or len(set(valid_fixture_ids)) != len(valid_fixture_ids)
            or any(int(row.get("capacity", 0)) < 1
                   or int(row.get("available", -1)) < 0
                   or int(row.get("available", -1)) > int(row.get("capacity", 0))
                   for row in fixtures)):
        fixtures = _default_fixtures(event_date)
    for fixture in fixtures:
        fixture["date"] = event_date
        fixture["available"] = min(int(fixture["available"]), int(fixture["capacity"]))
    content["data"]["fixtures"] = fixtures[:3]
    content["sample_schema"] = {
        "tour_id": "string", "name": "string", "location": "string",
        "date": "YYYY-MM-DD", "capacity": "integer", "available": "integer",
    }
    content["team_aliases"] = team
    content["team_workstreams"] = [
        {"owner": owner, "focus": f"{(ctx.get('team') or [{}] * expected_count)[i].get('role', 'generalist').title()}: "
                                      f"{(ctx.get('team') or [{}] * expected_count)[i].get('skills', ['prototype work'])[0] if (ctx.get('team') or [{}] * expected_count)[i].get('skills') else 'prototype work'}"}
        for i, owner in enumerate(team)
    ]

    for section_name, section in content.items():
        if isinstance(section, dict):
            for key, value in list(section.items()):
                if isinstance(value, str):
                    section[key] = normalize_model_text(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            for subkey, subvalue in list(item.items()):
                                if isinstance(subvalue, str):
                                    item[subkey] = normalize_model_text(subvalue)

    # Small models can return schema-shaped prose that conflicts with the selected
    # implementation. Fall back section-by-section when it contradicts the real
    # starter, the sponsor sandbox, or the fixed structured AI fields.
    stale_summary = json.dumps(content["summary"], ensure_ascii=False).casefold()
    if any(term in stale_summary for term in (
            "prototype that reviews synthetic examples", "flags cases for a person to review",
            "does not access real accounts or take external actions", "mock external apis", "mock/local fallback")):
        content["summary"] = section_defaults("summary", ctx, winner, team, event_date)
    content["idea"] = enforce_idea(content["idea"], ctx, winner)
    sponsor_sms = "africa's talking" in str(build_config(ctx, {}).get("sms", "")).casefold()
    if sponsor_sms:
        stale_frontend = json.dumps(content["frontend"], ensure_ascii=False).casefold()
        if any(term in stale_frontend for term in ("mock", "simulat", "ussd")):
            content["frontend"] = section_defaults("frontend", ctx, winner, team, event_date)
        backend_text = json.dumps(content["backend"], ensure_ascii=False).casefold()
        required_routes = {("GET", "/"), ("GET", "/api/tours"), ("POST", "/api/bookings")}
        actual_routes = {(row.get("method"), row.get("path")) for row in content["backend"].get("routes", [])}
        actual_files = {row.get("path") for row in content["backend"].get("files", [])}
        if (not required_routes.issubset(actual_routes)
                or not {"main.py", "data/tours.json", "prompts/booking.txt"}.issubset(actual_files)
                or any(term in backend_text for term in ("mock", "simulat", "ussd"))):
            content["backend"] = section_defaults("backend", ctx, winner, team, event_date)
        content["backend"]["files"][0]["path"] = "main.py"
        expected_fields = {"tour_id", "requested_date", "party_size"}
        ai_text = json.dumps(content["ai"], ensure_ascii=False).casefold()
        if not expected_fields.issubset({str(x) for x in content["ai"].get("output_fields", [])}) or "ussd" in ai_text:
            content["ai"] = section_defaults("ai", ctx, winner, team, event_date)
        data_text = json.dumps(content["data"], ensure_ascii=False).casefold()
        validation_text = str(content["data"].get("validation", "")).casefold()
        if (any(term in data_text for term in ("sms/ussd", "sms input", "ussd", "traveler input"))
                or any(term in validation_text for term in ("phone", "e.africa", "phone number"))):
            content["data"] = section_defaults("data", ctx, winner, team, event_date)
        timeline_text = json.dumps(content["timeline"], ensure_ascii=False).casefold()
        if any(term in timeline_text for term in ("ussd", "mock external", "mock api")):
            safe_timeline = section_defaults("timeline", ctx, winner, team, event_date)
            content["timeline"]["milestones"] = safe_timeline["milestones"]
            content["timeline"]["cut_line"] = safe_timeline["cut_line"]
        schedule = content["timeline"]
        milestones = schedule["milestones"][:6]
        schedule["calendar"] = [
            {"start": round(hours * i / len(milestones), 2),
             "end": round(hours * (i + 1) / len(milestones), 2),
             "task": normalize_model_text(task), "owner": team[i % expected_count]}
            for i, task in enumerate(milestones)
        ]
        schedule["total_hours"] = hours
        demo_text = json.dumps(content["demo"], ensure_ascii=False).casefold()
        if "sandbox" not in demo_text or any(term in demo_text for term in ("mock sms", "simulated sms", "ussd")):
            content["demo"] = section_defaults("demo", ctx, winner, team, event_date)
        risks_text = json.dumps(content["risks"], ensure_ascii=False).casefold()
        if any(term in risks_text for term in ("mock", "ussd", "simulat")):
            content["risks"] = section_defaults("risks", ctx, winner, team, event_date)
        appendix_text = str(content["appendix"].get("selection", "")).casefold()
        if any(term in appendix_text for term in ("ussd", "pair wins", "score", "penalt", "tournament")):
            content["appendix"] = section_defaults("appendix", ctx, winner, team, event_date)

    content["summary"]["runner_up_name"] = (runner_up or {}).get("name", "No second finalist was recorded.")
    content["summary"]["confidence_level"] = str(decision.get("confidence_level", "medium")).lower()
    if content["summary"]["confidence_level"] == "low":
        content["summary"]["confidence_note"] = (
            "The operator problem has not been confirmed with direct local evidence; validate it before expanding the build."
        )
        content["summary"]["first_action"] = (
            "Ask one intended user to try the example, then build the smallest complete version."
        )
    content["summary"]["confidence_action"] = (
        f"Runner-up: {content['summary']['runner_up_name']}. Use /alternatives to generate its blueprint."
        if content["summary"]["confidence_level"] == "low" and runner_up
        else "Use the first hour to test the key workflow with an intended user." if content["summary"]["confidence_level"] != "high"
        else "Proceed with the one-flow prototype and keep the demo focused.")
    return content


def validate_timeline(content: dict) -> None:
    rows = content["timeline"]["calendar"]
    expected = content["timeline"]["total_hours"]
    if not rows or rows[0]["start"] != 0 or rows[-1]["end"] != expected:
        raise ValueError("timeline must cover the complete hackathon")
    for current, following in zip(rows, rows[1:]):
        if current["end"] != following["start"]:
            raise ValueError("timeline has a gap or overlap")
    team = set(content["team_aliases"])
    if any(row["owner"] not in team for row in rows):
        raise ValueError("timeline owner is not a team member alias")


def code_files(content: dict) -> dict[str, str]:
    """Generate the small, runnable starter app shown in the document."""
    prompt = content["ai"]["prompt"].replace("\"\"\"", "'''")
    schema = json.dumps(content["sample_schema"], indent=2)
    fixtures = json.dumps(content["data"]["fixtures"], indent=2, ensure_ascii=False)
    main = r'''from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
DB = ROOT / "bookings.sqlite3"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
app = FastAPI(title="Rural Tour Logistics Agent")

class BookingRequest(BaseModel):
    request_text: str = Field(min_length=8, max_length=500)

class BookingIntent(BaseModel):
    tour_id: str | None
    requested_date: str | None
    party_size: int | None

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS tours (tour_id TEXT PRIMARY KEY, name TEXT, location TEXT, date TEXT, capacity INTEGER, available INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS bookings (booking_id TEXT PRIMARY KEY, tour_id TEXT, requested_date TEXT, party_size INTEGER, status TEXT)")
        if conn.execute("SELECT COUNT(*) FROM tours").fetchone()[0] == 0:
            rows = json.loads((ROOT / "data" / "tours.json").read_text(encoding="utf-8"))
            conn.executemany("INSERT INTO tours VALUES (:tour_id,:name,:location,:date,:capacity,:available)", rows)

init_db()

@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Rural Tour Desk</title><style>body{font:18px system-ui;max-width:680px;margin:2rem auto;padding:0 1rem;background:#f4f7f5;color:#15251d}main{background:white;padding:1.5rem;border-radius:16px}textarea,input,button{font:inherit;width:100%;padding:.8rem;margin:.4rem 0;box-sizing:border-box}button{background:#116b4d;color:white;border:0;border-radius:8px}</style>
<main><h1>Rural Tour Desk</h1><p>Try a booking against the sample tour inventory.</p>
<form id='booking'><label>Request <textarea name='request_text'>Book TOUR-01 for 2 people on the sample date</textarea></label><button>Check and send sandbox SMS</button></form><pre id='result'>Ready.</pre></main>
<script>document.querySelector('#booking').onsubmit=async e=>{e.preventDefault();let r=await fetch('/api/bookings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_text:e.target.request_text.value})});document.querySelector('#result').textContent=JSON.stringify(await r.json(),null,2)}</script>"""

@app.get("/api/tours")
def tours():
    with db() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM tours ORDER BY tour_id")]

@app.post("/api/bookings")
def create_booking(payload: BookingRequest):
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(503, "Set GEMINI_API_KEY to run structured extraction.")
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = (ROOT / "prompts" / "booking.txt").read_text(encoding="utf-8")
    try:
        response = client.models.generate_content(
            model=MODEL, contents=f"{prompt}\n\nREQUEST: {payload.request_text}",
            config={"response_mime_type":"application/json", "response_schema":BookingIntent})
        intent = BookingIntent.model_validate_json(response.text)
    except Exception as exc:
        raise HTTPException(502, f"Could not extract the booking fields: {type(exc).__name__}") from exc
    if not intent.tour_id or not intent.requested_date or not intent.party_size or intent.party_size < 1:
        return {"status":"NEEDS_DETAILS", "intent":intent.model_dump(), "message":"Choose a tour, date, and party size."}
    with db() as conn:
        tour = conn.execute("SELECT * FROM tours WHERE tour_id=? AND date=?", (intent.tour_id,intent.requested_date)).fetchone()
        if not tour or intent.party_size > tour["available"]:
            return {"status":"NOT_AVAILABLE", "intent":intent.model_dump(), "message":"No matching capacity; choose another fixture."}
        booking_id = f"DEMO-{conn.execute('SELECT COUNT(*) FROM bookings').fetchone()[0]+1:03d}"
        conn.execute("INSERT INTO bookings VALUES (?,?,?,?,?)", (booking_id,intent.tour_id,intent.requested_date,intent.party_size,"CONFIRMED"))
        conn.execute("UPDATE tours SET available=available-? WHERE tour_id=?", (intent.party_size,intent.tour_id))
    if not os.getenv("AT_API_KEY") or not os.getenv("AT_TEST_PHONE"):
        return {"status":"SAVED_SMS_NOT_SENT", "booking_id":booking_id, "intent":intent.model_dump(), "message":"Booking saved. Configure the sandbox API key and simulator phone to send the test SMS."}
    try:
        import africastalking
        africastalking.initialize("sandbox", os.environ["AT_API_KEY"])
        sms = africastalking.SMS
        result = sms.send(f"Demo booking {booking_id}: {tour['name']} for {intent.party_size}.", [os.environ["AT_TEST_PHONE"]])
        return {"status":"CONFIRMED_IN_SANDBOX", "booking_id":booking_id, "intent":intent.model_dump(), "sms":result}
    except Exception as exc:
        return {"status":"BOOKED_SMS_FAILED", "booking_id":booking_id, "intent":intent.model_dump(), "message":type(exc).__name__}
'''
    main = main.replace("TOUR-01", str(content["data"]["fixtures"][0]["tour_id"]))
    return {
        "main.py": main,
        "prompts/booking.txt": prompt + "\n",
        "data/schema.json": schema + "\n",
        "data/tours.json": fixtures + "\n",
        "requirements.txt": "fastapi>=0.115\nuvicorn>=0.30\nafricastalking>=1.2\ngoogle-genai>=1.0\npython-dotenv>=1.0\n",
        ".env.example": "GEMINI_API_KEY=replace-with-your-Google-AI-Studio-key\nGEMINI_MODEL=gemini-3.5-flash-lite\nAT_API_KEY=replace-with-your-Africa-Talking-sandbox-key\nAT_TEST_PHONE=+2547XXXXXXXX\n",
    }


def write_html(runner, html_text: str) -> Path:
    path = runner.run_dir / "final_report.html"
    path.write_text(html_text, encoding="utf-8")
    return path

