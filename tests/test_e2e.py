"""End-to-end pipeline test with mocked LLM + search + fetch (no network)."""
import json
import re

import pytest

from hackidea.config import Config
from hackidea.llm import MockLLM
from hackidea.runner import Runner
from hackidea.search import MockSearch

WORDS = ["milk", "school", "clinic", "bus", "farm", "shop",
         "water", "solar", "fish", "rent", "loan", "crop"]
ANGLES = ["agentic workflows", "multimodal", "RAG over niche data", "on-device AI",
          "AI + hardware", "accessibility", "emerging-market problems", "developer tools"]


def _candidates():
    out = []
    for i, w in enumerate(WORDS):
        out.append({"name": f"{w.capitalize()}Flow",
                    "pitch": f"{w} coordination for smallholders via voice notes and agents",
                    "target_user": f"{w} cooperative members",
                    "ai_core": f"tool-calling agent parsing {w} voice notes into structured records",
                    "why_now": "cheap speech models released this year (https://example.com/s0)",
                    "angle": ANGLES[i % len(ANGLES)],
                    "data_needed": f"sample {w} records", "sponsor_fit": "OpenAI credits"})
    return out


def _ids(text):
    return re.findall(r"\[(c\d+)\]", text)


def _scores(text):
    return {"scores": [{"id": i, **{d: (9.0 if i == "c01" else 6.0) for d in
                                    ("theme_fit", "ai_depth", "feasibility", "novelty",
                                     "demo_impact", "real_world_impact")},
                        "rationale": "mock rationale"} for i in _ids(text)]}


def _redteam(text):
    return {"reviews": [{"id": i, "risks": [
        {"category": cat, "risk": f"mock {cat} risk", "severity": "medium",
         "mitigation": "mock mitigation"} for cat in
        ("technical", "api_rate_limit", "data", "demo_failure", "already_exists")],
        "strongest_objection": "a judge could say this already exists because mock"}
        for i in _ids(text)]}


def _tournament(text):
    pairs = re.findall(r"MATCH \d+: A=\[(c\d+)\][\s\S]*?B=\[(c\d+)\]", text)
    return {"matches": [{"a": a, "b": b, "winner": a,
                         "reasoning": "mock head-to-head reasoning long enough"} for a, b in pairs]}


def _takeaways(text):
    urls = re.findall(r'"url":\s*"([^"]+)"', text)
    return {"takeaways": [{"url": u, "takeaway": "mock takeaway fact", "date_hint": "2026-05"}
                          for u in urls]}


def _recheck(text):
    return {"decisions": [{"id": i, "keep": True, "reason": "no close competitor found",
                           "category": "ok"} for i in _ids(text)]}


def _search_hits(query):
    i = len(getattr(_search_hits, "_calls", []))
    _search_hits._calls = getattr(_search_hits, "_calls", []) + [query]
    return [{"url": f"https://example.com/s{(i * 2) % 6}", "title": f"Source {i}a", "snippet": "mock snippet"},
            {"url": f"https://example.com/s{(i * 2 + 1) % 6}", "title": f"Source {i}b", "snippet": "mock snippet"}]


BP = {
    "blueprint_a": ("## 1. Idea\nMilkFlow: voice-first AI for dairy cooperatives. Problem: members log "
                    "deliveries by voice; records are lost. Target user: cooperative clerks. Why now: cheap "
                    "speech models. AI core: agent transcribes and reconciles deliveries.\n\n"
                    "## 3. Tech stack\n- Frontend: React (fast). Backend: FastAPI (team skill). "
                    "DB: SQLite (zero setup). AI primary: gpt-4o-mini (OpenAI credits); fallback: "
                    "whisper-tiny local. Hosting: Render free. Cost: $0.\n\n"
                    "## 4. Architecture\n```mermaid\nflowchart LR\nA[Phone] --> B[API] --> C[Agent]\n```\n"
                    "1. Upload voice. 2. Transcribe. 3. Reconcile.\n\n"
                    "## 8. Data plan\nSynthetic delivery logs generated with a seed script; real data via "
                    "cooperative consent. Fallback: 200 sample rows.\n"),
    "blueprint_b": ("## 5. Frontend guide\nPages: Upload, Ledger, Demo. State: zustand. Polish: dark theme, "
                    "skeleton loaders, big numbers, confetti on reconcile, mobile-first layout.\n\n"
                    "## 6. Backend guide\nStructure: app/main.py, app/agent.py. POST /api/voice "
                    "{audio_url} -> {delivery_id, liters, member}. GET /api/ledger -> {rows[]}. "
                    "Model: Delivery(id, member, liters, ts). Errors: retry queue + dead letter.\n\n"
                    "## 7. AI integration guide\nPrompt: 'Extract member, liters, date from this transcript. "
                    "Return JSON.' Tools: reconcile_delivery. Eval: 20 golden transcripts, >=90% field "
                    "accuracy. Fallback: cached responses for the demo script.\n"),
    "blueprint_c": ("## 9. Hour-by-hour timeline\n| 0-4 | scaffold | backend | repo runs |\n"
                    "| 4-12 | voice pipeline | ML | transcription works |\n| 12-24 | ledger UI | frontend | "
                    "demo flow |\n| 24-36 | integration | all | end-to-end |\n| 36-44 | polish | all | wow moment |\n"
                    "| 44-48 | pitch rehearsal | all | timed 3 min |\nCUT LINE at hour 29: drop auth and "
                    "multi-coop support.\n\n## 10. Task split\nFrontend dev: UI. Backend dev: API+DB. "
                    "ML engineer: prompts+eval. Demo owner: ML engineer.\n\n"
                    "## 11. Setup commands\n```\ngit init milkflow && cd milkflow\npip install fastapi uvicorn\n"
                    "uvicorn app.main:app --reload\n```\n.env.example:\nOPENAI_API_KEY=sk-...\nTWILIO_SID=...\n"),
    "blueprint_d": ("## 12. Demo and pitch\n0:00-0:30 problem story. 0:30-1:30 live voice upload -> ledger "
                    "updates (wow). 1:30-2:30 impact numbers. 2:30-3:00 ask. Judge Q: 'Why not SMS?' "
                    "A: voice works for low-literacy members. Sponsor emphasis: OpenAI credits power the "
                    "agent; Twilio sends SMS receipts.\n\n## 13. Risks and mitigations\n- Wifi fails: cached "
                    "demo mode (owner: backend).\n- Transcription errors: confirmation step (owner: ML).\n\n"
                    "### Stretch goals\n1. Swahili support. 2. USSD fallback. 3. Coop dashboard analytics.\n"),
}


