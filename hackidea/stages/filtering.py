"""Stage 3: filtering — discard infeasible / wrapper / saturated / off-theme, with search verification."""
from __future__ import annotations

import time

from hackidea import prompts, schemas
from hackidea.search import normalize_url

RECHECK_SCHEMA = schemas.FILTER_SCHEMA


def run(runner) -> dict:
    ctx = runner.ctx
    cands = runner.load("candidates.json")["candidates"]
    log = runner.load("research_log.json")
    evidence = [s for s in log["sources"]
                if s.get("source_type") in ("market_products", "past_winners")] or log["sources"]
    digest = prompts.build_digest(evidence, 30)

    system, user = prompts.filtering(ctx, cands, digest)
    data = runner.llm.complete_json(system, user, schemas.FILTER_SCHEMA,
                                    stage="filtering", max_tokens=5000)
    decisions = {d["id"]: d for d in data["decisions"]}
    for c in cands:
        decisions.setdefault(c["id"], {"id": c["id"], "keep": True,
                                       "reason": "not reviewed; kept by default", "category": "ok"})

    # --- verify suspected saturation with real searches + fetches ---
    by_id = {c["id"]: c for c in cands}
    checks = []
    flagged = [d for d in decisions.values() if d.get("needs_saturation_check")][:6]
    for d in flagged:
        c = by_id.get(d["id"])
        if not c:
            continue
        query = f'"{c["name"]}" OR "{c["pitch"][:60]}" product app hackathon'
        hits = runner.search.search(query, max_results=5)
        found = []
        for h in hits[:2]:
            url = normalize_url(h.url)
            page = runner.fetch(url)
            entry = {"url": url, "title": h.title, "snippet": h.snippet, "query": query,
                     "source_type": "market_products", "fetched": bool(page),
                     "date": (page or {}).get("date", ""), "text": (page or {}).get("text", ""),
                     "takeaway": f"Saturation check for {c['name']}: {h.snippet[:150]}"}
            log["sources"].append(entry)
            found.append(entry)
            time.sleep(0.3)
        checks.append({"id": d["id"], "query": query, "results": found})
        runner.log(f"  saturation check {c['name']}: {len(found)} sources")

    if checks:
        runner.save("research_log.json", log)
        extra = "\n".join(f"- {s['url']} — {s['takeaway']}" for ch in checks for s in ch["results"])
        system2, user2 = prompts.filtering(ctx, [by_id[d["id"]] for d in flagged if d["id"] in by_id],
                                           digest + "\nNEW SATURATION EVIDENCE:\n" + extra)
        try:
            re_data = runner.llm.complete_json(system2, user2, RECHECK_SCHEMA,
                                               stage="filtering_recheck", max_tokens=4000)
            for d in re_data["decisions"]:
                if d["id"] in decisions:
                    decisions[d["id"]] = d
        except Exception as e:  # noqa: BLE001
            runner.log(f"  saturation recheck failed: {e}")

    kept = [c for c in cands if decisions[c["id"]].get("keep")]
    # Pipeline needs finalists: if too few survive, reinstate the least-bad discards.
    if len(kept) < 5:
        priority = {"saturated": 0, "wrapper": 1, "infeasible": 2, "offtheme": 3, "ok": 4}
        discards = sorted((c for c in cands if not decisions[c["id"]].get("keep")),
                          key=lambda c: priority.get(decisions[c["id"]].get("category", "ok"), 4))
        for c in discards:
            if len(kept) >= 5:
                break
            decisions[c["id"]]["keep"] = True
            decisions[c["id"]]["reinstated"] = True
            kept.append(c)
        runner.log(f"  WARNING: only {len(kept)} survivors after filtering; reinstated least-bad discards")

    result = {"decisions": list(decisions.values()), "kept_ids": [c["id"] for c in kept],
              "saturation_checks": checks}
    runner.save("filtering.json", result)
    runner.log(f"filtering: {len(kept)}/{len(cands)} kept")
    return result