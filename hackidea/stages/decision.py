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
    ordered = [score_by_id[i] for i in stability["final_order"] if i in score_by_id]
    runner_up = ordered[1] if len(ordered) > 1 else None
    margin = (metrics["selection_score"] - stability["selection_metrics"][runner_up["id"]]["selection_score"]
              if runner_up else 0.0)
    winner_pairs = [p for p in tournament.get("pair_results", [])
                    if winner_id in (p.get("a"), p.get("b"))]
    split_votes = sum(p.get("votes", [None, None])[0] != p.get("votes", [None, None])[-1]
                      for p in winner_pairs)
    blocking = any(r.get("severity") == "high" and
                   r.get("category") in ("technical", "data", "demo_failure", "api_rate_limit") and
                   any(word in r.get("risk", "").lower() for word in
                       ("cannot", "unavailable", "no access", "not obtainable", "blocking", "will fail"))
                   for r in review.get("risks", []))
    high_risks = sum(r.get("severity") == "high" for r in review.get("risks", []))
    confidence_pct = max(20, min(90, round(55 + max(-15, min(20, margin * 5))
                                      - split_votes * 8 - high_risks * 4
                                      - (12 if stability.get("flipped") else 0))))
    if blocking:
        confidence_level = "low"
        confidence_pct = min(confidence_pct, 45)
    elif confidence_pct >= 72:
        confidence_level = "high"
    elif confidence_pct >= 48:
        confidence_level = "medium"
    else:
        confidence_level = "low"
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
                confidence_level=confidence_level,
                confidence_pct=confidence_pct,
                confidence_inputs={"selection_margin": round(margin, 4), "split_votes": split_votes,
                                   "stability_flipped": bool(stability.get("flipped")),
                                   "winner_high_risks": high_risks, "blocking_risk": blocking},
                key_assumption=data.get("key_assumption", "The required data access is available during the hackathon."))
    runner.save("decision.json", data)
    runner.log(f"DECISION: {winner['name']} (confidence {data['confidence_level']}, "
               f"{data['confidence_pct']}%; enforced selection rule)")
    return data
