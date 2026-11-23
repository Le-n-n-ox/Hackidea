"""End-to-end pipeline invariants with mocked LLM/search/fetch."""
import json
import re
import threading
import time

import pytest

from hackidea.config import Config
from hackidea.blueprint_document import code_files, section_defaults, team_aliases, event_date_from_context
from hackidea.llm import MockLLM
from hackidea.prompts import ANGLE_LIST
from hackidea.runner import Runner
from hackidea.search import MockSearch

WORDS = ["milk", "school", "clinic", "bus", "farm", "shop", "water", "solar", "fish", "rent", "loan", "crop"]
PROFILES = [
    ("dairy collection clerks", "milk volumes are lost between farm collection and payout", "voice-note entity extraction reconciles liters and farmer ids"),
    ("school bursars", "fee balances diverge across mobile money receipts and student ledgers", "receipt OCR matches transfers against student invoices"),
    ("clinic procurement nurses", "medicine expiry goes unnoticed in handwritten stock cards", "vision extraction predicts expiry risk from shelf photos and invoices"),
    ("matatu route coordinators", "drivers miss route disruptions reported in fragmented voice notes", "multilingual audio clustering maps reports to routes and severity"),
    ("smallholder agronomists", "crop disease outbreaks are spotted too late across scattered plots", "offline image classifier groups field photos into outbreak clusters"),
    ("market stall owners", "supplier invoices do not match daily cash and mobile money sales", "table parser reconciles invoice lines with transaction exports"),
    ("community water operators", "leaks persist because pump readings are handwritten and delayed", "sensor anomaly detector predicts leaks from pressure time series"),
    ("solar mini-grid technicians", "battery faults recur without a searchable service history", "edge model classifies inverter sounds and retrieves matching repairs"),
    ("fish cooperative cold-chain managers", "temperature excursions spoil catch before buyers are notified", "sensor fusion detects spoilage windows and prioritizes alerts"),
    ("rental housing mediators", "tenants cannot track repair commitments across SMS and photos", "document timeline extractor links evidence to repair obligations"),
    ("microfinance field officers", "loan repayment disputes lack a clear ledger across agents", "entity resolution reconciles receipt photos and repayment records"),
    ("crop insurance assessors", "claim inspections are slow because field evidence is unstructured", "vision model ranks damage photos against policy-specific evidence"),
]


def _ids(text):
    return re.findall(r"\[(c\d+)\]", text)


def _candidate(text):
    stage = re.findall(r"\[STAGE:(\w+)\]", text)[-1]
    slot = int(re.search(r"ideation_(\d+)", stage).group(1))
    word = WORDS[(slot - 1) % len(WORDS)]
    target, problem, core = PROFILES[(slot - 1) % len(PROFILES)]
    angle = re.search(r"Required angle: (.+)", text).group(1).split("\n", 1)[0]
    angle_cues = {
        "agentic": "an autonomous agent executes the workflow",
        "multimodal": "a multimodal vision and voice model processes photos and audio",
        "RAG": "retrieval augmented generation searches a niche knowledge base",
        "on-device": "an offline on-device edge model runs with low resources",
        "hardware": "IoT sensors supply live device measurements",
        "accessibility": "an accessible low-literacy interface supports disability needs",
        "local": "a local low-bandwidth Kenya workflow supports mobile money users",
        "developer": "a developer tool tests code and debugs API integrations",
    }
    cue = next(v for k, v in angle_cues.items() if k.casefold() in angle.casefold())
    url = re.search(r"URL: (https?://\S+)", text).group(1)
    return {"candidates": [{"name": f"{word.title()}Ledger",
        "pitch": f"An AI workflow for {target} that addresses {problem}",
        "problem": problem,
        "target_user": target,
        "ai_core": f"{core}; {cue}",
        "why_now": f"Fetched evidence identifies the {word} workflow gap ({url})",
        "angle": angle, "evidence_url": url,
        "data_needed": f"sample {word} records", "sponsor_fit": "OpenAI credits"}]}


def _score(text):
    ids = list(dict.fromkeys(_ids(text)))
    return {"scores": [{"id": identity,
        **{d: (9.0 if identity == "c01" else 6.0) for d in
           ("theme_fit", "ai_depth", "feasibility", "novelty", "demo_impact", "real_world_impact")},
        "rationale": "Relative mock comparison grounded in the supplied evidence."} for identity in ids]}


def _filter(text):
    identity = _ids(text)[0]
    number = int(identity[1:])
    return {"decisions": [{"id": identity, "keep": number <= 6,
        "reason": "Specific user and task; mock classification.",
        "category": "ok" if number <= 6 else "wrapper",
        "needs_saturation_check": number == 1}]}


def _redteam(text):
    identity = _ids(text)[0]
    return {"reviews": [{"id": identity, "risks": [
        {"category": cat, "risk": f"mock {cat} risk", "severity": "medium",
         "mitigation": "mock mitigation"} for cat in
        ("technical", "api_rate_limit", "data", "demo_failure", "already_exists")],
        "strongest_objection": "Mock objection; judge may see similar workflow."}]}


