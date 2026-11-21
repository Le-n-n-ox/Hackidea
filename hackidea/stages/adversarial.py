"""Stage 5: adversarial testing — red-team top 5, pairwise tournament, stability re-run of top 2."""
from __future__ import annotations

import itertools
import json

from hackidea import prompts, schemas
from hackidea.stages.scoring import weighted_total

INVESTIGATE_SCHEMA = {
    "type": "object", "required": ["winner_id", "explanation"],
    "properties": {"winner_id": {"type": "string"}, "explanation": {"type": "string"}},
}


def tournament_wins(matches: list[dict], ids: list[str]) -> dict:
    wins = {i: 0 for i in ids}
    for m in matches:
        if m.get("winner") in wins and m["winner"] in (m.get("a"), m.get("b")):
            wins[m["winner"]] += 1
    return wins


def run(runner) -> dict:
    ctx = runner.ctx
    scoring = runner.load("scoring_table.json")
    weights = scoring["weights"]
    n_finalists = runner.preset["finalists"]
    top_rows = scoring["rows"][:n_finalists]
    by_id = {c["id"]: c for c in runner.load("candidates.json")["candidates"]}
    finalists = [by_id[r["id"]] for r in top_rows if r["id"] in by_id]
    score_by_id = {r["id"]: r for r in top_rows}

    # --- red team (batches of 2) ---
    reviews = []
    for i in range(0, len(finalists), 2):
        batch = finalists[i:i + 2]
        system, user = prompts.redteam(ctx, batch)
        data = runner.llm.complete_json(system, user, schemas.REDTEAM_SCHEMA,
                                        stage="redteam", max_tokens=4500)
        reviews.extend(data["reviews"])
    runner.save("redteam.json", {"reviews": reviews})
    runner.log(f"red-team: {len(reviews)} finalists reviewed")

    # --- round-robin tournament (batches of 2 pairs) ---
    pairs = list(itertools.combinations(finalists, 2))
    matches = []
    for i in range(0, len(pairs), 2):
        batch = pairs[i:i + 2]
        system, user = prompts.tournament(ctx, batch)
        data = runner.llm.complete_json(system, user, schemas.TOURNAMENT_SCHEMA,
                                        stage="tournament", max_tokens=3500)
        for m in data["matches"]:
            if m.get("winner") in (m.get("a"), m.get("b")):
                matches.append(m)
    wins = tournament_wins(matches, [c["id"] for c in finalists])
    runner.save("tournament.json", {"matches": matches, "wins": wins})
    runner.log(f"tournament: {len(matches)} matches, wins={wins}")

    # --- combined ranking: tournament wins first, weighted score as tiebreak ---
    order = sorted(finalists, key=lambda c: (wins.get(c["id"], 0),
                                             score_by_id[c["id"]]["weighted_total"]), reverse=True)
    original_order = [c["id"] for c in order]

    # --- stability: re-score top 2 with reworded prompt + different temperature/seed ---
    top2 = order[:2]
    flipped = False
    resolution = "Ranking stable across both scoring passes."
    rescored_totals = {}
    if len(top2) == 2:
        system, user = prompts.scoring(ctx, top2, weights,
                                       prompts.build_digest(runner.load("research_log.json")["sources"], 20),
                                       reworded=True)
        try:
            data = runner.llm.complete_json(system, user, schemas.SCORES_SCHEMA,
                                            stage="scoring_stability", max_tokens=2500,
                                            temperature=0.9, seed=1337)
            for s in data["scores"]:
                if s["id"] in {c["id"] for c in top2}:
                    rescored_totals[s["id"]] = weighted_total({d: float(s[d]) for d in
                                                               ("theme_fit", "ai_depth", "feasibility",
                                                                "novelty", "demo_impact", "real_world_impact")},
                                                              weights)
            if len(rescored_totals) == 2:
                new_order = sorted(rescored_totals, key=rescored_totals.get, reverse=True)
                if new_order[0] != original_order[0]:
                    flipped = True
                    detail = (f"Pass 1 order: {original_order[:2]} (totals "
                              f"{ {i: score_by_id[i]['weighted_total'] for i in original_order[:2]} }); "
                              f"pass 2 totals: {rescored_totals}")
                    system, user = prompts.investigate(ctx, top2[0], top2[1], detail)
                    inv = runner.llm.complete_json(system, user, INVESTIGATE_SCHEMA,
                                                   stage="investigate", max_tokens=3000)
                    if inv["winner_id"] in {c["id"] for c in top2}:
                        loser = next(i for i in original_order[:2] if i != inv["winner_id"])
                        order = [by_id[inv["winner_id"]], by_id[loser]] + order[2:]
                        resolution = f"Ranking flipped on re-score; investigation resolved it: {inv['explanation']}"
                    else:
                        resolution = "Flip detected but investigation returned invalid id; kept pass-1 order."
        except Exception as e:  # noqa: BLE001
            resolution = f"Stability re-run failed ({e}); kept pass-1 order."
            runner.log(f"  stability re-run failed: {e}")

    final_order = [c["id"] for c in order]
    result = {"original_order": original_order, "rescored_totals": rescored_totals,
              "flipped": flipped, "resolution": resolution, "final_order": final_order,
              "tournament_wins": wins}
    runner.save("stability.json", result)
    runner.log(f"adversarial final order: {final_order} (flipped={flipped})")
    return result