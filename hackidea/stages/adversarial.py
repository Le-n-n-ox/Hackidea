"""Stage 5: evidence-aware red team, complete pairwise tournament, and scale-safe stability."""
from __future__ import annotations

import itertools
import json

from hackidea import prompts, schemas
from hackidea.stages.ideation import idea_similarity, SIMILARITY_THRESHOLD
from hackidea.stages.scoring import weighted_total


def tournament_wins(matches: list[dict], ids: list[str]) -> dict:
    wins = {i: 0 for i in ids}
    for match in matches:
        if match.get("winner") in wins and match["winner"] in (match.get("a"), match.get("b")):
            wins[match["winner"]] += 1
    return wins


def _high_risk_count(review: dict) -> tuple[int, int]:
    risks = review.get("risks", [])
    high = sum(r.get("severity") == "high" for r in risks)
    high_existing = sum(r.get("severity") == "high" and r.get("category") == "already_exists"
                        for r in risks)
    return high, high_existing


def _scaled_scores(data: dict, expected_ids: set[str], weights: dict) -> tuple[dict, bool]:
    rows = {r.get("id"): r for r in data.get("scores", []) if r.get("id") in expected_ids}
    if set(rows) != expected_ids:
        raise ValueError("stability response omitted or misidentified a finalist")
    values = [[float(rows[i][d]) for d in weights] for i in expected_ids]
    scale_fix = max(max(row) for row in values) <= 1.0 and max(max(row) for row in values) > 0
    totals = {}
    for identity, row in rows.items():
        scores = {d: float(row[d]) * (10 if scale_fix else 1) for d in weights}
        totals[identity] = weighted_total(scores, weights)
    return totals, scale_fix


def _distinct_finalists(rows, candidates, count):
    by_id = {c["id"]: c for c in candidates}
    chosen, ids = [], set()
    for row in rows:
        c = by_id.get(row["id"])
        if not c or any(idea_similarity(c, old) >= SIMILARITY_THRESHOLD for old in chosen):
            continue
        chosen.append(c)
        ids.add(c["id"])
        if len(chosen) >= count:
            break
    return chosen


def _fallback_review(identity: str) -> dict:
    """Keep review coverage intact when the model returns invalid JSON/schema."""
    categories = ("technical", "api_rate_limit", "data", "demo_failure", "already_exists")
    return {
        "id": identity,
        "risks": [{"category": category,
                   "risk": "No valid model assessment was returned; verify this risk category manually.",
                   "severity": "medium",
                   "mitigation": "Use a simulated demo path and check the relevant current documentation."}
                  for category in categories],
        "strongest_objection": "Red-team response was invalid; manually review technical feasibility, data access, and existing products.",
    }


