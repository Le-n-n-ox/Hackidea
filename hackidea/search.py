"""Pluggable search providers + page fetching with cache, retries, URL dedup."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, asdict
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

from hackidea.cache import DiskCache

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 HackIdea/0.1")

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$)", re.I)


def normalize_url(url: str) -> str:
    """Canonical form for dedup: https, lowercase host, no tracking params, no trailing slash."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    scheme = "https"
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    qs = [(k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)]
    return urlunparse((scheme, host, path, "", urlencode(qs), ""))


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str


class DDGSSearch:
    """Free meta-search via ddgs. Tries backends in configured order until one returns
    results (some backends are blocked on some networks). No API key needed."""

    def __init__(self, cfg, cache: DiskCache):
        self.cfg = cfg
        self.cache = cache
        self.backends = [b.strip() for b in cfg.ddgs_backends.split(",") if b.strip()]

    def search(self, query: str, max_results: int = 8, *, deadline_at: float | None = None) -> list[SearchHit]:
        key = json.dumps([query, max_results, self.backends], sort_keys=True)
        cached = self.cache.get("search", key, max_age_s=7 * 86400)
        if cached is not None:
            return [SearchHit(**h) for h in cached]
        from ddgs import DDGS
        hits: list[SearchHit] = []
        errors = []
        for backend in self.backends:
            for attempt in range(2):
                remaining = self.cfg.search_timeout
                if deadline_at is not None:
                    remaining = min(remaining, deadline_at - time.monotonic())
                    if remaining <= 0:
                        break
                try:
                    rows = DDGS(timeout=max(0.1, remaining)).text(
                        query, max_results=max_results, backend=backend)
                    hits = [SearchHit(url=r.get("href", ""), title=r.get("title", ""),
                                      snippet=r.get("body", ""))
                            for r in (rows or []) if r.get("href")]
                    if hits:
                        break
                except Exception as e:  # noqa: BLE001 - provider errors are expected
                    errors.append(f"{backend}: {str(e)[:120]}")
                    wait_for = 1.5 * (attempt + 1)
                    if deadline_at is not None:
                        wait_for = min(wait_for, max(0.0, deadline_at - time.monotonic()))
                    if wait_for:
                        time.sleep(wait_for)
            if hits:
                break
            if deadline_at is not None:
                delay = min(0.8, max(0.0, deadline_at - time.monotonic()))
                if delay <= 0:
                    break
                time.sleep(delay)  # politeness between backends, within deadline
                if time.monotonic() >= deadline_at:
                    break
            else:
                time.sleep(0.8)  # politeness between backends
        if not hits and errors:
            print(f"    [search] no results for {query!r} ({'; '.join(errors[:2])})")
        self.cache.set("search", key, [asdict(h) for h in hits])
        return hits


def _request_with_backoff(method: str, url: str, *, tries: int = 3,
                          deadline_at: float | None = None, **kwargs) -> requests.Response:
    """HTTP call for the keyed search APIs. They rate-limit by requests per second,
    so a 429 is waited out and retried instead of failing the query."""
    for attempt in range(tries):
        if deadline_at is not None:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout("provider query deadline exceeded")
            kwargs["timeout"] = min(float(kwargs.get("timeout", remaining)), remaining)
        resp = requests.request(method, url, **kwargs)
        if resp.status_code != 429 or attempt == tries - 1:
            break
        try:
            wait = float(resp.headers.get("Retry-After") or 0)
        except ValueError:
            wait = 0.0
        delay = wait or 1.2 * (attempt + 1)
        if deadline_at is not None:
            delay = min(delay, max(0.0, deadline_at - time.monotonic()))
            if time.monotonic() + delay >= deadline_at:
                break
        time.sleep(delay)
    resp.raise_for_status()
    return resp


class TavilySearch:
    def __init__(self, cfg, cache: DiskCache):
        self.cfg, self.cache = cfg, cache

    def search(self, query: str, max_results: int = 8, *, deadline_at: float | None = None) -> list[SearchHit]:
        key = json.dumps(["tavily", query, max_results], sort_keys=True)
        cached = self.cache.get("search", key, max_age_s=7 * 86400)
        if cached is not None:
            return [SearchHit(**h) for h in cached]
        resp = _request_with_backoff("POST", "https://api.tavily.com/search",
                                     json={"api_key": self.cfg.tavily_api_key, "query": query,
                                           "max_results": max_results},
                                     timeout=self.cfg.search_timeout, deadline_at=deadline_at)
        hits = [SearchHit(url=r["url"], title=r.get("title", ""), snippet=r.get("content", ""))
                for r in resp.json().get("results", [])]
        self.cache.set("search", key, [asdict(h) for h in hits])
        return hits


