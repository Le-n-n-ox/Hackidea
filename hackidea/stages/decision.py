"""Stage 6: code-bound winner selection and evidence-limited explanation."""
from __future__ import annotations

import json

from hackidea import prompts, schemas
from hackidea.search import normalize_url


def run(runner) -> dict:
    ctx = runner.ctx
    scoring = runner.load("scoring_table.json")
    stability = runner.load("stability.json")
    tournament = runner.load("tournament.json")
    redteam = {r["id"]: r for r in runner.load("redteam.json")["reviews"]}
    by_id = {c["id"]: c for c in runner.load("candidates.json")["candidates"]}
    score_by_id = {r["id"]: r for r in scoring["rows"]}
    sources = runner.load("research_log.json")["sources"]
    fetched_urls = {normalize_url(s["url"]) for s in sources if s.get("fetched")}

    # Winner is deterministic from adversarial.json; the LLM explains it but cannot overrule it.
    winner_id = stability["final_order"][0]
    winner = by_id[winner_id]
    finalists = [by_id[i] for i in stability["final_order"] if i in by_id]
    detail_lines = []
    for c in finalists:
        row, review = score_by_id[c["id"]], redteam.get(c["id"], {})
        detail_lines.append(prompts.candidate_block(c))
        detail_lines.append(f"score={row['weighted_total']}; pair wins="
                            f"{tournament['wins'].get(c['id'], 0)}; selection="
                            f"{stability['selection_metrics'][c['id']]}")
        detail_lines.append("red-team=" + review.get("strongest_objection", "no review"))
        detail_lines.append("risks=" + json.dumps(review.get("risks", []), ensure_ascii=False))
    detail = "\n".join(detail_lines)
    system, user = prompts.decision(ctx, winner, detail, sources)
    data = runner.llm.complete_json(system, user, schemas.DECISION_SCHEMA,
                                    stage="decision", max_tokens=2400)
    # Enforce the result rather than trusting a model that might choose another id.
    if data.get("winner_id") != winner_id:
        runner.log(f"  decision explanation proposed {data.get('winner_id')}; enforced rule winner {winner_id}")
    row = score_by_id[winner_id]
    review = redteam.get(winner_id, {})
    metrics = stability["selection_metrics"][winner_id]
    top_dimensions = sorted(row["scores"].items(), key=lambda item: item[1], reverse=True)[:2]
    evidence_url = normalize_url(winner.get("evidence_url", ""))
    why = [
        {"point": f"The recorded selection rule ranks this idea first: {metrics['pair_wins']} pair wins, "
                  f"risk penalty {metrics['risk_penalty']}, selection score {metrics['selection_score']:.4f}."},
        {"point": f"It scored {row['weighted_total']:.2f} under the stated judging weights; strongest rubric "
                  f"dimensions were {top_dimensions[0][0]} ({top_dimensions[0][1]:g}) and "
                  f"{top_dimensions[1][0]} ({top_dimensions[1][1]:g})."},
        {"point": f"It addresses {winner.get('problem','the stated user problem')} for "
                  f"{winner.get('target_user','the target user')}."},
        {"point": "Red-team objection retained in the plan: " +
                  review.get("strongest_objection", "no material objection was returned") + "."},
    ]
    if evidence_url in fetched_urls and winner.get("evidence_url"):
        why[2]["evidence_url"] = evidence_url
    data.update(winner_id=winner_id, why_beats_others=why,
                rationale="Selection is bound to the recorded tournament and risk-adjusted ranking.",
                selection_rule=stability["ranking_rule"],
                confidence_level=data.get("confidence_level", "medium"),
                confidence_pct=data.get("confidence_pct", 60),
                key_assumption=data.get("key_assumption", "The required data access is available during the hackathon."))
    runner.save("decision.json", data)
    runner.log(f"DECISION: {winner['name']} (confidence {data['confidence_level']}, "
               f"{data['confidence_pct']}%; enforced selection rule)")
    return data
