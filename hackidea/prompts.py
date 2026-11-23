"""All prompt templates. Quality bar: experienced hackathon winner, concrete, evidence-bound."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from hackidea.config import SOURCE_TYPES

ANGLE_LIST = [
    "agentic workflows (multi-step AI agents doing real work, not chat)",
    "multimodal (vision / voice / document understanding)",
    "RAG over niche, hard-to-get data",
    "on-device or low-resource AI (offline, low-bandwidth, cheap phones)",
    "AI + hardware / IoT / sensors",
    "AI for accessibility",
    "AI for local / emerging-market problems",
    "developer tools (AI that builds, tests, or debugs)",
]

SOURCE_TYPE_HINTS = {
    "past_winners": "past winners & finalists on Devpost, MLH, ETHGlobal, Kaggle, Hugging Face Spaces for this theme",
    "sponsor_apis": "sponsor documentation, newly launched APIs/SDKs (last 6 months), sponsor prize tracks",
    "trending_repos": "trending GitHub repositories and new open-source tools relevant to the theme",
    "models_papers": "newly released models (Hugging Face trending) and recent arXiv papers that unlock new capabilities",
    "news_launches": "Hacker News / Product Hunt / tech news launches and discussions from the last months",
    "user_problems": "real unsolved problems: forum complaints, news, NGO or government reports tied to the theme",
    "market_products": "existing products/startups in this space (to detect saturation and find gaps)",
    "local_context": "region-specific constraints and opportunities (e.g. Nairobi/Kenya: mobile money, USSD, low bandwidth, regulation)",
}


def ctx_block(ctx: dict) -> str:
    keep = {k: ctx.get(k) for k in ("theme", "hackathon_name", "tracks", "sponsors", "sponsor_apis",
                                    "judging_criteria", "duration_hours", "team", "preferred_tech",
                                    "banned_tech", "budget_constraints", "audience_region", "extra_notes")}
    return json.dumps(keep, ensure_ascii=False, indent=1)


def build_digest(sources: list[dict], limit: int = 40) -> str:
    """Evidence digest: fetched sources first, one line each. URLs are real (fetched or search-returned)."""
    fetched = [s for s in sources if s.get("fetched")]
    rest = [s for s in sources if not s.get("fetched")]
    lines = []
    for s in (fetched + rest)[:limit]:
        date = s.get("date") or "date unknown"
        lines.append(f"- [{s.get('source_type','?')}] {s.get('title','(untitled)')} ({date}) "
                     f"— {s.get('takeaway') or s.get('snippet','')[:160]} | {s['url']}")
    return "\n".join(lines)


def candidate_block(c: dict) -> str:
    scope_line = f"  judged demo scope: {c['demo_scope']}\n" if c.get("demo_scope") else ""
    return (f"[{c['id']}] {c['name']} — {c['pitch']}\n{scope_line}"
            f"  problem: {c.get('problem','')} | evidence: {c.get('evidence_url','')}\n"
            f"  user: {c.get('target_user','')} | AI core: {c.get('ai_core','')}\n"
            f"  why now: {c.get('why_now','')} | angle: {c.get('angle','')} | "
            f"data: {c.get('data_needed','')} | sponsor fit: {c.get('sponsor_fit','')}")


# ---------------- Stage 1: research ----------------

def research_plan(ctx: dict, n_searches: int) -> tuple[str, str]:
    year = datetime.now(timezone.utc).year
    recent_years = f"{year-1}/{year}"
    system = ("You are a hackathon research strategist. You design web-search query plans that "
              "gather concrete evidence, not generic inspiration.")
    types = "\n".join(f"- {t}: {SOURCE_TYPE_HINTS[t]}" for t in SOURCE_TYPES)
    user = f"""Design exactly {n_searches} web search queries for this hackathon context:
{ctx_block(ctx)}

Cover ALL of these source types (at least one query each, spread the rest where evidence is richest):
{types}

Rules:
- Queries must be specific and searchable (names, sites, years). Use site: operators where useful
  (site:devpost.com, site:github.com, site:huggingface.co, site:arxiv.org, site:news.ycombinator.com).