def _tournament(text):
    pairs = re.findall(r"MATCH \d+: A=\[(c\d+)\][\s\S]*?B=\[(c\d+)\]", text)
    return {"matches": [{"a": a, "b": b, "winner": a,
                         "reasoning": "Mock head-to-head comparison; A better fits the judging rubric."}
                        for a, b in pairs]}


def _takeaways(text):
    urls = re.findall(r'"url":\s*"([^\"]+)"', text)
    return {"takeaways": [{"url": u, "takeaway": "Fetched page identifies a concrete workflow or product fact.",
                           "date_hint": "2026-05"} for u in urls]}


def _search_hits(query):
    i = len(getattr(_search_hits, "_calls", []))
    _search_hits._calls = getattr(_search_hits, "_calls", []) + [query]
    return [{"url": f"https://example.com/s{i * 2}", "title": f"Source {i}a", "snippet": "mock snippet"},
            {"url": f"https://example.com/s{i * 2 + 1}", "title": f"Source {i}b", "snippet": "mock snippet"}]


_BP_WINNER = {"name": "MilkLedger", "pitch": "An AI workflow for cooperative treasurers that reconciles payment records.",
              "problem": "Cooperative treasurers struggle to reconcile member payments.",
              "target_user": "cooperative treasurers", "ai_core": "structured extraction", "why_now": "test",
              "angle": "agentic", "data_needed": "synthetic ledger rows", "sponsor_fit": "SMS"}
_BP_CTX = {"duration_hours": 48, "team": [{"role": "frontend", "skills": ["react"]},
            {"role": "backend", "skills": ["python"]}, {"role": "ML", "skills": ["prompting"]}],
           "sponsors": ["OpenAI", "Twilio"]}
BP = {f"blueprint_{section}":
      (lambda _text, section=section: section_defaults(
          section, _BP_CTX, _BP_WINNER, team_aliases(_BP_CTX), "2026-11-26"))
      for section in ("idea", "summary", "frontend", "backend", "ai", "data", "timeline", "demo", "risks", "appendix")}


@pytest.fixture()
def scripts():
    s = {
        "context": {"theme": "AI for financial inclusion in Africa", "duration_hours": 48,
                    "team": [{"role": "frontend", "skills": ["react"]},
                             {"role": "backend", "skills": ["python"]},
                             {"role": "ML", "skills": ["prompting"]}],
                    "sponsors": ["OpenAI", "Twilio"], "sponsor_apis": ["OpenAI API", "Twilio SMS"],
                    "judging_criteria": {"impact": 30, "innovation": 30,
                                         "technical execution": 25, "presentation": 15},
                    "audience_region": "Nairobi, Kenya"},
        "research_plan": {"queries": [{"query": f"mock query {i} financial inclusion",
            "source_type": t, "rationale": "mock"} for i, t in enumerate(
            ["past_winners", "sponsor_apis", "trending_repos", "models_papers", "news_launches",
             "user_problems", "market_products", "local_context", "past_winners", "sponsor_apis",
             "user_problems", "market_products"])]},
        "takeaways": _takeaways,
        "filter_recheck": _filter,
        "scoring": _score,
        "scoring_stability": _score,
        "decision": {"winner_id": "c01", "confidence_level": "medium", "confidence_pct": 72,
            "key_assumption": "Consented sample records are available for the demo.",
            "why_beats_others": [{"point": "mock"}, {"point": "mock"}, {"point": "mock"}],
            "rationale": "Mock decision explanation."},
        **BP,
    }
    n = Config(depth="quick").preset["candidates"]
    for i in range(1, n + 1):
        s[f"ideation_{i:02d}_try1"] = _candidate
        for attempt in (2, 3):
            s[f"ideation_{i:02d}_try{attempt}"] = _candidate
        identity = f"c{i:02d}"
        s[f"filter_{identity}"] = _filter
        s[f"filter_recheck_{identity}"] = _filter
        s[f"redteam_{identity}"] = _redteam
    for i in range(1, 20):
        for pos in (1, 2):
            s[f"tournament_{i:02d}_{pos}"] = _tournament
    return s


