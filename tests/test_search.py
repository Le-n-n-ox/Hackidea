"""Multi-provider search: result fusion, failure isolation, provider selection."""
import pytest

from hackidea.cache import DiskCache
from hackidea.config import Config
from hackidea.search import (BraveSearch, DDGSSearch, MockSearch, MultiSearch, SearchHit,
                             TavilySearch, fuse_hits, make_search)


def _hit(url, snippet="s", title="t"):
    return SearchHit(url=url, title=title, snippet=snippet)


def test_fuse_puts_agreed_urls_first():
    a = [_hit("https://a.com/1"), _hit("https://shared.com/x")]
    b = [_hit("https://b.com/1"), _hit("https://shared.com/x")]
    fused = fuse_hits([a, b], max_results=8)
    assert fused[0].url == "https://shared.com/x"      # returned by both engines
    assert len(fused) == 3


def test_fuse_interleaves_single_engine_results_by_rank():
    a = [_hit("https://a.com/1"), _hit("https://a.com/2")]
    b = [_hit("https://b.com/1"), _hit("https://b.com/2")]
    urls = [h.url for h in fuse_hits([a, b], max_results=8)]
    assert urls == ["https://a.com/1", "https://b.com/1", "https://a.com/2", "https://b.com/2"]


def test_fuse_dedups_on_normalized_url_and_keeps_longest_snippet():
    a = [_hit("http://www.Example.com/page/?utm_source=x", snippet="short")]
    b = [_hit("https://example.com/page", snippet="a much longer snippet")]
    fused = fuse_hits([a, b], max_results=8)
    assert len(fused) == 1
    assert fused[0].snippet == "a much longer snippet"


def test_fuse_respects_max_results():
    a = [_hit(f"https://a.com/{i}") for i in range(8)]
    b = [_hit(f"https://b.com/{i}") for i in range(8)]
    assert len(fuse_hits([a, b], max_results=8)) == 8


def test_multisearch_survives_a_failing_provider():
    class Broken:
        def search(self, query, max_results=8):
            raise RuntimeError("429 rate limited")

    good = MockSearch([{"url": "https://ok.com/1", "title": "ok", "snippet": "fine"}])
    multi = MultiSearch([("broken", Broken()), ("good", good)])
    assert [h.url for h in multi.search("anything")] == ["https://ok.com/1"]


def _cfg(tmp_path, **kw):
    return Config(cache_dir=str(tmp_path), **kw), DiskCache(tmp_path)


def test_make_search_single_provider_is_unwrapped(tmp_path):
    cfg, cache = _cfg(tmp_path, search_provider="ddgs")
    assert isinstance(make_search(cfg, cache), DDGSSearch)


def test_make_search_all_uses_only_providers_with_keys(tmp_path):
    cfg, cache = _cfg(tmp_path, search_provider="all", tavily_api_key="k")
    multi = make_search(cfg, cache)
    assert isinstance(multi, MultiSearch)
    assert [name for name, _ in multi.providers] == ["tavily", "ddgs"]

    cfg, cache = _cfg(tmp_path, search_provider="all", tavily_api_key="k", brave_api_key="k")
    assert [name for name, _ in make_search(cfg, cache).providers] == ["tavily", "brave", "ddgs"]

    cfg, cache = _cfg(tmp_path, search_provider="all")      # no keys -> plain ddgs
    assert isinstance(make_search(cfg, cache), DDGSSearch)


def test_make_search_explicit_list(tmp_path):
    cfg, cache = _cfg(tmp_path, search_provider="tavily, brave", tavily_api_key="k", brave_api_key="k")
    multi = make_search(cfg, cache)
    assert [type(p) for _, p in multi.providers] == [TavilySearch, BraveSearch]


def test_make_search_named_provider_without_key_fails(tmp_path):
    cfg, cache = _cfg(tmp_path, search_provider="tavily,ddgs")
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        make_search(cfg, cache)


def test_make_search_unknown_provider_fails(tmp_path):
    cfg, cache = _cfg(tmp_path, search_provider="bing")
    with pytest.raises(ValueError, match="unknown SEARCH_PROVIDER"):
        make_search(cfg, cache)
