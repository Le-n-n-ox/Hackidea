"""Stage 6: decision — exactly ONE winner, honest confidence, evidence-bound citations."""
from __future__ import annotations

import json

from hackidea import prompts, schemas
from hackidea.search import normalize_url


def run(runner) -> dict:
    ctx = runner.ctx
    scoring = runner.load("scoring_table.json")
    stability = runner.load("stability.json")
    redteam = {r["id"]: r for r in runner.load("redteam.json")["reviews"]}
    tournament = runner.load("tournament.json")
    by_id = {c["id"]: c for c in runner.load("candidates.json")["candidates"]}
    score_by_id = {r["id"]: r for r in scoring["rows"]}
    sources = runner.load("research_log.json")["sources"]
    fetched_urls = {normalize_url(s["url"]) for s in sources if s.get("fetched")}

    finalists = [by_id[i] for i in stability["final_order"] if i in by_id]
    detail_lines = []
    for c in finalists:
        r = score_by_id.get(c["id"], {})
        rt = redteam.get(c["id"], {})
        detail_lines.append(prompts.candidate_block(c))
        detail_lines.append(f"  weighted score: {r.get('weighted_total')} (rank {r.get('rank')}) "
                            f"| tournament wins: {tournament['wins'].get(c['id'], 0)}")
        detail_lines.append(f"  scores: {json.dumps(r.get('scores', {}))}")
        detail_lines.append(f"  strongest objection: {rt.get('strongest_objection', 'n/a')}")
        hi = [x for x in rt.get("risks", []) if x.get("severity") == "high"]
        if hi:
            detail_lines.append("  high risks: " + "; ".join(x["risk"] for x in hi[:3]))
    detail_lines.append(f"Stability: {stability['resolution']}")
    detail = "\n".join(detail_lines)

    system, user = prompts.decision(ctx, finalists, detail, sources)
    data = runner.llm.complete_json(system, user, schemas.DECISION_SCHEMA,
                                    stage="decision", max_tokens=3000)
    if data["winner_id"] not in {c["id"] for c in finalists}:
        raise RuntimeError(f"decision returned unknown winner_id {data['winner_id']!r}")

    # Never cite unfetched/invalid URLs: strip them, keep the point.
    for bullet in data["why_beats_others"]:
        url = bullet.get("evidence_url", "")
        if url and normalize_url(url) not in fetched_urls:
            bullet.pop("evidence_url", None)
            bullet["point"] += " (score/red-team evidence)"

    runner.save("decision.json", data)
    runner.log(f"DECISION: {by_id[data['winner_id']]['name']} "
               f"(confidence {data['confidence_level']}, {data['confidence_pct']}%)")
    return data