"""Stage 1: research — >=N searches across >=6 source types, fetch pages, log every source."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from hackidea import prompts, schemas
from hackidea.concurrency import ordered_fanout
from hackidea.config import SOURCE_TYPES
from hackidea.search import normalize_url

TAKEAWAYS_SCHEMA = {
    "type": "object", "required": ["takeaways"],
    "properties": {"takeaways": {"type": "array", "items": {
        "type": "object", "required": ["url", "takeaway"],
        "properties": {"url": {"type": "string"}, "takeaway": {"type": "string"},
                       "date_hint": {"type": "string"}}}}},
}

PROBLEMS_SCHEMA = {"type": "object", "required": ["problems"], "properties": {
    "problems": {"type": "array", "items": {"type": "object",
        "required": ["who", "task", "evidence_url"], "properties": {
            "who": {"type": "string"}, "task": {"type": "string"},
            "evidence_url": {"type": "string"}}}}}}


def _theme_relevant(theme: str, page: dict) -> bool:
    import re
    text = " ".join((page.get("title", ""), page.get("text", ""), page.get("snippet", ""))).lower()
    terms = {x for x in re.findall(r"[a-z0-9]+", theme.lower())
             if len(x) > 3 and x not in {"solutions", "solution", "program", "global"}}
    aliases = {"edtech": {"education", "learning", "learner", "teacher", "school", "student"},
               "education": {"edtech", "learning", "learner", "teacher", "school", "student"},
               "hospitality": {"hotel", "tourism", "travel", "guest", "restaurant", "tour"},
               "tourism": {"hotel", "hospitality", "travel", "guest", "restaurant", "tour"}}
    expanded = terms | set().union(*(aliases.get(t, set()) for t in terms)) if terms else set()
    return not expanded or sum(t in text for t in expanded) >= 1

_TYPE_FALLBACK_QUERY = {
    "past_winners": "{theme} hackathon winners devpost {recent_years}",
    "sponsor_apis": "{sponsor} API new features launch {year}",
    "trending_repos": "{theme} github trending open source {year}",
    "models_papers": "{theme} new model arxiv huggingface {year}",
    "news_launches": "{theme} startup launch product hunt hacker news {recent_years}",
    "user_problems": "{theme} biggest problems complaints report {recent_years}",
    "market_products": "{theme} startups existing products comparison {year}",
    "local_context": "{theme} {region} mobile money USSD constraints {year}",
}


def _ensure_coverage(queries: list[dict], ctx: dict, n: int) -> list[dict]:
    """Guarantee >= n queries and >= 6 distinct source types (deterministic top-up)."""
    queries = [q for q in queries if q.get("source_type") in SOURCE_TYPES]
    have = {q.get("source_type") for q in queries}
    theme = ctx.get("theme", "AI")
    sponsor = (ctx.get("sponsors") or [""])[0]
    region = ctx.get("audience_region") or "emerging markets"
    year = datetime.now(timezone.utc).year
    values = {"theme": theme, "sponsor": sponsor, "region": region,
              "year": year, "recent_years": f"{year-1}/{year}"}
    for t in SOURCE_TYPES:
        if t not in have:
            q = _TYPE_FALLBACK_QUERY[t].format(**values)
            queries.append({"query": q, "source_type": t, "rationale": "coverage top-up"})
            have.add(t)
    i = 0
    while len(queries) < n:
        t = SOURCE_TYPES[i % len(SOURCE_TYPES)]
        q = _TYPE_FALLBACK_QUERY[t].format(**values) + f" case study {i}"
        queries.append({"query": q, "source_type": t, "rationale": "count top-up"})
        i += 1
    return queries


def run(runner) -> dict:
    ctx, preset = runner.ctx, runner.preset
    n = preset["searches"]
    system, user = prompts.research_plan(ctx, n)
    plan = runner.llm.complete_json(system, user, schemas.QUERY_PLAN_SCHEMA,
                                    stage="research_plan", max_tokens=4500)
    queries = _ensure_coverage(plan["queries"], ctx, n)
    # Reserve at least one third of quick searches for real user pain. The model
    # plan remains useful for phrasing; this invariant fixes source coverage.
    target_problems = max(4, n // 3)
    problem_count = sum(q["source_type"] == "user_problems" for q in queries)
    year = datetime.now(timezone.utc).year
    values = {"theme": ctx.get("theme", ""), "sponsor": "",
              "region": ctx.get("audience_region") or "Africa", "year": year,
              "recent_years": f"{year-1}/{year}"}
    for i in range(target_problems - problem_count):
        counts = {t: sum(q["source_type"] == t for q in queries) for t in SOURCE_TYPES}
        replace_at = next((j for j in range(len(queries) - 1, -1, -1)
                           if queries[j]["source_type"] != "user_problems"
                           and counts[queries[j]["source_type"]] > 1), None)
        if replace_at is None:
            break
        queries[replace_at] = {"query": _TYPE_FALLBACK_QUERY["user_problems"].format(
            **values),
            "source_type": "user_problems", "rationale": "enforced user problem coverage"}
    runner.save("research_plan.json", {"queries": queries})
    runner.log(f"research plan: {len(queries)} queries, {len({q['source_type'] for q in queries})} source types")

    # --- execute searches, dedup URLs globally ---
    search_started = time.perf_counter()
    seen: set[str] = set()
    hits: list[dict] = []
    workers = min(getattr(runner.cfg, "search_workers", 4), len(queries))
    outcomes = ordered_fanout(
        queries, lambda q: runner.search.search(q["query"], max_results=8), workers=workers,
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("research search", done, total))
    if runner.cancel_requested:
        raise InterruptedError("research search cancelled")
    for i, (q, outcome) in enumerate(zip(queries, outcomes)):
        results = outcome.value or []
        added = 0
        for h in results:
            url = normalize_url(h.url)
            if not url or url in seen:
                continue
            seen.add(url)
            hits.append({"url": url, "title": h.title, "snippet": h.snippet,
                         "query": q["query"], "source_type": q["source_type"]})
            added += 1
        runner.log(f"  search {i+1}/{len(queries)} [{q['source_type']}] +{added} ({q['query'][:60]})")
    search_seconds = round(time.perf_counter() - search_started, 2)
    runner.save("search_results.json", {"hits": hits})

    # --- fetch top K pages per query (only fetched pages become citable) ---
    # Fetch results in balanced source-type rounds so early types cannot consume
    # the whole budget and low-volume types (especially news/problems) are seen.
    hit_by_query: dict[str, list[dict]] = {}
    k = preset["fetch_per_query"]
    for h in hits:
        hit_by_query.setdefault(h["query"], []).append(h)
    fetch_order = []
    for result_index in range(k):
        for source_type in SOURCE_TYPES:
            for q in queries:
                if q["source_type"] != source_type:
                    continue
                query_hits = hit_by_query.get(q["query"], [])
                if result_index < len(query_hits):
                    fetch_order.append(query_hits[result_index])

    per_query: dict[str, int] = {}
    sources: list[dict] = []
    fetched_urls: set[str] = set()
    fetch_started = time.perf_counter()
    selected = {h["url"] for h in fetch_order}
    pages_by_url = {}
    if fetch_order:
        outcomes = ordered_fanout(
            fetch_order, lambda hit: runner.fetch(hit["url"]),
            workers=min(6, getattr(runner.cfg, "search_workers", 4), len(fetch_order)),
            cancelled=lambda: runner.cancel_requested,
            progress=lambda done, total: runner.item_progress("research fetch", done, total))
        if runner.cancel_requested:
            raise InterruptedError("research fetch cancelled")
        pages_by_url = {hit["url"]: outcome.value for hit, outcome in zip(fetch_order, outcomes)
                        if outcome.error is None and not outcome.cancelled}
    ordered_hits = fetch_order + [h for h in hits if h["url"] not in selected]
    for h in ordered_hits:
        pq = per_query.get(h["query"], 0)
        entry = {**h, "fetched": False, "date": "", "text": "", "takeaway": ""}
        if h["url"] in selected and pq < k:
            page = pages_by_url.get(h["url"])
            per_query[h["query"]] = pq + 1
            if page:
                entry.update(fetched=True, date=page.get("date", ""),
                             text=page.get("text", ""), title=page.get("title") or h["title"])
                fetched_urls.add(h["url"])
            time.sleep(0.05)
        sources.append(entry)
    fetch_seconds = round(time.perf_counter() - fetch_started, 2)
    runner.log(f"fetched {len(fetched_urls)} pages successfully")

    # --- one-line takeaways for fetched sources (batched LLM) ---
    fetched = [s for s in sources if s["fetched"]]
    by_url = {s["url"]: s for s in sources}
    takeaway_started = time.perf_counter()
    batches = [fetched[i:i + 5] for i in range(0, len(fetched), 5)]
    def make_takeaway(batch):
        system, user = prompts.takeaways(batch)
        return runner.llm.complete_json(system, user, TAKEAWAYS_SCHEMA,
                                        stage="takeaways", max_tokens=4000)
    takeaway_outcomes = ordered_fanout(
        batches, make_takeaway, workers=runner.cfg.llm_concurrency,
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("research takeaways", done, total))
    if runner.cancel_requested:
        raise InterruptedError("research takeaways cancelled")
    for batch, outcome in zip(batches, takeaway_outcomes):
        if outcome.error:
            runner.log(f"  takeaway batch failed ({type(outcome.error).__name__}); using snippets")
            continue
        if outcome.value:
            data = outcome.value
            for t in data["takeaways"]:
                s = by_url.get(normalize_url(t["url"])) or by_url.get(t["url"])
                if s:
                    s["takeaway"] = t["takeaway"][:300]
                    if not s["date"] and t.get("date_hint"):
                        s["date"] = t["date_hint"]
    for s in sources:
        if not s["takeaway"]:
            s["takeaway"] = (s["snippet"] or s["title"])[:200]

    # Relevance and problem extraction are enforced before ideation consumes sources.
    for s in sources:
        s["relevant"] = bool(s.get("fetched") and _theme_relevant(ctx.get("theme", ""), s))
    problems = []
    relevant_pages = [s for s in sources if s.get("relevant") and s.get("source_type") == "user_problems"]
    if relevant_pages:
        page_text = "\n\n".join(f"URL: {s['url']}\nTITLE: {s['title']}\nTEXT: {s.get('text','')[:1400]}" for s in relevant_pages[:12])
        try:
            extracted = runner.llm.complete_json(
                "Extract distinct concrete user problems shown by these pages. Do not infer beyond page text.",
                f"Theme: {ctx.get('theme')}\n{page_text}\nReturn who, task and evidence_url.",
                PROBLEMS_SCHEMA, stage="research_problems", max_tokens=3500)
            allowed = {s["url"] for s in relevant_pages}
            for item in extracted["problems"]:
                url = normalize_url(item["evidence_url"])
                if url in allowed and item["who"].strip() and item["task"].strip():
                    problems.append({"who": item["who"].strip(), "task": item["task"].strip(), "evidence_url": url})
        except Exception as exc:
            runner.log(f"  problem extraction failed ({type(exc).__name__})")
    runner.save("problems.json", {"problems": problems})

    stats = {"queries_executed": len(queries),
             "source_types": sorted({q["source_type"] for q in queries}),
             "unique_urls": len(sources), "fetched": len(fetched_urls),
             "fetched_by_type": {t: sum(1 for s in fetched if s["source_type"] == t)
                                 for t in SOURCE_TYPES},
             "timings_seconds": {"search": search_seconds, "fetch": fetch_seconds,
                                 "takeaways": round(time.perf_counter() - takeaway_started, 2)}}
    log = {"sources": sources, "stats": stats}
    runner.save("research_log.json", log)
    runner.log(f"research log: {stats['unique_urls']} sources, {stats['fetched']} fetched, "
               f"types={stats['source_types']}")
    return log
