"""Stage 3: per-idea filtering plus code-triggered saturation checks."""
from __future__ import annotations

from hackidea import prompts, schemas
from hackidea.concurrency import ordered_fanout
from hackidea.search import normalize_url


_THEME_CUES = {
    "edtech": {"education", "educational", "learning", "learner", "teacher", "school", "student", "lesson", "quiz", "classroom", "curriculum"},
    "hospitality": {"hospitality", "tourism", "tour", "hotel", "guest", "travel", "restaurant", "destination", "booking", "guide"},
}


def _hard_guard(ctx: dict, candidate: dict) -> str | None:
    theme = str(ctx.get("theme", "")).lower()
    domain = next((name for name in _THEME_CUES if name in theme), None)
    if domain:
        words = " ".join(str(candidate.get(k, "")) for k in ("name", "pitch", "problem", "target_user")).lower()
        if not any(cue in words for cue in _THEME_CUES[domain]):
            return f"Off-theme: no {domain} user, task, or outcome in the candidate."
    if ctx.get("duration_hours") and ctx["duration_hours"] <= 12:
        if any(term in (candidate.get("data_needed", "") + " " + candidate.get("pitch", "")).lower()
               for term in ("train a model", "collect a dataset", "integrate bank", "production deployment")):
            return "Infeasible in the stated short event: the described scope depends on training, new data collection, or a production integration."
    return None


def _decision(runner, candidate, digest, stage):
    system, user = prompts.filter_one(runner.ctx, candidate, digest)
    result = runner.llm.complete_json(system, user, schemas.FILTER_SCHEMA,
                                      stage=stage, max_tokens=900)
    # Do not trust ids or omissions from model output: this request concerned one known idea.
    row = next((d for d in result["decisions"] if d.get("id") == candidate["id"]), None)
    if row is None:
        raise ValueError(f"filter model omitted requested id {candidate['id']}")
    return row


