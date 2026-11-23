"""Small, targeted documentation research performed after winner selection."""
from __future__ import annotations

import time
from urllib.parse import urlparse

from hackidea.concurrency import ordered_fanout
from hackidea.search import normalize_url


TARGETS = [
    {
        "id": "at_sandbox",
        "kind": "documentation",
        "query": "site:help.africastalking.com/en/articles Africa's Talking sandbox simulator two-way SMS USSD quick start",
        "hosts": ("help.africastalking.com", "africastalkingltd.gitbooks.io"),
        "fallback_url": "https://help.africastalking.com/en/articles/1170660-how-do-i-get-started-on-the-africa-s-talking-sandbox",
        "used_for": ["sandbox flow and test recipient"],
    },
    {
        "id": "at_python_sdk",
        "kind": "documentation",
        "query": "site:github.com/AfricasTalkingLtd/africastalking-python README SMS SDK sandbox initialize send",
        "hosts": ("github.com",),
        "path_prefix": "/AfricasTalkingLtd/africastalking-python",
        "fallback_url": "https://github.com/AfricasTalkingLtd/africastalking-python",
        "used_for": ["Python SDK setup and SMS send call"],
    },
    {
        "id": "gemini_pricing",
        "kind": "documentation",
        "query": "site:ai.google.dev/gemini-api/docs/pricing Gemini 3.5 Flash-Lite free tier",
        "hosts": ("ai.google.dev", "aistudio.google.com"),
        "fallback_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "used_for": ["selected model free-tier availability"],
    },
    {
        "id": "gemini_python",
        "kind": "documentation",
        "query": "site:github.com/googleapis/python-genai README install google-genai Gemini Python JSON schema",
        "hosts": ("github.com",),
        "path_prefix": "/googleapis/python-genai/blob/main/README.md",
        "fallback_url": "https://github.com/googleapis/python-genai/blob/main/README.md",
        "used_for": ["official google-genai install, client setup, and JSON schema output"],
    },
    {
        "id": "fastapi_quickstart",
        "kind": "documentation",
        "query": "site:fastapi.tiangolo.com/tutorial/first-steps Python FastAPI endpoint development server",
        "hosts": ("fastapi.tiangolo.com",),
        "fallback_url": "https://fastapi.tiangolo.com/tutorial/first-steps/",
        "used_for": ["web application route and local server"],
    },
]


def _allowed(url: str, target: dict) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host_ok = any(parsed.hostname == host or (parsed.hostname or "").endswith("." + host)
                  for host in target["hosts"])
    return host_ok and parsed.path.startswith(target.get("path_prefix", "/"))


def run_blueprint_research(runner) -> dict:
    """Run five targeted searches; fetch at most one official page per target.

    Search-engine output is treated as untrusted discovery. Pages are accepted only
    from explicitly allow-listed official documentation hosts, and only successful
    page fetches are added to the artifact or made available to the report.
    """
    search_results = ordered_fanout(
        TARGETS,
        lambda target: runner.search.search(target["query"], max_results=5),
        workers=min(getattr(runner.cfg, "search_workers", 4), len(TARGETS)),
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("blueprint research", done, total),
    )
    if runner.cancel_requested:
        raise InterruptedError("blueprint documentation search cancelled")

    selected = []
    search_rows = []
    for target, outcome in zip(TARGETS, search_results):
        hits = outcome.value or []
        valid = [hit for hit in hits if _allowed(hit.url, target)]
        chosen = valid[0] if valid else None
        url = normalize_url(chosen.url) if chosen else target["fallback_url"]
        selected.append((target, url, chosen))
        search_rows.append({"id": target["id"], "query": target["query"],
                            "result_count": len(hits), "selected_url": url,
                            "used_search_result": bool(chosen)})

    def fetch_official_page(row):
        target, discovered_url, hit = row
        urls = [discovered_url]
        if discovered_url != target["fallback_url"]:
            urls.append(target["fallback_url"])
        for url in urls:
            try:
                page = runner.fetch(url)
            except Exception:
                page = None
            if not page or len(page.get("text", "")) < 180:
                continue
            final_url = normalize_url(page.get("final_url") or page.get("url") or url)
            if _allowed(final_url, target):
                return {"page": page, "url": final_url, "hit": hit}
        return None

    fetch_rows = ordered_fanout(
        selected,
        fetch_official_page,
        workers=min(getattr(runner.cfg, "search_workers", 4), len(selected)),
        cancelled=lambda: runner.cancel_requested,
        progress=lambda done, total: runner.item_progress("blueprint documentation fetch", done, total),
    )
    if runner.cancel_requested:
        raise InterruptedError("blueprint documentation fetch cancelled")

    sources = []
    for (target, url, hit), outcome in zip(selected, fetch_rows):
        fetched = outcome.value if outcome.error is None else None
        if not fetched:
            continue
        page = fetched["page"]
        final_url = fetched["url"]
        search_row = next(item for item in search_rows if item["id"] == target["id"])
        search_row["selected_url"] = final_url
        search_row["used_search_result"] = bool(fetched["hit"] and final_url == normalize_url(hit.url))
        sources.append({
            "id": target["id"],
            "kind": target["kind"],
            "title": (page.get("title") or (hit.title if hit else "") or target["id"]).strip(),
            "url": final_url,
            "date": page.get("date", ""),
            "query": target["query"],
            "used_for": target["used_for"],
            "text": page["text"][:9000],
        })
    result = {"queries": search_rows, "sources": sources,
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    runner.save("blueprint_research.json", result)
    runner.log(f"blueprint research: {len(search_rows)} targeted searches, {len(sources)} official pages fetched")
    return result
