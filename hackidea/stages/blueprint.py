"""Stage 7: research, structure, validate, and render the build blueprint."""
from __future__ import annotations

import json

from hackidea import prompts, schemas
from hackidea.blueprint_document import (BLUEPRINT_SECTIONS, ARCHITECTURE, build_config,
                                         enforce_document, event_date_from_context,
                                         section_defaults, team_aliases, validate_timeline)
from hackidea.blueprint_research import run_blueprint_research
from hackidea.concurrency import ordered_fanout
from hackidea.report import build_final, write_outputs


def run(runner) -> dict:
    ctx = runner.ctx
    decision = runner.load("decision.json")
    by_id = {c["id"]: c for c in runner.load("candidates.json")["candidates"]}
    winner = by_id[decision["winner_id"]]
    scoring = runner.load("scoring_table.json")
    ordered_ids = [identity for identity in runner.load("stability.json")["final_order"] if identity in by_id]
    runner_up = by_id[ordered_ids[1]] if len(ordered_ids) > 1 else None

    # The selected idea gets a fresh, narrowly scoped documentation search.
    docs = run_blueprint_research(runner)
    project_stack = build_config(ctx, docs)
    runner.save("blueprint_recommendations.json", project_stack)
    team = team_aliases(ctx)
    date_value = event_date_from_context(ctx)
    section_names = list(BLUEPRINT_SECTIONS)

    def generate(name: str) -> tuple[str, dict]:
        system, user = prompts.blueprint_section(ctx, winner, name, project_stack,
                                                docs["sources"], runner_up)
        try:
            data = runner.llm.complete_json(system, user, BLUEPRINT_SECTIONS[name],
                stage=f"blueprint_{name}", max_tokens=2600 if name in ("backend", "demo") else 1900,
                temperature=0.35, tries=2)
            schemas.validate(data, BLUEPRINT_SECTIONS[name])
            return name, data
        except Exception as exc:
            runner.log(f"  structured blueprint section {name} failed ({type(exc).__name__}); using safe template")
            data = section_defaults(name, ctx, winner, team, date_value)
            schemas.validate(data, BLUEPRINT_SECTIONS[name])
            return name, data

    outcomes = ordered_fanout(
        section_names, generate, workers=min(runner.cfg.llm_concurrency, len(section_names)),
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("blueprint content", done, total),
    )
    if runner.cancel_requested:
        raise InterruptedError("blueprint content generation cancelled")
    content = {}
    for name, outcome in zip(section_names, outcomes):
        if outcome.value is None:
            team_aliases_value = team_aliases(ctx)
            data = section_defaults(name, ctx, winner, team_aliases_value, date_value)
        else:
            _, data = outcome.value
        content[name] = data

    content = enforce_document(content, ctx, winner, runner_up, decision)
    content["architecture"] = ARCHITECTURE
    validate_timeline(content)
    # Validate the post-processed sections as well as the model response. The
    # extra fields below are generated in code and have their own invariants.
    for name, schema in BLUEPRINT_SECTIONS.items():
        schemas.validate(content[name], schema)
    runner.save("blueprint_content.json", content)

    # Keep the historical blueprint artifact and part filenames for resume and
    # alternative-idea workflows; the Markdown files are rendered from JSON.
    parts = {
        "a": ["summary", "frontend", "backend"],
        "b": ["ai", "data"],
        "c": ["timeline"],
        "d": ["demo", "risks", "appendix"],
    }
    for key, section_ids in parts.items():
        (runner.run_dir / f"blueprint_{key}.md").write_text(
            "\n\n".join(json.dumps({section: content[section]}, ensure_ascii=False, indent=2)
                          for section in section_ids), encoding="utf-8")
    config = {**project_stack, "event_date": date_value,
              "architecture_node_count": len(ARCHITECTURE["nodes"]),
              "architecture_edge_count": len(ARCHITECTURE["edges"])}
    runner.save("blueprint.json", {"winner_id": winner["id"], "build_config": config,
                                   "parts": list(parts)})
    markdown = build_final(runner)
    artifacts = write_outputs(runner, markdown)
    runner.log(f"blueprint rendered: {artifacts['pages']} PDF pages, {len(docs['sources'])} doc citations, "
               f"{sum(len(json.dumps(x, ensure_ascii=False).split()) for x in content.values())} structured fields")
    return {"winner_id": winner["id"], "build_config": config, "parts": list(parts),
            "artifacts": artifacts}