- Prefer the last 6-12 months: include {recent_years} in queries about launches, models, winners.
- Include sponsor-specific queries for every sponsor/API listed, and region-specific queries if a region is given.
- Include queries that surface REAL unsolved problems (complaints, reports), not just products.
Return JSON: {{"queries": [{{"query": "...", "source_type": "<one of the types>", "rationale": "..."}}]}}"""
    return system, user


def takeaways(batch: list[dict]) -> tuple[str, str]:
    system = "You summarize research sources into one-line takeaways for a hackathon idea pipeline. Be factual; never invent details not present in the text."
    items = []
    for s in batch:
        items.append(json.dumps({"url": s["url"], "title": s.get("title", ""),
                                 "snippet": (s.get("snippet") or "")[:300],
                                 "text_head": (s.get("text") or "")[:5000]}, ensure_ascii=False))
    user = ("For each source below write a ONE-LINE takeaway: what concrete fact, capability, problem, "
            "or gap it evidences for building a hackathon project. If a publication date is visible, "
            "include date_hint (YYYY-MM or YYYY), else empty string.\n\nSOURCES:\n" + "\n".join(items) +
            '\n\nReturn JSON: {"takeaways": [{"url": "...", "takeaway": "...", "date_hint": "..."}]}')
    return system, user


# ---------------- Stage 2: ideation ----------------

def ideation(ctx: dict, digest: str, n_candidates: int) -> tuple[str, str]:
    system = ("You are a repeat hackathon winner who generates AI-CENTRAL project ideas: the AI is the "
              "product's core mechanism, never a bolted-on chatbot. You are concrete and evidence-driven.")
    angles = "\n".join(f"- {a}" for a in ANGLE_LIST)
    user = f"""Generate {n_candidates} diverse hackathon project candidates for this context:
{ctx_block(ctx)}

RESEARCH EVIDENCE (real sources found by web search — ground 'why now' in these):
{digest}

Hard requirements:
- Cover EVERY angle below with at least 2 candidates each:
{angles}
- AI must be central: name the actual mechanism (e.g. structured extraction with tool-calling agent,
  fine-tuned small classifier, RAG over X corpus, on-device Whisper-tiny + rules, vision VLM judging photos).
- Feasible for the stated team in the stated duration. Respect banned/preferred tech and budget.
- 'why_now' must reference something that changed recently (new model/API/price/regulation) — cite the
  evidence URL from the digest where possible.
- No generic 'ChatGPT for X' wrappers: each idea needs unique data, workflow, or insight.
- If a region is given, at least 4 candidates must exploit local realities (mobile money, USSD/SMS,
  low bandwidth, local languages, informal economy) where the theme allows.
Return JSON: {{"candidates": [{{"name": "...", "pitch": "one sentence", "target_user": "...",
"ai_core": "specific mechanism + model family", "why_now": "...", "angle": "<angle label>",
"data_needed": "...", "sponsor_fit": "..."}}]}}"""
    return system, user


def ideation_one(ctx: dict, angle: str, evidence: dict, excluded: list[dict]) -> tuple[str, str]:
    system = ("You design one distinct, feasible AI product around a real user's problem. "
              "Do not start from a sponsor API or turn a generic voice/chat agent into a product. "
              "Return only the requested single JSON object.")
    used = "\n".join(f"- {x.get('name')}: {x.get('problem')} / {x.get('target_user')} / {x.get('ai_core')}"
                      for x in excluded[-16:]) or "(none)"
    evidence_text = (evidence.get("text") or evidence.get("takeaway") or evidence.get("snippet") or "")[:1800]
    user = f"""Create ONE hackathon idea for this context:
{ctx_block(ctx)}

Required angle: {angle}
Start from this fetched evidence, especially the named person/user and their problem:
Title: {evidence.get('title','')}
URL: {evidence.get('url','')}
Evidence: {evidence_text}