class BraveSearch:
    def __init__(self, cfg, cache: DiskCache):
        self.cfg, self.cache = cfg, cache

    def search(self, query: str, max_results: int = 8, *, deadline_at: float | None = None) -> list[SearchHit]:
        key = json.dumps(["brave", query, max_results], sort_keys=True)
        cached = self.cache.get("search", key, max_age_s=7 * 86400)
        if cached is not None:
            return [SearchHit(**h) for h in cached]
        resp = _request_with_backoff("GET", "https://api.search.brave.com/res/v1/web/search",
                                     params={"q": query, "count": max_results},
                                     headers={"X-Subscription-Token": self.cfg.brave_api_key,
                                              "Accept": "application/json"},
                                     timeout=self.cfg.search_timeout, deadline_at=deadline_at)
        hits = [SearchHit(url=r["url"], title=r.get("title", ""), snippet=r.get("description", ""))
                for r in resp.json().get("web", {}).get("results", [])]
        self.cache.set("search", key, [asdict(h) for h in hits])
        return hits


class MockSearch:
    """Canned results for tests. `hits` may be a list or a callable(query)->list."""

    def __init__(self, hits):
        self.hits = hits
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 8) -> list[SearchHit]:
        self.queries.append(query)
        rows = self.hits(query) if callable(self.hits) else self.hits
        out = []
        for r in rows[:max_results]:
            out.append(r if isinstance(r, SearchHit) else SearchHit(**r))
        return out


_RRF_K = 60  # standard reciprocal-rank-fusion constant


def fuse_hits(result_lists: list[list[SearchHit]], max_results: int) -> list[SearchHit]:
    """Merge ranked lists from several providers by reciprocal-rank fusion.

    Each provider that returned a URL adds 1/(60 + rank) to its score, so a URL
    several engines agree on outranks one that only a single engine returned,
    and single-engine results interleave by rank. Duplicates are matched on the
    normalized URL; the longest snippet is kept.
    """
    merged: dict[str, dict] = {}
    for hits in result_lists:
        seen_here: set[str] = set()
        for rank, h in enumerate(hits, start=1):
            key = normalize_url(h.url)
            if not key or key in seen_here:
                continue
            seen_here.add(key)
            entry = merged.setdefault(key, {"hit": SearchHit(h.url, h.title, h.snippet),
                                            "score": 0.0, "order": len(merged)})
            entry["score"] += 1.0 / (_RRF_K + rank)
            if len(h.snippet or "") > len(entry["hit"].snippet or ""):
                entry["hit"].snippet = h.snippet
            if not entry["hit"].title:
                entry["hit"].title = h.title
    ranked = sorted(merged.values(), key=lambda e: (-e["score"], e["order"]))
    return [e["hit"] for e in ranked[:max_results]]


class MultiSearch:
    """Runs every configured provider for each query (in parallel) and fuses the
    results. A provider that errors is skipped for that query, so one outage or
    rate limit cannot fail the run."""

    def __init__(self, providers: list[tuple[str, object]], deadline: int = 12,
                 provider_interval: float = 0.5, query_workers: int = 4):
        self.providers = providers
        self.deadline = deadline
        self.provider_interval = provider_interval
        # Reserve a worker for every provider of every in-flight query. A slow
        # provider can therefore neither queue nor hold up another provider.
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(providers) * query_workers),
                                        thread_name_prefix="hackidea-search")
        self._provider_locks = {name: threading.Lock() for name, _ in providers}
        self._provider_next_start = {name: 0.0 for name, _ in providers}
        self._metrics_lock = threading.Lock()
        self._stage = "unknown"
        self._metrics: dict[str, dict[str, int]] = {}

    def set_current_stage(self, stage: str):
        self._stage = stage

    def metrics_snapshot(self, stage: str | None = None) -> dict:
        with self._metrics_lock:
            if stage is not None:
                return dict(self._metrics.get(stage, {"queries": 0, "deadline_cutoffs": 0}))
            return {k: dict(v) for k, v in self._metrics.items()}

    def _one(self, item: tuple[str, object], query: str, max_results: int,
             deadline_at: float) -> list[SearchHit]:
        name, provider = item
        try:
            # Reserve a paced start under the lock, but never hold the lock while
            # doing network I/O. In-flight slow requests cannot serialize peers.
            with self._provider_locks[name]:
                start_at = max(time.monotonic(), self._provider_next_start[name])
                self._provider_next_start[name] = start_at + self.provider_interval
            wait_for = start_at - time.monotonic()
            if wait_for > 0:
                time.sleep(min(wait_for, max(0.0, deadline_at - time.monotonic())))
            if time.monotonic() >= deadline_at:
                return []
            if isinstance(provider, (DDGSSearch, TavilySearch, BraveSearch)):
                return provider.search(query, max_results=max_results, deadline_at=deadline_at)
            return provider.search(query, max_results=max_results)
        except Exception as e:  # noqa: BLE001 - provider errors are expected
            print(f"    [search] {name} failed for {query[:60]!r}: {str(e)[:120]}",
                  file=sys.stderr, flush=True)
            return []

    def search(self, query: str, max_results: int = 8) -> list[SearchHit]:
        stage = self._stage
        with self._metrics_lock:
            row = self._metrics.setdefault(stage, {"queries": 0, "deadline_cutoffs": 0})
            row["queries"] += 1
        started = time.monotonic()
        deadline_at = started + self.deadline
        futures = [self._pool.submit(self._one, item, query, max_results, deadline_at)
                   for item in self.providers]
        done, pending = wait(futures, timeout=max(0.0, deadline_at - time.monotonic()))
        result_lists = []
        cutoffs = set(pending)
        for item, future in zip(self.providers, futures):
            if future in cutoffs:
                print(f"    [search] {item[0]} exceeded {self.deadline}s total deadline for {query[:60]!r}",
                      file=sys.stderr, flush=True)
                future.cancel()
                result_lists.append([])
            else:
                try:
                    result_lists.append(future.result())
                except Exception:
                    result_lists.append([])
        if cutoffs:
            with self._metrics_lock:
                self._metrics[stage]["deadline_cutoffs"] += len(cutoffs)
        return fuse_hits(result_lists, max_results)