def run(runner) -> dict:
    ctx = runner.ctx
    scoring = runner.load("scoring_table.json")
    weights = scoring["weights"]
    candidates = runner.load("candidates.json")["candidates"]
    finalists = _distinct_finalists(scoring["rows"], candidates, runner.preset["finalists"])
    if len(finalists) < 2:
        raise RuntimeError("adversarial stage needs at least two distinct finalists")
    finalist_ids = [c["id"] for c in finalists]
    score_by_id = {r["id"]: r for r in scoring["rows"]}
    source_rows = runner.load("research_log.json")["sources"]
    fetched_urls = {s["url"] for s in source_rows if s.get("fetched")}
    evidence = prompts.build_digest([s for s in source_rows if s.get("fetched")], 24)

    # One small red-team request per finalist, with fetched research included.
    reviews = []
    for c in finalists:
        system, user = prompts.redteam(ctx, [c], evidence)
        stage = f"redteam_{c['id']}"
        try:
            result = runner.llm.complete_json(system, user, schemas.REDTEAM_SCHEMA,
                                              stage=stage, max_tokens=2600)
            review = next((r for r in result["reviews"] if r.get("id") == c["id"]), None)
        except Exception as exc:
            runner.log(f"  red-team response invalid for {c['id']}: {type(exc).__name__}; retrying")
            review = None
        if review is None:
            system, user = prompts.redteam(ctx, [c], evidence)
            user += f"\nReturn exactly one review for id {c['id']}."
            try:
                result = runner.llm.complete_json(system, user, schemas.REDTEAM_SCHEMA,
                                                  stage=f"redteam_retry_{c['id']}", max_tokens=2600)
                review = next((r for r in result["reviews"] if r.get("id") == c["id"]), None)
            except Exception as exc:
                runner.log(f"  red-team retry invalid for {c['id']}: {type(exc).__name__}")
        if review is None:
            review = _fallback_review(c["id"])
        for risk in review.get("risks", []):
            url = risk.get("evidence_url")
            if url not in fetched_urls:
                risk.pop("evidence_url", None)
            # A matching fetched URL is a research lead, not automatic proof that
            # the page supports the model's specific claim.
            risk["verification"] = "unverified; check the exact claim against the linked source"
        reviews.append(review)
    runner.save("redteam.json", {"reviews": reviews})
    runner.log(f"red-team: {len(reviews)}/{len(finalists)} finalists reviewed")

    # Every unordered pair is judged in both positions. Python binds the response
    # to the submitted pair; model-returned a/b labels are diagnostic only.
    matches, pair_results = [], []
    for pair_index, (first, second) in enumerate(itertools.combinations(finalists, 2), 1):
        votes = []
        for position, (a, b) in enumerate(((first, second), (second, first)), 1):
            system, user = prompts.tournament(ctx, [(a, b)])
            try:
                result = runner.llm.complete_json(system, user, schemas.TOURNAMENT_SCHEMA,
                                                  stage=f"tournament_{pair_index:02d}_{position}", max_tokens=1600)
                emitted = result["matches"][0]
                winner = emitted.get("winner")
            except Exception as exc:
                runner.log(f"  tournament response invalid for pair {pair_index}/{position}: {type(exc).__name__}")
                emitted, winner = {}, None
            if winner not in (first["id"], second["id"]):
                # Targeted correction before any deterministic fallback.
                user += (f"\nThe only valid winner ids are {first['id']} and {second['id']}. "
                         "Return one corrected match for the pair exactly as shown.")
                try:
                    result = runner.llm.complete_json(system, user, schemas.TOURNAMENT_SCHEMA,
                                                      stage=f"tournament_retry_{pair_index:02d}_{position}", max_tokens=1600)
                    emitted = result["matches"][0]
                    winner = emitted.get("winner")
                except Exception as exc:
                    runner.log(f"  tournament retry invalid for pair {pair_index}/{position}: {type(exc).__name__}")
                    emitted, winner = {}, None
            if winner not in (first["id"], second["id"]):
                winner = max((first["id"], second["id"]),
                             key=lambda i: score_by_id[i]["weighted_total"])
                reasoning = "Model returned an invalid id; resolved by the saved weighted score."
            else:
                reasoning = emitted.get("reasoning", "")
            votes.append(winner)
            matches.append({"pair_id": pair_index, "position": position,
                            "a": a["id"], "b": b["id"], "winner": winner,
                            "reasoning": reasoning,
                            "model_ids": {"a": emitted.get("a"), "b": emitted.get("b")}})

        if votes[0] == votes[1]:
            pair_winner, resolution = votes[0], "both orderings agreed"
        else:
            reviews_by_id = {r["id"]: r for r in reviews}
            def adjusted(identity):
                high, existing = _high_risk_count(reviews_by_id[identity])
                return score_by_id[identity]["weighted_total"] - 0.5 * high - 0.75 * existing
            pair_winner = max((first["id"], second["id"]),
                              key=lambda i: (adjusted(i), score_by_id[i]["weighted_total"], i))
            resolution = "split vote; adjusted score and red-team severity broke the tie"
        pair_results.append({"pair_id": pair_index, "a": first["id"], "b": second["id"],
                             "votes": votes, "winner": pair_winner, "resolution": resolution})

    wins = {i: 0 for i in finalist_ids}
    for pair in pair_results:
        wins[pair["winner"]] += 1
    runner.save("tournament.json", {"matches": matches, "pair_results": pair_results,
                                    "expected_pairs": len(pair_results),
                                    "expected_ordered_matches": len(matches), "wins": wins})
    runner.log(f"tournament: {len(matches)} ordered judgments for {len(pair_results)} pairs; wins={wins}")

    reviews_by_id = {r["id"]: r for r in reviews}
    metrics = {}
    for c in finalists:
        high, existing = _high_risk_count(reviews_by_id[c["id"]])
        weighted = score_by_id[c["id"]]["weighted_total"]
        penalty = 0.5 * high + 0.75 * existing
        metrics[c["id"]] = {"pair_wins": wins[c["id"]], "high_risks": high,
                            "high_existing_risks": existing, "risk_penalty": penalty,
                            "selection_score": round(wins[c["id"]] + weighted / 100 - penalty, 4)}
    order = sorted(finalists, key=lambda c: (metrics[c["id"]]["selection_score"],
                                             wins[c["id"]], score_by_id[c["id"]]["weighted_total"],
                                             c["id"]), reverse=True)
    original_order = [c["id"] for c in order]

    # Re-score two candidates. A whole 0..1 response is corrected to the 0..10 scale.
    flipped, scale_fix, rescored_totals = False, False, {}
    resolution = "No stability comparison was available."
    if len(order) >= 2:
        top2 = order[:2]
        system, user = prompts.scoring(ctx, top2, weights,
                                       prompts.build_digest(source_rows, 20), reworded=True)
        try:
            data = runner.llm.complete_json(system, user, schemas.SCORES_SCHEMA,
                                            stage="scoring_stability", max_tokens=2500,
                                            temperature=0.9, seed=1337)
            rescored_totals, scale_fix = _scaled_scores(data, {c["id"] for c in top2}, weights)
            if scale_fix:
                runner.log("  stability scores were 0..1; rescaled to 0..10")
            new_leader = max(rescored_totals, key=rescored_totals.get)
            flipped = new_leader != original_order[0]
            resolution = ("Stability re-score changed the top two; selection rule retained tournament/risk leader."
                          if flipped else "Top-two order stable on a second 0..10 scoring pass.")
        except Exception as exc:
            resolution = f"Stability pass invalid or incomplete ({type(exc).__name__}); tournament/risk order retained."
            runner.log(f"  stability re-run failed: {type(exc).__name__}")

    final_order = [c["id"] for c in order]
    result = {"ranking_rule": "pair wins, then pair-split risk-adjusted score; overall selection_score = pair_wins + weighted_total/100 - 0.5 per high risk - 0.75 per high already_exists risk; then weighted score and id",
              "original_order": original_order, "rescored_totals": rescored_totals,
              "stability_scale_correction_0_to_10": scale_fix, "flipped": flipped,
              "resolution": resolution, "selection_metrics": metrics,
              "final_order": final_order, "tournament_wins": wins}
    runner.save("stability.json", result)
    runner.log(f"adversarial final order: {final_order} (stability_flip={flipped})")
    return result