Make a product with a specific user, a concrete painful task, and an AI mechanism that directly changes
that task. The AI is central. Fit the available time, skills, budget and theme. Use a sponsor API only if
it directly enables the solution. Do not invent capabilities that are absent from the evidence.
Do not repeat these accepted ideas or any product identity tried and rejected as a near-duplicate:
{used}

Use this angle exactly: {angle}
Return one object inside {{"candidates": [...]}} with fields name, pitch, problem, target_user, ai_core,
why_now, angle, data_needed, sponsor_fit. Keep name and product identity distinct from all examples."""
    return system, user


# ---------------- Stage 3: filtering ----------------

def filtering(ctx: dict, candidates: list[dict], digest: str) -> tuple[str, str]:
    system = "You are a ruthless hackathon judge filtering ideas. You discard anything infeasible, generic, saturated, or off-theme."
    user = f"""Filter these candidates for this context:
{ctx_block(ctx)}

CANDIDATES:
{chr(10).join(candidate_block(c) for c in candidates)}

EVIDENCE (existing products / past winners found in research):
{digest}

Discard (keep=false) anything that is:
1. infeasible in {ctx.get('duration_hours')}h with this team's skills (category=infeasible)
2. a generic wrapper — 'ChatGPT for X' with no unique data/workflow/insight (category=wrapper)
3. already saturated — existing products or past winners clearly do this (category=saturated)
4. outside the theme/rules/banned tech (category=offtheme)
Set needs_saturation_check=true when you suspect saturation but the evidence is inconclusive.
Be specific in 'reason' — name the competing product or the missing capability when you can.
Return JSON: {{"decisions": [{{"id": "...", "keep": true, "reason": "...", "category": "ok",
"needs_saturation_check": false}}]}}"""
    return system, user


def filter_one(ctx: dict, candidate: dict, digest: str) -> tuple[str, str]:
    system = "You are a strict hackathon reviewer. Judge only the one supplied candidate and cite concrete reasons."
    user = f"""Evaluate this single idea against the hackathon:
{ctx_block(ctx)}

CANDIDATE:
{candidate_block(candidate)}
Problem: {candidate.get('problem','')}
Evidence URL: {candidate.get('evidence_url','')}

Choose exactly one category:
- offtheme: does not materially serve the theme or rules
- wrapper: generic AI interface without distinctive workflow/data/mechanism
- saturated: a close product or past winner already solves this
- infeasible: cannot make a convincing demo in the stated time/team/budget
- ok: credible and materially relevant
The reason must identify the specific user/task, feasibility constraint or known competitor. Do not restate
the pitch. If a product comparison is uncertain, request a saturation check.
Return {{"decisions":[{{"id":"{candidate['id']}","keep":true,"reason":"...","category":"ok",
"needs_saturation_check":false}}]}}"""
    return system, user


# ---------------- Stage 4: scoring ----------------

def weights(ctx: dict) -> tuple[str, str]:
    system = "You map hackathon judging criteria onto a fixed scoring rubric. Weights must sum to 1.0."
    user = f"""The hackathon judges with these criteria (fractions of total): {json.dumps(ctx.get('judging_criteria') or {})}
Map them onto these six rubric dimensions and return weights summing to 1.0:
theme_fit, ai_depth, feasibility, novelty, demo_impact, real_world_impact.
Guidance: 'impact' -> real_world_impact (+ some theme_fit); 'innovation/creativity' -> novelty + ai_depth;
'technical execution/complexity' -> feasibility + ai_depth; 'presentation/demo' -> demo_impact.
Keep every dimension >= 0.05. Explain the mapping in rationale.
Return JSON: {{"weights": {{"theme_fit": 0.2, "ai_depth": 0.2, "feasibility": 0.2, "novelty": 0.15,
"demo_impact": 0.15, "real_world_impact": 0.1}}, "rationale": "..."}}"""
    return system, user


def scoring(ctx: dict, batch: list[dict], weights: dict, digest: str, reworded: bool = False) -> tuple[str, str]:
    if reworded:
        system = ("You are a skeptical senior technical judge at a hackathon demo day. You have seen "
                  "hundreds of teams. Rank what actually wins: working demos, real AI depth, credible impact.")
        intro = "Re-evaluate these finalists from scratch. Ignore any prior scores you may imagine."
    else:
        system = ("You are an experienced hackathon judge comparing a small set of candidates. "
                  "Use evidence and relative strengths; avoid giving everyone high scores.")
        intro = ("Compare these candidates against one another, then score each on six dimensions. "
                 "Use 5 for average, 7 for good, 9 only for exceptional. A weak candidate may score 2-4. "
                 "Give candidates different scores on dimensions where the evidence distinguishes them.")
    user = f"""{intro}