def _make_one(name: str, cfg, cache: DiskCache):
    if name == "ddgs":
        return DDGSSearch(cfg, cache)
    if name == "tavily":
        if not cfg.tavily_api_key:
            raise ValueError("SEARCH_PROVIDER=tavily requires TAVILY_API_KEY")
        return TavilySearch(cfg, cache)
    if name == "brave":
        if not cfg.brave_api_key:
            raise ValueError("SEARCH_PROVIDER=brave requires BRAVE_API_KEY")
        return BraveSearch(cfg, cache)
    raise ValueError(f"unknown SEARCH_PROVIDER {name!r} (use ddgs, tavily, brave, "
                     "a comma-separated list, or all)")


def make_search(cfg, cache: DiskCache, mock=None):
    """SEARCH_PROVIDER is one name, a comma-separated list, or `all` (every keyed
    provider that has a key, plus ddgs). More than one provider -> fused results."""
    if mock is not None:
        return mock
    raw = (cfg.search_provider or "").strip().lower()
    if raw == "all":
        names = [n for n, key in (("tavily", cfg.tavily_api_key), ("brave", cfg.brave_api_key)) if key]
        names.append("ddgs")
    else:
        names = list(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip()))
    if not names:
        raise ValueError("SEARCH_PROVIDER is empty")
    providers = [(n, _make_one(n, cfg, cache)) for n in names]
    if len(providers) == 1:
        return providers[0][1]
    return MultiSearch(providers, deadline=cfg.search_provider_deadline,
                       query_workers=cfg.search_workers)


_DATE_META = ["article:published_time", "datePublished", "date", "publish-date",
              "og:article:published_time", "publication_date"]


def _extract_date(soup: BeautifulSoup) -> str:
    for name in _DATE_META:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"])[:30]
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                for k in ("datePublished", "dateCreated", "uploadDate"):
                    if item.get(k):
                        return str(item[k])[:30]
    t = soup.find("time")
    if t:
        return (t.get("datetime") or t.get_text(strip=True))[:30]
    return ""


def fetch_page(url: str, cfg, cache: DiskCache, session: requests.Session | None = None) -> dict | None:
    """Fetch + extract readable text. Returns None on any failure.
    ONLY successfully fetched pages may be cited in the final report."""
    norm = normalize_url(url)
    cached = cache.get("fetch", norm, max_age_s=30 * 86400)
    if cached is not None:
        return cached or None
    sess = session or requests.Session()
    result = None
    try:
        resp = sess.get(url, headers={"User-Agent": UA}, timeout=cfg.fetch_timeout,
                        allow_redirects=True)
        if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", "text/html"):
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "form"]):
                tag.decompose()
            title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
            date = _extract_date(soup)
            body = soup.find("article") or soup.find("main") or soup.body or soup
            text = re.sub(r"\s+", " ", body.get_text(" ", strip=True))[:8000]
            if len(text) > 200:
                result = {"url": norm, "final_url": resp.url, "title": title,
                          "date": date, "text": text}
    except requests.RequestException:
        result = None
    cache.set("fetch", norm, result or {})
    return result
