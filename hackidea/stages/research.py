"""Stage 1: research — >=N searches across >=6 source types, fetch pages, log every source."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from hackidea import prompts, schemas
from hackidea.config import SOURCE_TYPES
from hackidea.search import normalize_url

TAKEAWAYS_SCHEMA = {
    "type": "object", "required": ["takeaways"],
    "properties": {"takeaways": {"type": "array", "items": {
        "type": "object", "required": ["url", "takeaway"],
        "properties": {"url": {"type": "string"}, "takeaway": {"type": "string"},
                       "date_hint": {"type": "string"}}}}},
}

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
    runner.save("research_plan.json", {"queries": queries})
    runner.log(f"research plan: {len(queries)} queries, {len({q['source_type'] for q in queries})} source types")

    # --- execute searches, dedup URLs globally ---
    search_started = time.perf_counter()
    seen: set[str] = set()
    hits: list[dict] = []
    for i, q in enumerate(queries):
        results = runner.search.search(q["query"], max_results=8)
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
        time.sleep(0.15)
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
        # Network waits dominate this stage. Fetch a small bounded batch in parallel;
        # executor.map preserves order and URLs were globally deduplicated above.
        workers = min(6, len(fetch_order))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pages = pool.map(lambda hit: runner.fetch(hit["url"]), fetch_order)
            pages_by_url = {hit["url"]: page for hit, page in zip(fetch_order, pages)}
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
    for i in range(0, len(fetched), 5):
        batch = fetched[i:i + 5]
        system, user = prompts.takeaways(batch)
        try:
            data = runner.llm.complete_json(system, user, TAKEAWAYS_SCHEMA,
                                            stage="takeaways", max_tokens=4000)
            for t in data["takeaways"]:
                s = by_url.get(normalize_url(t["url"])) or by_url.get(t["url"])
                if s:
                    s["takeaway"] = t["takeaway"][:300]
                    if not s["date"] and t.get("date_hint"):
                        s["date"] = t["date_hint"]
        except Exception as e:  # noqa: BLE001 - takeaways are enrichment, not fatal
            runner.log(f"  takeaway batch failed ({e}); using snippets")
    for s in sources:
        if not s["takeaway"]:
            s["takeaway"] = (s["snippet"] or s["title"])[:200]

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