CONTEXT:
{ctx_block(ctx)}

RUBRIC WEIGHTS (for your awareness; totals are computed externally): {json.dumps(weights)}
Dimensions: theme_fit (theme+sponsor fit), ai_depth (depth/cleverness of AI integration),
feasibility (in {ctx.get('duration_hours')}h with this team), novelty (vs existing products/past winners —
use the evidence), demo_impact (judge wowed in 3 minutes), real_world_impact (credible user + problem).

CANDIDATES:
{chr(10).join(candidate_block(c) for c in batch)}

EVIDENCE:
{digest}

Novelty scores MUST reflect the evidence: if a close competitor exists, novelty <= 4.
Feasibility MUST reflect the actual team skills and time. Give a 1-2 sentence rationale per candidate.
Return JSON: {{"scores": [{{"id": "...", "theme_fit": 0, "ai_depth": 0, "feasibility": 0,
"novelty": 0, "demo_impact": 0, "real_world_impact": 0, "rationale": "..."}}]}}"""
    return system, user


# ---------------- Stage 5: adversarial ----------------

def redteam(ctx: dict, batch: list[dict], evidence: str = "") -> tuple[str, str]:
    system = ("You are a red-team reviewer whose job is to explain why a hackathon project would LOSE. "
              "You are specific and technical, never generic.")
    user = f"""Red-team these finalists for this context:
{ctx_block(ctx)}

FINALISTS:
{chr(10).join(candidate_block(c) for c in batch)}

FETCHED RESEARCH EVIDENCE (only these pages may support external claims):
{evidence}

For each, cover ALL five risk categories: technical, api_rate_limit, data, demo_failure, already_exists.
- technical: what breaks under hackathon conditions?
- api_rate_limit: which APIs throttle/cost money mid-demo?
- data: is the required data actually obtainable legally in time?
- demo_failure: what fails live in front of judges (wifi, latency, edge cases)?
- already_exists: 'a judge could say this already exists because...' — name real products if known.
Each risk: severity low/medium/high + a concrete mitigation. For external limits, prices or existing products,
include evidence_url only when a supplied page directly supports it; otherwise label the claim unverified and
recommend how the team can check it. Avoid invented numeric quotas or product capabilities. Then the strongest objection.
Return JSON: {{"reviews": [{{"id": "...", "risks": [{{"category": "technical", "risk": "...",
"severity": "high", "mitigation": "...", "evidence_url": "..."}}], "strongest_objection": "..."}}]}}"""
    return system, user


def tournament(ctx: dict, pairs: list[tuple[dict, dict]]) -> tuple[str, str]:
    system = ("You are a hackathon judge doing head-to-head comparisons. Pick the idea more likely to WIN "
              "this specific hackathon, with written reasoning. No ties allowed.")
    blocks = []
    for i, (a, b) in enumerate(pairs):
        blocks.append(f"MATCH {i+1}: A={candidate_block(a)}\n---\nB={candidate_block(b)}")
    user = f"""Context (judge with its criteria):
{ctx_block(ctx)}

Compare each pair head-to-head. Weigh: AI depth, feasibility for this team/time, novelty, 3-minute demo
impact, real-world impact, sponsor fit. Write 2-4 sentences of reasoning per match, then pick a winner id.

{chr(10).join(blocks)}

Return JSON: {{"matches": [{{"a": "<id>", "b": "<id>", "winner": "<id>", "reasoning": "..."}}]}}"""
    return system, user


def investigate(ctx: dict, a: dict, b: dict, detail: str) -> tuple[str, str]:
    system = "You are the head judge resolving a ranking instability between two hackathon finalists."
    user = f"""Two scoring passes disagreed about which finalist is stronger.
