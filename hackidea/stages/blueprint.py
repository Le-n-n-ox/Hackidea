"""Stage 7: build blueprint — four focused LLM passes, saved as markdown parts."""
from __future__ import annotations

import json

from hackidea import prompts

PARTS = [("a", prompts.blueprint_a), ("b", prompts.blueprint_b),
         ("c", prompts.blueprint_c), ("d", prompts.blueprint_d)]


def run(runner) -> dict:
    ctx = runner.ctx
    decision = runner.load("decision.json")
    by_id = {c["id"]: c for c in runner.load("candidates.json")["candidates"]}
    winner = by_id[decision["winner_id"]]
    scoring = runner.load("scoring_table.json")
    redteam = {r["id"]: r for r in runner.load("redteam.json")["reviews"]}
    stability = runner.load("stability.json")
    sources = runner.load("research_log.json")["sources"]
    digest = prompts.build_digest(sources, runner.preset["digest_sources"])

    r = next((x for x in scoring["rows"] if x["id"] == winner["id"]), {})
    rt = redteam.get(winner["id"], {})
    detail = (f"Weighted score {r.get('weighted_total')} (rank {r.get('rank')}). "
              f"Scores: {json.dumps(r.get('scores', {}))}. "
              f"Confidence: {decision['confidence_level']} ({decision['confidence_pct']}%). "
              f"Key assumption: {decision['key_assumption']}. "
              f"Strongest objection: {rt.get('strongest_objection', 'n/a')}. "
              f"Mitigations: {json.dumps([x.get('mitigation') for x in rt.get('risks', [])][:5])}. "
              f"Stability: {stability['resolution']}")

    parts = {}
    for key, fn in PARTS:
        system, user = fn(ctx, winner, detail, digest)
        user += f"\n[STAGE:blueprint_{key}]"
        text = runner.llm.chat([{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                               max_tokens=9000, temperature=0.5)
        if len(text.strip()) < 500:
            retry = user + "\nYour previous answer was too short. Write the FULL sections with all required detail."
            text = runner.llm.chat([{"role": "system", "content": system},
                                    {"role": "user", "content": retry}],
                                   max_tokens=9000, temperature=0.5)
        parts[key] = text
        (runner.run_dir / f"blueprint_{key}.md").write_text(text, encoding="utf-8")
        runner.log(f"blueprint part {key}: {len(text)} chars")
    runner.save("blueprint.json", {"winner_id": winner["id"], "parts": list(parts)})
    return {"winner_id": winner["id"], "parts": parts}