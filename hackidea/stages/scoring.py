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
    digest = prompts.build_digest(sources, runner.preset["digest_sources"])

    # --- weights: default rubric, reweighted toward judging criteria when provided ---
    if ctx.get("judging_criteria"):
        system, user = prompts.weights(ctx)
        wdata = runner.llm.complete_json(system, user, schemas.WEIGHTS_SCHEMA,
                                         stage="weights", max_tokens=1200)
        weights = normalize_weights(wdata["weights"])
        note = ("Reweighted toward stated judging criteria "
                f"{json.dumps(ctx['judging_criteria'])}: {wdata['rationale']}")
    else:
        weights = dict(DEFAULT_WEIGHTS)
        note = "Default rubric weights (no judging criteria provided)."
    runner.save("weights.json", {"weights": weights, "note": note})
    runner.log(f"weights: {weights}")

    # --- score in batches; totals/ranks computed deterministically ---
    rows = []
    for i in range(0, len(cands), 8):
        batch = cands[i:i + 8]
        system, user = prompts.scoring(ctx, batch, weights, digest)
        data = runner.llm.complete_json(system, user, schemas.SCORES_SCHEMA,
                                        stage="scoring", max_tokens=4000)
        by_id = {c["id"]: c for c in batch}
        for s in data["scores"]:
            c = by_id.get(s["id"])
            if not c:
                continue
            scores = {d: float(s[d]) for d in DIMENSIONS}
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
    result = {"weights": weights, "note": note, "rows": rows, "table_md": table_md}
    runner.save("scoring_table.json", result)
    (runner.run_dir / "scoring_table.md").write_text(table_md, encoding="utf-8")
    runner.log("scoring top5: " + ", ".join(f"{r['name']}={r['weighted_total']}" for r in rows[:5]))
    return result