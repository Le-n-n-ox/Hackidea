"""Stage 4: scoring — weighted rubric (reweighted to judging criteria), totals computed in Python."""
from __future__ import annotations

import json

from hackidea import prompts, schemas
from hackidea.config import DEFAULT_WEIGHTS

DIMENSIONS = list(DEFAULT_WEIGHTS)


def normalize_weights(weights: dict) -> dict:
    """Clamp to [0,1], ensure all dimensions present, renormalize to sum 1."""
    w = {d: max(0.0, float(weights.get(d, 0.0))) for d in DIMENSIONS}
    total = sum(w.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {d: round(v / total, 4) for d, v in w.items()}


def map_judging_weights(criteria: dict) -> tuple[dict, str]:
    """Map named judge criteria deterministically onto the six scoring dimensions."""
    mapped = {d: 0.0 for d in DIMENSIONS}
    explanations = []
    for label, raw_weight in criteria.items():
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        key = str(label).lower().replace("_", " ").replace("-", " ")
        if "impact" in key or "social" in key:
            shares = {"real_world_impact": 2/3, "theme_fit": 1/3}
        elif any(term in key for term in ("innovation", "novelty", "creativity")):
            shares = {"novelty": 0.5, "ai_depth": 0.5}
        elif any(term in key for term in ("technical", "execution", "engineering", "complexity")):
            shares = {"feasibility": 0.8, "ai_depth": 0.2}
        elif any(term in key for term in ("presentation", "demo", "pitch")):
            shares = {"demo_impact": 1.0}
        elif any(term in key for term in ("theme", "sponsor", "relevance", "track")):
            shares = {"theme_fit": 1.0}
        else:
            shares = dict(DEFAULT_WEIGHTS)
        for dimension, share in shares.items():
            mapped[dimension] += weight * share
        explanations.append(f"{label} ({weight:g}) -> " + ", ".join(
            f"{dimension} {share:.2f}" for dimension, share in shares.items()))
    if not explanations:
        return dict(DEFAULT_WEIGHTS), "Default rubric weights (criteria could not be mapped)."
    # Keep every dimension represented, then renormalize for rounded input criteria.
    mapped = {d: max(0.05, value) for d, value in mapped.items()}
    return normalize_weights(mapped), "Deterministic judge-criteria mapping: " + "; ".join(explanations)


def weighted_total(scores: dict, weights: dict) -> float:
    return round(sum(float(scores.get(d, 0)) * weights[d] for d in DIMENSIONS), 3)


def render_table(rows: list[dict], weights: dict) -> str:
    head = "| rank | id | name | " + " | ".join(DIMENSIONS) + " | weighted |"
    sep = "|" + "---|" * (len(DIMENSIONS) + 4)
    lines = [head, sep]
    for r in rows:
        cells = " | ".join(f"{r['scores'][d]:.1f}" for d in DIMENSIONS)
        lines.append(f"| {r['rank']} | {r['id']} | {r['name']} | {cells} | {r['weighted_total']:.2f} |")
    lines.append("")
    lines.append("Weights: " + json.dumps(weights))
    return "\n".join(lines)


def run(runner) -> dict:
    ctx = runner.ctx
    kept_ids = set(runner.load("filtering.json")["kept_ids"])
    cands = [c for c in runner.load("candidates.json")["candidates"] if c["id"] in kept_ids]
    sources = runner.load("research_log.json")["sources"]
    digest = prompts.build_digest([s for s in sources if s.get("fetched")], runner.preset["digest_sources"])

    # --- weights: map judging criteria deterministically when provided ---
    if ctx.get("judging_criteria"):
        weights, mapping_note = map_judging_weights(ctx["judging_criteria"])
        note = ("Reweighted toward stated judging criteria "
                f"{json.dumps(ctx['judging_criteria'])}: {mapping_note}")
    else:
        weights = dict(DEFAULT_WEIGHTS)
        note = "Default rubric weights (no judging criteria provided)."
    runner.save("weights.json", {"weights": weights, "note": note})
    runner.log(f"weights: {weights}")

    # --- score in batches; totals/ranks computed deterministically ---
    rows = []
    for i in range(0, len(cands), 4):
        batch = cands[i:i + 4]
        system, user = prompts.scoring(ctx, batch, weights, digest)
        data = runner.llm.complete_json(system, user, schemas.SCORES_SCHEMA,
                                        stage="scoring", max_tokens=4000)
        by_id = {c["id"]: c for c in batch}
        accepted = {s["id"]: s for s in data["scores"] if s.get("id") in by_id}
        for missing_id in set(by_id) - set(accepted):
            retry_system, retry_user = prompts.scoring(ctx, [by_id[missing_id]], weights, digest)
            retry = runner.llm.complete_json(retry_system, retry_user, schemas.SCORES_SCHEMA,
                                             stage=f"scoring_retry_{missing_id}", max_tokens=1600)
            matched = next((s for s in retry["scores"] if s.get("id") == missing_id), None)
            if matched:
                accepted[missing_id] = matched
        raw_values = [[float(s[d]) for d in DIMENSIONS] for s in accepted.values()]
        scale_fix = bool(raw_values) and max(max(row) for row in raw_values) <= 1.0 and max(max(row) for row in raw_values) > 0
        if scale_fix:
            runner.log(f"  score batch {i // 4 + 1}: corrected model's 0..1 scale to 0..10")
        for identity, s in accepted.items():
            c = by_id[identity]
            scores = {d: float(s[d]) for d in DIMENSIONS}
            if scale_fix:
                scores = {d: value * 10 for d, value in scores.items()}
            rows.append({"id": c["id"], "name": c["name"], "scores": scores,
                         "rationale": s.get("rationale", ""),
                         "weighted_total": weighted_total(scores, weights)})
    missing = kept_ids - {r["id"] for r in rows}
    if missing:
        runner.log(f"  WARNING: no scores returned for {sorted(missing)}")
    rows.sort(key=lambda r: r["weighted_total"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    table_md = render_table(rows, weights)
    result = {"weights": weights, "note": note, "rows": rows, "table_md": table_md,
              "scale_correction": "entire batch multiplied by 10 when model returned 0..1 values"}
    runner.save("scoring_table.json", result)
    (runner.run_dir / "scoring_table.md").write_text(table_md, encoding="utf-8")
    runner.log("scoring top5: " + ", ".join(f"{r['name']}={r['weighted_total']}" for r in rows[:5]))
    return result
