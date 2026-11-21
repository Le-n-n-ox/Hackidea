"""Stage 0: parse free-text context into structured fields; <=3 clarifying questions."""
from __future__ import annotations

import json
import re

from hackidea import schemas

SYSTEM = ("You extract structured hackathon context from free text. Be literal: only "
          "record what the text states. Unknown fields get empty values, never guesses.")


def _normalize(data: dict) -> dict:
    dh = data.get("duration_hours")
    if isinstance(dh, str):
        m = re.search(r"(\d+)", dh)
        dh = int(m.group(1)) if m else None
    data["duration_hours"] = dh
    jc = data.get("judging_criteria") or {}
    clean = {}
    for k, v in jc.items():
        try:
            clean[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    total = sum(clean.values())
    if total > 1.5:  # percentages like 30/30/25/15 -> fractions
        clean = {k: v / total for k, v in clean.items()}
    data["judging_criteria"] = clean
    for key in ("tracks", "sponsors", "sponsor_apis", "preferred_tech", "banned_tech"):
        data[key] = [str(x) for x in (data.get(key) or [])]
    if not data["sponsors"] and data["sponsor_apis"]:
        data["sponsors"] = list(data["sponsor_apis"])
    team = []
    for m in data.get("team") or []:
        if isinstance(m, dict) and m.get("role"):
            team.append({"role": str(m["role"]), "skills": [str(s) for s in (m.get("skills") or [])]})
        elif isinstance(m, str):
            team.append({"role": m, "skills": []})
    data["team"] = team
    for key in ("hackathon_name", "budget_constraints", "audience_region", "extra_notes"):
        data[key] = str(data.get(key) or "")
    data["theme"] = str(data.get("theme") or "").strip()
    return data


def _missing_questions(ctx: dict) -> list[str]:
    qs = []
    if not ctx.get("theme"):
        qs.append("What is the hackathon theme or goal? (required)")
    if not ctx.get("duration_hours"):
        qs.append("How long is the hackathon (e.g. 24h, 48h, 1 week)?")
    if not ctx.get("team"):
        qs.append("Team size and each member's main skills?")
    return qs[:3]


def run(runner, raw_text: str) -> dict:
    user = ("Extract the hackathon context below into JSON with keys: theme, hackathon_name, "
            "tracks[], sponsors[], sponsor_apis[], judging_criteria{name:weight}, duration_hours, "
            "team[{role, skills[]}], preferred_tech[], banned_tech[], budget_constraints, "
            "audience_region, extra_notes.\n\nCONTEXT:\n" + raw_text)
    data = _normalize(runner.llm.complete_json(SYSTEM, user, schemas.CONTEXT_SCHEMA,
                                               stage="context", max_tokens=2500))

    questions: list[str] = []
    answers: list[str] = []
    missing = _missing_questions(data)
    if missing and runner.interactive:
        for q in missing:
            try:
                ans = runner.ask(q + " ").strip()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            questions.append(q)
            answers.append(ans)
        if any(answers):
            raw_text += "\n\nClarifications:\n" + "\n".join(
                f"Q: {q}\nA: {a}" for q, a in zip(questions, answers) if a)
            data = _normalize(runner.llm.complete_json(SYSTEM, user + "\n\nCONTEXT:\n" + raw_text,
                                                       schemas.CONTEXT_SCHEMA, stage="context"))

    assumptions = []
    if not data.get("theme"):
        raise SystemExit("Cannot proceed: no theme/goal in context (run with --interactive to be asked).")
    if not data.get("duration_hours"):
        data["duration_hours"] = 48
        assumptions.append("Duration not given -> assumed 48 hours.")
    if not data.get("team"):
        data["team"] = [{"role": "generalist", "skills": ["web development", "prompting"]}] * 3
        assumptions.append("Team not given -> assumed 3 generalist developers.")
    if not data.get("budget_constraints"):
        data["budget_constraints"] = "free tiers only"
        assumptions.append("Budget not given -> assumed free tiers only.")
    if not data.get("audience_region"):
        assumptions.append("No target region given -> global audience, local relevance only if theme allows.")

    ctx = {**data, "raw_context": raw_text, "assumptions": assumptions,
           "questions_asked": questions}
    runner.save("context.json", ctx)
    runner.ctx = ctx
    runner.log(f"context parsed: theme={ctx['theme']!r}, {ctx['duration_hours']}h, "
               f"team={len(ctx['team'])}, judging={list(ctx['judging_criteria']) or 'default rubric'}")
    for a in assumptions:
        runner.log(f"  assumption: {a}")
    return ctx