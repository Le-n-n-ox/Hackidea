"""Environment-driven configuration. Secrets come from env / .env, never hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Source types the research stage must cover (>= 6 distinct per spec).
SOURCE_TYPES = [
    "past_winners",     # Devpost / MLH / ETHGlobal / Kaggle winners & finalists
    "sponsor_apis",     # sponsor docs, new APIs / SDK launches (last ~6 months)
    "trending_repos",   # GitHub trending / new OSS tools
    "models_papers",    # Hugging Face trending models, recent arXiv
    "news_launches",    # Hacker News, Product Hunt, tech news
    "user_problems",    # forums, complaints, NGO / government reports
    "market_products",  # existing products (saturation checks)
    "local_context",    # regional / emerging-market specifics (e.g. Nairobi, Kenya)
]

DEPTH_PRESETS = {
    "quick":    dict(searches=12, candidates=12, fetch_per_query=1, finalists=3, digest_sources=24),
    "standard": dict(searches=26, candidates=24, fetch_per_query=2, finalists=5, digest_sources=40),
    "deep":     dict(searches=40, candidates=30, fetch_per_query=3, finalists=5, digest_sources=60),
}

DEFAULT_WEIGHTS = {
    "theme_fit": 0.20,
    "ai_depth": 0.20,
    "feasibility": 0.20,
    "novelty": 0.15,
    "demo_impact": 0.15,
    "real_world_impact": 0.10,
}


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Config:
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 300
    llm_max_retries: int = 3
    llm_max_tokens: int = 8000
    search_provider: str = "ddgs"
    ddgs_backends: str = "google,duckduckgo,bing,mojeek"
    search_timeout: int = 30
    tavily_api_key: str = ""
    brave_api_key: str = ""
    fetch_timeout: int = 25
    depth: str = "standard"
    runs_dir: str = "runs"
    cache_dir: str = ".cache"

    @classmethod
    def from_env(cls, depth: str | None = None) -> "Config":
        load_dotenv()
        d = (depth or os.environ.get("DEPTH") or "standard").lower()
        if d not in DEPTH_PRESETS:
            raise ValueError(f"unknown depth {d!r}; choose quick|standard|deep")
        return cls(
            llm_provider=os.environ.get("LLM_PROVIDER", "openai").lower(),
            llm_base_url=os.environ.get("LLM_BASE_URL", cls.llm_base_url).rstrip("/"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=os.environ.get("LLM_MODEL", cls.llm_model),
            llm_timeout=_int("LLM_TIMEOUT", 300),
            llm_max_retries=_int("LLM_MAX_RETRIES", 3),
            llm_max_tokens=_int("LLM_MAX_TOKENS", 8000),
            search_provider=os.environ.get("SEARCH_PROVIDER", "ddgs").lower(),
            ddgs_backends=os.environ.get("DDGS_BACKENDS", cls.ddgs_backends),
            search_timeout=_int("SEARCH_TIMEOUT", 30),
            tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
            brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
            fetch_timeout=_int("FETCH_TIMEOUT", 25),
            depth=d,
            runs_dir=os.environ.get("RUNS_DIR", "runs"),
            cache_dir=os.environ.get("CACHE_DIR", ".cache"),
        )

    @property
    def preset(self) -> dict:
        return DEPTH_PRESETS[self.depth]