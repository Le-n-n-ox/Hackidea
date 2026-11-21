# HackIdea

Finds the **single best hackathon project idea** for your context — through real web research,
diverse ideation, ruthless filtering, weighted scoring, red-teaming and a head-to-head tournament —
then writes the **complete build blueprint** (stack, architecture, guides, timeline, demo script).

AI-central ideas only: the AI is the product's core mechanism, never a bolted-on chatbot.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env   # then edit
```

Requires Python 3.11+.

## Configuration (env vars / `.env`)

| Var | Meaning | Default |
|---|---|---|
| `LLM_PROVIDER` | `openai` = any OpenAI-compatible endpoint | `openai` |
| `LLM_BASE_URL` | API base (`.../v1`) | `https://api.openai.com/v1` |
| `LLM_API_KEY` | API key (any string for local proxies) | — |
| `LLM_MODEL` | exact model id | `gpt-4o-mini` |
| `LLM_TIMEOUT` / `LLM_MAX_RETRIES` / `LLM_MAX_TOKENS` | HTTP behavior | 300 / 3 / 8000 |
| `SEARCH_PROVIDER` | `ddgs` (free, no key), `tavily`, `brave`, a comma-separated list, or `all` (every provider with a key, plus ddgs). Several providers are queried in parallel and their results fused, with URLs that more than one engine returns ranked first | `ddgs` |
| `DDGS_BACKENDS` | backends tried in order until one works | `google,duckduckgo,bing,mojeek` |
| `TAVILY_API_KEY` / `BRAVE_API_KEY` | for keyed search providers | — |
| `DEPTH` | `quick` / `standard` / `deep` | `standard` |
| `RUNS_DIR` / `CACHE_DIR` | artifact + cache locations | `runs` / `.cache` |

**Free LLM option (no API key):** if you run [Hermes Agent](https://hermes-agent.nousresearch.com),
start `hermes proxy start` (OAuth-backed, e.g. Nous Portal) and set:

```
LLM_BASE_URL=http://127.0.0.1:8645/v1
LLM_API_KEY=local-proxy
LLM_MODEL=qwen/qwen4-max:free
```

Any other OpenAI-compatible server works the same way (OpenRouter, Ollama, vLLM, ...).

## Usage

```bash
# full pipeline
hackidea run --context "Theme: AI for financial inclusion in Africa. 48 hours, team of 3
(one frontend dev, one backend dev, one ML/prompt engineer). Sponsors offer OpenAI and Twilio
credits. Judging: impact 30%, innovation 30%, technical execution 25%, presentation 15%."

# depth scales searches/candidates: quick=12/12, standard=26/24, deep=40/30
hackidea run --context-file hackathon.txt --depth deep

# interactive: asks up to 3 clarifying questions when key inputs are missing
hackidea run --interactive

# inspect / re-run a single stage of an existing run
hackidea run --resume runs/20260101_120000 --stage scoring
hackidea run --resume runs/20260101_120000 --stage blueprint
```

The final report is printed to the terminal **and** saved to `runs/<timestamp>/final_report.md`.
Progress logs go to stderr.

## Pipeline (each stage = one inspectable JSON artifact)

| Stage | Artifact | What happens |
|---|---|---|
| 0 context | `context.json` | structured extraction; ≤3 clarifying questions or stated assumptions |
| 1 research | `research_plan.json`, `search_results.json`, `research_log.json` | ≥N searches across ≥6 source types (winners, sponsor APIs, trending repos, models/papers, news, real problems, market, local); pages fetched; every source logged with URL/title/date/takeaway |
| 2 ideation | `candidates.json` | 20–30 diverse AI-central candidates across 8 forced angles |
| 3 filtering | `filtering.json` | discards infeasible / wrapper / saturated / off-theme; suspected saturation verified with extra searches |
| 4 scoring | `weights.json`, `scoring_table.json/.md` | 0–10 on 6 dimensions, reweighted to judging criteria; totals computed in Python |
| 5 adversarial | `redteam.json`, `tournament.json`, `stability.json` | red-team top 5, round-robin pairwise tournament, top-2 re-scored with reworded prompt + different seed; flips investigated |
| 6 decision | `decision.json` | ONE winner, honest confidence + the assumption that would change the pick |
| 7 blueprint | `blueprint_a..d.md`, `final_report.md` | full 14-section build plan |

Guarantees:
- **No fabricated citations** — only pages actually fetched appear in Sources; invalid evidence URLs are stripped.
- Search + fetch results cached on disk (`.cache/`), retries with backoff, rate-limit friendly.
- Structured LLM outputs validated against JSON schemas, retried with error feedback on malformed JSON.
- URLs and ideas deduplicated.

## Tests

```bash
pytest -q
```

Unit tests cover scoring math, weight normalization, JSON extraction/retry, URL normalization,
dedup, tournament counting; one end-to-end test runs the whole pipeline with a mocked LLM/search/fetch.

## Notes & limits

- `ddgs` backends vary by network; the provider falls through the configured list automatically.
- Research quality depends on what search returns; paywalled/JS-heavy pages may fail to fetch and are
  excluded from citations (logged as `fetched: false`).
- The bot asks ≤3 questions only in `--interactive` mode; otherwise it proceeds with logged assumptions.