@pytest.fixture()
def scripts():
    s = {
        "context": {"theme": "AI for financial inclusion in Africa", "duration_hours": 48,
                    "team": [{"role": "frontend", "skills": ["react"]},
                             {"role": "backend", "skills": ["python"]},
                             {"role": "ML", "skills": ["prompting"]}],
                    "sponsors": ["OpenAI", "Twilio"], "sponsor_apis": ["OpenAI API", "Twilio SMS"],
                    "judging_criteria": {"impact": 30, "innovation": 30,
                                         "technical_execution": 25, "presentation": 15},
                    "audience_region": "Nairobi, Kenya"},
        "research_plan": {"queries": [{"query": f"mock query {i} financial inclusion",
                                       "source_type": t, "rationale": "mock"}
                                      for i, t in enumerate(
                                          ["past_winners", "sponsor_apis", "trending_repos", "models_papers",
                                           "news_launches", "user_problems", "market_products", "local_context",
                                           "past_winners", "sponsor_apis", "user_problems", "market_products"])]},
        "takeaways": _takeaways,
        "ideation": {"candidates": _candidates()},
        "filtering": {"decisions": [
            {"id": f"c{i:02d}", "keep": i <= 6, "reason": "mock",
             "category": "ok" if i <= 6 else "wrapper",
             "needs_saturation_check": i == 6} for i in range(1, 13)]},
        "filtering_recheck": _recheck,
        "weights": {"weights": {"theme_fit": .10, "ai_depth": .25, "feasibility": .20,
                                "novelty": .20, "demo_impact": .15, "real_world_impact": .10},
                    "rationale": "mapped to judging criteria"},
        "scoring": _scores,
        "scoring_stability": _scores,
        "redteam": _redteam,
        "tournament": _tournament,
        "decision": {"winner_id": "c01", "confidence_level": "medium", "confidence_pct": 72,
                     "key_assumption": "voice parsing works for low-resource languages",
                     "why_beats_others": [
                         {"point": "unique voice-first workflow", "evidence_url": "https://example.com/s0"},
                         {"point": "no close competitor", "evidence_url": "https://evil.example/fake"},
                         {"point": "strongest demo moment"}],
                     "rationale": "mock decision"},
        **BP,
    }
    return s


def test_end_to_end(tmp_path, scripts):
    _search_hits._calls = []
    cfg = Config(depth="quick", runs_dir=str(tmp_path / "runs"), cache_dir=str(tmp_path / "cache"))
    llm = MockLLM(scripts)
    search = MockSearch(_search_hits)

    def fetch(url):
        return {"url": url, "final_url": url, "title": "Fetched " + url,
                "date": "2026-05-01", "text": "mock page text " * 30}

    runner = Runner(cfg, llm=llm, search=search, fetch=fetch)
    run_dir = runner.run(context_text="Theme: AI for financial inclusion in Africa. 48 hours, team of 3.")

    # every stage artifact exists
    for name in ("context.json", "research_plan.json", "search_results.json", "research_log.json",
                 "candidates.json", "filtering.json", "weights.json", "scoring_table.json",
                 "redteam.json", "tournament.json", "stability.json", "decision.json",
                 "blueprint.json", "final_report.md"):
        assert (run_dir / name).exists(), f"missing {name}"

    log = json.loads((run_dir / "research_log.json").read_text(encoding="utf-8"))
    assert log["stats"]["queries_executed"] >= 12
    assert len(log["stats"]["source_types"]) >= 6
    assert log["stats"]["fetched"] >= 1

    scoring = json.loads((run_dir / "scoring_table.json").read_text(encoding="utf-8"))
    assert scoring["rows"][0]["id"] == "c01"
    assert abs(sum(scoring["weights"].values()) - 1.0) < 1e-6

    stability = json.loads((run_dir / "stability.json").read_text(encoding="utf-8"))
    assert stability["flipped"] is False
    assert stability["final_order"][0] == "c01"

    decision = json.loads((run_dir / "decision.json").read_text(encoding="utf-8"))
    assert decision["winner_id"] == "c01"
    # fabricated URL stripped, real fetched URL kept
    assert "evidence_url" not in decision["why_beats_others"][1]
    assert decision["why_beats_others"][0]["evidence_url"] == "https://example.com/s0"

    report = (run_dir / "final_report.md").read_text(encoding="utf-8")
    assert report.count("## 1. Idea") == 1          # exactly ONE idea
    assert "MilkFlow" in report
    assert "## 14. Sources" in report
    assert "https://example.com/s0" in report
    assert "evil.example" not in report              # never cite unfetched pages
    for n in range(1, 15):
        assert f"## {n}." in report, f"missing section {n}"