CONTEXT: {ctx_block(ctx)}
A: {candidate_block(a)}
B: {candidate_block(b)}
DETAIL: {detail}
Investigate: which ranking is better supported by feasibility, evidence, and demo impact? Decide the
stronger idea and explain what drove the instability.
Return JSON: {{"winner_id": "...", "explanation": "..."}}"""
    return system, user


# ---------------- Stage 6: decision ----------------

def decision(ctx: dict, winner: dict, detail: str, sources: list[dict]) -> tuple[str, str]:
    system = ("You explain a selection that has already been determined by recorded tournament, score, and "
              "red-team rules. Do not change the winner or invent evidence.")
    src_lines = "\n".join(f"- {s['url']} — {s.get('takeaway') or s.get('title','')}"
                          for s in sources if s.get("fetched"))[:6000]
    user = f"""The code has selected exactly this winner:
{ctx_block(ctx)}

WINNER:
{candidate_block(winner)}

RECORDED EVALUATION (with scores, red-team findings, tournament results):
{detail}

CITABLE SOURCES (only these URLs may appear as evidence_url — they were actually fetched):
{src_lines}

Explain the winner using only the recorded evaluation. Do not restate unsourced external limits, prices or
product capabilities. If a source genuinely supports a problem statement, use its exact URL. Confidence and
the key assumption should reflect unresolved red-team risks.
Return JSON matching: winner_id (must be {winner['id']}), confidence_level, confidence_pct,
key_assumption, why_beats_others[{{point, evidence_url?}}], rationale."""
    return system, user


# ---------------- Stage 7: blueprint ----------------

BP_SYSTEM = ("You are a senior engineer writing an implementable hackathon prototype blueprint. Be concrete "
             "about app-owned code and demo steps, but never invent external model names, endpoints, prices, "
             "quotas, legal permissions, or product capabilities. If evidence does not establish a detail, "
             "label it 'verify before use' and give a local or simulated fallback. For payment, banking, "
             "identity, or account workflows, use synthetic fixtures and simulated events only: do not collect "
             "PINs, passwords, OTPs, or private account data, and do not approve, block, or initiate real "
             "transactions. "
             "Output GitHub markdown only (no fences around the whole answer).")


def blueprint_config(ctx: dict, winner: dict, digest: str) -> tuple[str, str]:
    system = "Choose one coherent, minimal stack for the winning idea. Do not invent API capabilities or prices."
    user = f"""Choose the project's shared stack and list only concrete facts directly supported by the evidence.
Context: {ctx_block(ctx)}
Winner: {candidate_block(winner)}
Fetched research:
{digest}

Use primary_ai and fallback_ai as provider + model names only when supplied evidence supports the choice;
otherwise mark the choice as a decision to verify and give a mock/local fallback. List only integrations the
prototype can actually use and that are supported by the evidence. Do not imply access to a carrier, bank,
wallet, device notification stream, or transaction approval API unless the supplied evidence proves it.
For each external factual claim, set evidence_url to an exact URL in the supplied fetched research; if no
source supports a fact, omit it. Keep services minimal and compatible with the stated team/time/budget.
Return JSON with frontend, backend, database, primary_ai, fallback_ai, hosting, auth, integrations[],
claims[{{claim,evidence_url}}]."""
    return system, user


def _bp_base(ctx: dict, winner: dict, detail: str, digest: str, build_config: dict) -> str:
    return f"""HACKATHON CONTEXT:
{ctx_block(ctx)}

WINNING IDEA (build exactly this):
{candidate_block(winner)}

JUDGING/SCORE/RED-TEAM DETAIL:
{detail}

SHARED BUILD DECISIONS (use these choices consistently in every section; do not introduce alternatives):
{json.dumps(build_config, ensure_ascii=False, indent=2)}