def test_end_to_end(tmp_path, scripts):
    _search_hits._calls = []
    cfg = Config(depth="quick", runs_dir=str(tmp_path / "runs"), cache_dir=str(tmp_path / "cache"))
    llm = MockLLM(scripts)
    search = MockSearch(_search_hits)

    active_fetches = 0
    peak_fetches = 0
    fetch_lock = threading.Lock()

    def fetch(url):
        nonlocal active_fetches, peak_fetches
        with fetch_lock:
            active_fetches += 1
            peak_fetches = max(peak_fetches, active_fetches)
        try:
            time.sleep(0.02)
            return {"url": url, "final_url": url, "title": "Fetched " + url,
                    "date": "2026-05-01", "text": ("mock page about financial inclusion, financial services, "
                    "mobile money and the needs of people excluded from banking. ") * 40}
        finally:
            with fetch_lock:
                active_fetches -= 1

    runner = Runner(cfg, llm=llm, search=search, fetch=fetch)
    run_dir = runner.run(context_text="Theme: AI for hospitality and rural tourism. 48 hours, team of 3. Sponsor: Africa's Talking.")
    assert peak_fetches > 1

    for name in ("context.json", "research_plan.json", "search_results.json", "research_log.json",
                 "candidates.json", "filtering.json", "weights.json", "scoring_table.json",
                 "redteam.json", "tournament.json", "stability.json", "decision.json",
                 "blueprint.json", "final_report.md"):
        assert (run_dir / name).exists(), f"missing {name}"

    candidates = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    assert len({c["name"].casefold() for c in candidates}) == len(candidates)
    assert {c["angle"] for c in candidates} == set(ANGLE_LIST)
    filtering = json.loads((run_dir / "filtering.json").read_text(encoding="utf-8"))
    assert len(filtering["decisions"]) == len(candidates)
    assert len(filtering["saturation_checks"]) > 0

    scoring = json.loads((run_dir / "scoring_table.json").read_text(encoding="utf-8"))
    assert scoring["rows"][0]["id"] == "c01"
    assert abs(sum(scoring["weights"].values()) - 1.0) < 1e-6
    assert scoring["weights"]["real_world_impact"] > 0.1

    t = json.loads((run_dir / "tournament.json").read_text(encoding="utf-8"))
    expected = len(t["pair_results"])
    assert len(t["matches"]) == expected * 2
    assert len({(m["pair_id"], m["position"]) for m in t["matches"]}) == expected * 2
    assert all(m["winner"] in (m["a"], m["b"]) for m in t["matches"])

    stability = json.loads((run_dir / "stability.json").read_text(encoding="utf-8"))
    assert stability["final_order"][0] == "c01"
    assert all(0 <= v <= 10 for v in stability["rescored_totals"].values())
    decision = json.loads((run_dir / "decision.json").read_text(encoding="utf-8"))
    assert decision["winner_id"] == stability["final_order"][0]

    report = (run_dir / "final_report.md").read_text(encoding="utf-8")
    assert report.startswith("# ")
    assert report.count("## Decide in five minutes") == 1
    assert report.count("### 1. The idea:") == 1
    assert "**In plain language:**" in report
    assert "**Example input:**" in report
    assert "**What the AI does:**" in report
    assert "prototype that reviews synthetic examples related to this idea" not in report.lower()
    assert "MilkLedger" in report
    assert "another candidate" not in report
    assert "## Sources" in report
    assert "Check before you rely on this plan" in report
    assert "selection score" not in report.lower()
    assert "pair wins" not in report.lower()
    assert "\\rightarrow" not in report
    assert "```mermaid" not in report
    assert "gemini-3.5-flash-lite" in report
    assert "AT_API_KEY" in report
    assert report.count("### Check before you rely on this plan") == 1
    assert "Africa's Talking SMS sandbox" in report
    assert "gemini-3.5-flash-lite" in report
    appendix = report.partition("## Appendix:")[2].lower()
    main_report = report.partition("## Appendix:")[0].lower()
    assert "selection score" not in main_report
    assert "pair wins" not in main_report
    assert "selection score" not in appendix
    blueprint_sources = json.loads((run_dir / "blueprint_research.json").read_text(encoding="utf-8"))["sources"]
    cited_report = report.partition("## Sources")[0]
    assert all(source["url"] in cited_report for source in blueprint_sources)
    content = json.loads((run_dir / "blueprint_content.json").read_text(encoding="utf-8"))
    aliases = set(content["team_aliases"])
    assert all(row["owner"] in aliases for row in content["timeline"]["calendar"])
    assert {row["owner"] for row in content["team_workstreams"]} == aliases
    fixtures = content["data"]["fixtures"]
    assert len({row["tour_id"] for row in fixtures}) == len(fixtures)
    compile(code_files(content)["main.py"], "generated main.py", "exec")
    assert (run_dir / "final_report.pdf").exists()
    pdf = (run_dir / "final_report.pdf").read_bytes()
    assert pdf.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) >= 3
    assert (run_dir / "final_report.html").exists()
    html_report = (run_dir / "final_report.html").read_text(encoding="utf-8")
    assert html_report.count("Automate rural tour bookings via SMS/USSD") <= 1
    assert (run_dir / "architecture.svg").exists()
    assert (run_dir / "blueprint_content.json").exists()
    assert (run_dir / "blueprint_research.json").exists()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert len(state["stage_runs"]) == 8
    assert all(row["status"] == "completed" for row in state["stage_runs"])
    assert all(row["seconds"] >= 0 and row["llm_requests"] >= 0 for row in state["stage_runs"])
    assert all(row["llm_call_seconds"] >= 0 and row["llm_rate_limits"] >= 0
               and row["search_deadline_cutoffs"] >= 0 for row in state["stage_runs"])
    assert "### 1. The idea:" in report
    assert "### 2. Stack recommendations" in report
    assert "### 10. Risks" in report
    content = json.loads((run_dir / "blueprint_content.json").read_text(encoding="utf-8"))
    assert content["timeline"]["calendar"][0]["start"] == 0
    assert content["timeline"]["calendar"][-1]["end"] == 48
    assert len({row["owner"] for row in content["timeline"]["calendar"]}) <= 3
