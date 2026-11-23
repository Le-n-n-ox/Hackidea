"""Stage 3: per-idea filtering plus code-triggered saturation checks."""
from __future__ import annotations

from hackidea import prompts, schemas
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

    decisions = {}
    for candidate in candidates:
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
        decisions[candidate["id"]] = row

    # Search saturation in code for a deterministic shortlist of survivors, even if the
    # model failed to ask. The cap keeps search/fetch volume near the original standard run.
    survivors = [c for c in candidates if decisions[c["id"]].get("keep")]
    flagged = [by_id[d["id"]] for d in decisions.values()
               if d.get("needs_saturation_check") and d["id"] in by_id]
    check_candidates = []
    for c in flagged + survivors:
        if c not in check_candidates and len(check_candidates) < 8:
            check_candidates.append(c)

    checks, fetched_evidence = [], []
    for candidate in check_candidates:
        query = f'"{candidate["name"]}" {candidate.get("target_user", "")} {ctx.get("theme", "")} product'
        found = []
        for hit in runner.search.search(query, max_results=3)[:2]:
            url = normalize_url(hit.url)
            page = runner.fetch(url)
            entry = {"url": url, "title": hit.title, "snippet": hit.snippet,
                     "query": query, "source_type": "market_products", "fetched": bool(page),
                     "date": (page or {}).get("date", ""), "text": (page or {}).get("text", ""),
                     "takeaway": "Candidate saturation check; assess only the overlap supported by this page."}
            found.append(entry)
            if page:
                fetched_evidence.append(entry)
        checks.append({"id": candidate["id"], "query": query, "results": found})
        runner.log(f"  saturation check {candidate['id']}: {sum(x['fetched'] for x in found)} pages fetched")

    # Revisit flagged or code-searched ideas only when new pages were actually fetched.
    for check in checks:
        pages = [x for x in check["results"] if x["fetched"]]
        if not pages:
            continue
        candidate = by_id[check["id"]]
        extra = "\n".join(f"- {x['title']} | {x['url']} | {x['text'][:1000]}" for x in pages)
        try:
            revised = _decision(runner, candidate, digest + "\n\nNEW FETCHED SATURATION EVIDENCE:\n" + extra,
                                f"filter_recheck_{candidate['id']}")
            revised["saturation_reviewed"] = True
            decisions[candidate["id"]] = revised
        except Exception as exc:
            runner.log(f"  saturation recheck failed for {candidate['id']}: {type(exc).__name__}")

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
