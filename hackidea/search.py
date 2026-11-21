"""Pluggable search providers + page fetching with cache, retries, URL dedup."""
from __future__ import annotations

import json
import re
import time
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

    def search(self, query: str, max_results: int = 8) -> list[SearchHit]:
        key = json.dumps([query, max_results, self.backends], sort_keys=True)
        cached = self.cache.get("search", key, max_age_s=7 * 86400)
        if cached is not None:
            return [SearchHit(**h) for h in cached]
        from ddgs import DDGS
        hits: list[SearchHit] = []
        errors = []
        for backend in self.backends:
            for attempt in range(2):
                try:
                    rows = DDGS(timeout=self.cfg.search_timeout).text(
                        query, max_results=max_results, backend=backend)
                    hits = [SearchHit(url=r.get("href", ""), title=r.get("title", ""),
                                      snippet=r.get("body", ""))
                            for r in (rows or []) if r.get("href")]
                    if hits:
                        break
                except Exception as e:  # noqa: BLE001 - provider errors are expected
                    errors.append(f"{backend}: {str(e)[:120]}")
                    time.sleep(1.5 * (attempt + 1))
            if hits:
                break
            time.sleep(0.8)  # politeness between backends
        if not hits and errors:
            print(f"    [search] no results for {query!r} ({'; '.join(errors[:2])})")
        self.cache.set("search", key, [asdict(h) for h in hits])
        return hits


class TavilySearch:
    def __init__(self, cfg, cache: DiskCache):
        self.cfg, self.cache = cfg, cache

    def search(self, query: str, max_results: int = 8) -> list[SearchHit]:
        key = json.dumps(["tavily", query, max_results], sort_keys=True)
        cached = self.cache.get("search", key, max_age_s=7 * 86400)
        if cached is not None:
            return [SearchHit(**h) for h in cached]
        resp = requests.post("https://api.tavily.com/search",
                             json={"api_key": self.cfg.tavily_api_key, "query": query,
                                   "max_results": max_results},
                             timeout=self.cfg.search_timeout)
        resp.raise_for_status()
        hits = [SearchHit(url=r["url"], title=r.get("title", ""), snippet=r.get("content", ""))
                for r in resp.json().get("results", [])]
        self.cache.set("search", key, [asdict(h) for h in hits])
        return hits


class BraveSearch:
    def __init__(self, cfg, cache: DiskCache):
        self.cfg, self.cache = cfg, cache

    def search(self, query: str, max_results: int = 8) -> list[SearchHit]:
        key = json.dumps(["brave", query, max_results], sort_keys=True)
        cached = self.cache.get("search", key, max_age_s=7 * 86400)
        if cached is not None:
            return [SearchHit(**h) for h in cached]
        resp = requests.get("https://api.search.brave.com/res/v1/web/search",
                            params={"q": query, "count": max_results},
                            headers={"X-Subscription-Token": self.cfg.brave_api_key,
                                     "Accept": "application/json"},
                            timeout=self.cfg.search_timeout)
        resp.raise_for_status()
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


def make_search(cfg, cache: DiskCache, mock=None):
    if mock is not None:
        return mock
    if cfg.search_provider == "ddgs":
        return DDGSSearch(cfg, cache)
    if cfg.search_provider == "tavily":
        if not cfg.tavily_api_key:
            raise ValueError("SEARCH_PROVIDER=tavily requires TAVILY_API_KEY")
        return TavilySearch(cfg, cache)
    if cfg.search_provider == "brave":
        if not cfg.brave_api_key:
            raise ValueError("SEARCH_PROVIDER=brave requires BRAVE_API_KEY")
        return BraveSearch(cfg, cache)
    raise ValueError(f"unknown SEARCH_PROVIDER {cfg.search_provider!r}")


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