PROTOTYPE SAFETY BOUNDARY:
Use synthetic data and simulated incoming events for any financial, banking, mobile-money, identity, or
account workflow. The prototype must not capture secrets or interact with real accounts or transactions.
Describe external capabilities, prices, quotas, permissions, and product comparisons as unverified unless a
specific supplied page directly establishes that exact claim. Do not treat a fetched URL alone as proof.

FETCHED RESEARCH EVIDENCE:
{digest}
"""


def blueprint_a(ctx, winner, detail, digest, build_config) -> tuple[str, str]:
    user = _bp_base(ctx, winner, detail, digest, build_config) + """
Write these sections (markdown headers exactly):
## 1. Idea
Name, one-sentence pitch, the problem (2-3 sentences, concrete), target user, the 'why now', and the AI
core (what the AI actually does, mechanically). Expand the winning idea — do not change it.
## 3. Tech stack
Every choice with a one-line reason: frontend, backend, database, AI models, hosting, auth if needed. Use
the shared build decisions. Mark model/provider details and costs for verification when they are not supported
by supplied evidence. Add a cost table that states 'unknown; verify current pricing' instead of inventing
free-tier limits or $0 costs.
## 4. Architecture
A Mermaid flowchart of the system (```mermaid block) + a short data-flow explanation (numbered steps).
## 8. Data plan
Where each dataset comes from, how to get it legally (license/API terms), volume needed, and a concrete
fallback with synthetic/sample data (describe how to generate it)."""
    return BP_SYSTEM, user


def blueprint_b(ctx, winner, detail, digest, build_config) -> tuple[str, str]:
    user = _bp_base(ctx, winner, detail, digest, build_config) + """
Write these sections (markdown headers exactly):
## 5. Frontend guide
Pages/components list, state management choice, UI flow, key libraries, design direction, and 5 concrete
tricks to make the demo look polished fast (specific to this app). Mobile-first if the audience needs it.
## 6. Backend guide
Project structure tree, API endpoints table (method, path, request shape, response shape — real JSON
examples), data models/schema (actual fields+types), background jobs if any, error handling strategy. Use
local prototype endpoints and synthetic fixtures; do not describe unverified external APIs.
## 7. AI integration guide
Model selection rationale (primary+fallback, clearly marking any unverified choice), ACTUAL prompt templates (full text in code
blocks), RAG/agent design if relevant (chunking, retrieval, tool definitions as JSON), evaluation approach
(golden set + metrics), latency/cost handling (streaming, caching, batching), guardrails, and a demo-safe
fallback path if the API fails live (cached responses / local model)."""
    return BP_SYSTEM, user


def blueprint_c(ctx, winner, detail, digest, build_config) -> tuple[str, str]:
    hours = ctx.get("duration_hours") or 48
    team = ctx.get("team") or []
    user = _bp_base(ctx, winner, detail, digest, build_config) + f"""
Write these sections (markdown headers exactly):
## 9. Hour-by-hour timeline
Scaled to {hours} hours. Table: hour range | milestone | owner | done-when. Include sleep/buffer realism for
{hours}h, integration checkpoints, and a clearly marked CUT LINE: what to drop if behind at the 60% mark.
## 10. Task split
Per team member ({json.dumps(team)}): concrete workstreams matching their skills, parallelizable, with
hand-off points and who owns the demo.
## 11. Setup commands
Repo structure tree, exact install/run commands (copy-pasteable), and a complete .env.example in a code
block (placeholder values only)."""
    return BP_SYSTEM, user


def blueprint_d(ctx, winner, detail, digest, build_config) -> tuple[str, str]:
    user = _bp_base(ctx, winner, detail, digest, build_config) + """
Write these sections (markdown headers exactly):
## 12. Demo and pitch
A 3-minute demo script (minute-by-minute, what is shown/said, the 'wow' moment), a pitch outline
(problem->insight->demo->impact->ask), 6 likely judge questions with strong specific answers, and exactly
what to emphasize for each sponsor prize/credit mentioned in the context.
## 13. Risks and mitigations
Top risks (technical, demo-day, judging) each with a concrete mitigation and an owner.
### Stretch goals
3 stretch goals ordered by value/effort, only if time allows."""
    return BP_SYSTEM, user