def run(runner) -> dict:
    ctx = runner.ctx
    candidates = runner.load("candidates.json")["candidates"]
    research = runner.load("research_log.json")
    digest = prompts.build_digest(research["sources"], 40)
    by_id = {c["id"]: c for c in candidates}

    def classify(candidate):
        duration = ctx.get("duration_hours") or 0
        candidate["demo_scope"] = (
            f"In {duration:g} hours, implement one end-to-end {candidate.get('problem','user problem')} workflow "
            "with a single screen/action, one AI result grounded in the selected fetched evidence, and 5-10 "
            "hand-authored or synthetic fixtures. Mock external APIs; no live account or production integration.")
        stage = f"filter_{candidate['id']}"
        try:
            row = _decision(runner, candidate, digest, stage)
        except Exception as exc:
            runner.log(f"  filter failed for {candidate['id']}: {type(exc).__name__}; discarded conservatively")
            row = {"id": candidate["id"], "keep": False,
                   "reason": "Filtering could not verify this idea; discarded conservatively.",
                   "category": "infeasible", "needs_saturation_check": False}
        guard = _hard_guard(ctx, candidate)
        if guard:
            row.update(keep=False, reason=guard, category="offtheme" if guard.startswith("Off-theme") else "infeasible")
        return row

    outcomes = ordered_fanout(
        candidates, classify, workers=runner.cfg.llm_concurrency,
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("filtering classify", done, total))
    if runner.cancel_requested:
        raise InterruptedError("filtering classification cancelled")
    decisions = {}
    for candidate, outcome in zip(candidates, outcomes):
        if outcome.error is None and outcome.value is not None:
            decisions[candidate["id"]] = outcome.value
        else:
            runner.log(f"  filter item failed for {candidate['id']}; discarded conservatively")
            decisions[candidate["id"]] = {"id": candidate["id"], "keep": False,
                "reason": "Filtering item failed; discarded conservatively.",
                "category": "infeasible", "needs_saturation_check": False}

    # Search saturation in code for a deterministic shortlist of survivors, even if the
    # model failed to ask. The cap keeps search/fetch volume near the original standard run.
    survivors = [c for c in candidates if decisions[c["id"]].get("keep")]
    flagged = [by_id[d["id"]] for d in decisions.values()
               if d.get("needs_saturation_check") and d["id"] in by_id]
    check_candidates = []
    for c in flagged + survivors:
        if c not in check_candidates and len(check_candidates) < 8:
            check_candidates.append(c)

    checks_by_id = {}
    fetched_evidence = []
    query_rows = [(c, f'"{c["name"]}" {c.get("target_user", "")} {ctx.get("theme", "")} product')
                  for c in check_candidates]
    search_outcomes = ordered_fanout(
        query_rows, lambda row: runner.search.search(row[1], max_results=3)[:2],
        workers=min(runner.cfg.search_workers, max(1, len(query_rows))),
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("filtering saturation search", done, total))
    if runner.cancel_requested:
        raise InterruptedError("filtering saturation searches cancelled")
    fetch_rows = []
    for (candidate, query), outcome in zip(query_rows, search_outcomes):
        hits = outcome.value or []
        checks_by_id[candidate["id"]] = {"id": candidate["id"], "query": query, "results": []}
        for hit in hits:
            fetch_rows.append((candidate, query, hit))
    fetch_outcomes = ordered_fanout(
        fetch_rows, lambda row: runner.fetch(normalize_url(row[2].url)),
        workers=min(runner.cfg.search_workers, max(1, len(fetch_rows))),
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("filtering saturation fetch", done, total))
    if runner.cancel_requested:
        raise InterruptedError("filtering saturation fetches cancelled")
    for (candidate, query, hit), outcome in zip(fetch_rows, fetch_outcomes):
        page = outcome.value if outcome.error is None else None
        url = normalize_url(hit.url)
        entry = {"url": url, "title": hit.title, "snippet": hit.snippet,
                 "query": query, "source_type": "market_products", "fetched": bool(page),
                 "date": (page or {}).get("date", ""), "text": (page or {}).get("text", ""),
                 "takeaway": "Candidate saturation check; assess only the overlap supported by this page."}
        checks_by_id[candidate["id"]]["results"].append(entry)
        if page:
            fetched_evidence.append(entry)
    checks = [checks_by_id[c["id"]] for c in check_candidates]
    for check in checks:
        runner.log(f"  saturation check {check['id']}: {sum(x['fetched'] for x in check['results'])} pages fetched")

    # Revisit each searched candidate independently; failed items retain their
    # original decision while successful rechecks are applied in candidate order.
    recheck_rows = []
    for check in checks:
        pages = [x for x in check["results"] if x["fetched"]]
        if pages:
            candidate = by_id[check["id"]]
            extra = "\n".join(f"- {x['title']} | {x['url']} | {x['text'][:1000]}" for x in pages)
            recheck_rows.append((candidate, digest + "\n\nNEW FETCHED SATURATION EVIDENCE:\n" + extra))
    def recheck(row):
        candidate, detail = row
        revised = _decision(runner, candidate, detail, f"filter_recheck_{candidate['id']}")
        revised["saturation_reviewed"] = True
        return revised
    recheck_outcomes = ordered_fanout(
        recheck_rows, recheck, workers=runner.cfg.llm_concurrency,
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("filtering recheck", done, total))
    if runner.cancel_requested:
        raise InterruptedError("filtering rechecks cancelled")
    for (candidate, _), outcome in zip(recheck_rows, recheck_outcomes):
        if outcome.error is not None:
            runner.log(f"  saturation recheck failed for {candidate['id']}: {type(outcome.error).__name__}")
        else:
            decisions[candidate["id"]] = outcome.value

    if fetched_evidence:
        existing = {normalize_url(s["url"]) for s in research["sources"]}
        added = []
        for source in fetched_evidence:
            url = normalize_url(source["url"])
            if url in existing:
                continue
            existing.add(url)
            added.append(source)
        research["sources"].extend(added)
        research["stats"]["fetched"] += len(added)
        runner.save("research_log.json", research)

    kept = [c for c in candidates if decisions[c["id"]].get("keep")]
    runner.save("candidates.json", {"candidates": candidates})
    result = {"decisions": [decisions[c["id"]] for c in candidates],
              "kept_ids": [c["id"] for c in kept], "saturation_checks": checks}
    runner.save("filtering.json", result)
    runner.log(f"filtering: {len(kept)}/{len(candidates)} kept; categories="
               f"{ {cat: sum(d.get('category') == cat for d in decisions.values()) for cat in ('ok','infeasible','wrapper','saturated','offtheme')} }")
    return result
