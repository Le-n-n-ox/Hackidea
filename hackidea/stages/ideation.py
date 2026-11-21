"""Stage 2: ideation — 20-30 diverse, AI-central candidates grounded in research."""
from __future__ import annotations

import re

from hackidea import prompts, schemas


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def idea_similarity(a: dict, b: dict) -> float:
    ta = _tokens(a.get("name", "") + " " + a.get("pitch", ""))
    tb = _tokens(b.get("name", "") + " " + b.get("pitch", ""))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedup_ideas(candidates: list[dict], threshold: float = 0.75) -> list[dict]:
    out: list[dict] = []
    for c in candidates:
        if any(idea_similarity(c, o) >= threshold for o in out):
            continue
        out.append(c)
    return out


def run(runner) -> dict:
    ctx, preset = runner.ctx, runner.preset
    n = preset["candidates"]
    sources = runner.load("research_log.json")["sources"]
    digest = prompts.build_digest(sources, preset["digest_sources"])

    system, user = prompts.ideation(ctx, digest, n)
    data = runner.llm.complete_json(system, user, schemas.CANDIDATES_SCHEMA,
                                    stage="ideation", max_tokens=7000)
    cands = dedup_ideas(data["candidates"])

    if len(cands) < n:
        missing = n - len(cands)
        system2, user2 = prompts.ideation(ctx, digest, missing)
        user2 += ("\n\nIMPORTANT: these names/pitches already exist — generate DIFFERENT ideas: "
                  + "; ".join(c["name"] for c in cands))
        try:
            more = runner.llm.complete_json(system2, user2, schemas.CANDIDATES_SCHEMA,
                                            stage="ideation", max_tokens=8000)
            cands = dedup_ideas(cands + more["candidates"])
        except Exception as e:  # noqa: BLE001
            runner.log(f"  ideation top-up failed: {e}")

    for i, c in enumerate(cands):
        c["id"] = f"c{i+1:02d}"
    runner.save("candidates.json", {"candidates": cands})
    runner.log(f"ideation: {len(cands)} candidates across angles "
               f"{sorted({c.get('angle','?') for c in cands})}")
    return {"candidates": cands}