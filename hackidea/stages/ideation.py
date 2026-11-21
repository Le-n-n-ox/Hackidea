"""Stage 2: evidence-led, angle-balanced ideation with Python-enforced uniqueness."""
from __future__ import annotations

import re

from hackidea import prompts, schemas

_STOP = {"the", "and", "for", "with", "from", "that", "this", "into", "using", "via",
         "ai", "agent", "agents", "voice", "chat", "powered", "based", "real", "time",
         "platform", "app", "tool", "system", "helps", "help", "users", "user"}
SIMILARITY_THRESHOLD = 0.27


def _tokens(text: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        # Normalize regular plurals so "PIN prompts" and "PIN prompt" overlap.
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if len(token) > 2 and token not in _STOP:
            tokens.add(token)
    return tokens


def idea_similarity(a: dict, b: dict) -> float:
    """Compare product/problem identity, not just a shared AI implementation word."""
    if a.get("name", "").strip().casefold() == b.get("name", "").strip().casefold():
        return 1.0

    def jac(x: str, y: str) -> float:
        tx, ty = _tokens(x), _tokens(y)
        return len(tx & ty) / len(tx | ty) if tx and ty else 0.0

    identity_a = " ".join(a.get(k, "") for k in ("problem", "target_user", "pitch"))
    identity_b = " ".join(b.get(k, "") for k in ("problem", "target_user", "pitch"))
    core_a, core_b = a.get("ai_core", ""), b.get("ai_core", "")
    user_a = " ".join((a.get("problem", ""), a.get("target_user", "")))
    user_b = " ".join((b.get("problem", ""), b.get("target_user", "")))
    overall = 0.45 * jac(identity_a, identity_b) + 0.30 * jac(core_a, core_b) + 0.25 * jac(user_a, user_b)
    pitch_overlap = jac(a.get("pitch", ""), b.get("pitch", ""))
    return max(overall, 0.8 + 0.2 * pitch_overlap if pitch_overlap >= 0.8 else 0.0)


def dedup_ideas(candidates: list[dict], threshold: float = SIMILARITY_THRESHOLD) -> list[dict]:
    out: list[dict] = []
    for candidate in candidates:
        if any(idea_similarity(candidate, old) >= threshold for old in out):
            continue
        out.append(candidate)
    return out


def run(runner) -> dict:
    ctx, n = runner.ctx, runner.preset["candidates"]
    sources = runner.load("research_log.json")["sources"]
    evidence = [s for s in sources if s.get("fetched") and s.get("source_type") in
                ("user_problems", "market_products", "local_context", "news_launches")]
    if not evidence:
        evidence = [s for s in sources if s.get("fetched")]
    if not evidence:
        raise RuntimeError("ideation needs at least one fetched evidence page")

    angles = prompts.ANGLE_LIST
    counts = [n // len(angles) + (1 if i < n % len(angles) else 0)
              for i in range(len(angles))]
    candidates: list[dict] = []
    rejected: list[dict] = []
    slot = 0
    for angle, count in zip(angles, counts):
        for _ in range(count):
            slot += 1
            accepted = False
            for attempt in range(1, 4):
                # A duplicate retry changes both the source seed and sampling seed,
                # so the model has a concrete path out of its previous answer.
                seed = evidence[(slot + attempt - 2) % len(evidence)]
                system, user = prompts.ideation_one(ctx, angle, seed,
                                                     candidates[-8:] + rejected[-6:])
                if attempt > 1 and rejected:
                    last = rejected[-1]
                    user += (f"\nYour previous candidate was rejected as a near-duplicate: "
                             f"{last.get('name')} — {last.get('problem')}. Change the user, task, and AI mechanism.")
                stage = f"ideation_{slot:02d}_try{attempt}"
                try:
                    data = runner.llm.complete_json(system, user, schemas.CANDIDATES_SCHEMA,
                                                    stage=stage, max_tokens=1800, tries=1,
                                                    temperature=0.9 if attempt > 1 else 0.8,
                                                    seed=slot * 10 + attempt)
                except Exception as exc:
                    runner.log(f"  malformed candidate for {angle!r}; retry {attempt}/3")
                    rejected.append({"name": "malformed response",
                                     "problem": "Previous response did not match the candidate JSON schema",
                                     "target_user": "", "ai_core": ""})
                    continue
                candidate = dict(data["candidates"][0])
                candidate["id"] = f"c{len(candidates)+1:02d}"
                candidate["model_angle"] = candidate.get("angle", "")
                if not _angle_matches(angle, candidate):
                    runner.log(f"  candidate ignored assigned angle {angle!r}; retry {attempt}/3")
                    rejected.append(candidate)
                    continue
                candidate["angle"] = angle
                # The source identity is assigned from the fetched research record.
                candidate["evidence_url"] = seed["url"]
                candidate["evidence_title"] = seed.get("title", "")
                if any(idea_similarity(candidate, old) >= SIMILARITY_THRESHOLD for old in candidates):
                    runner.log(f"  duplicate result for angle {angle!r}; retry {attempt}/3")
                    rejected.append(candidate)
                    continue
                candidates.append(candidate)
                accepted = True
                break
            if not accepted:
                runner.log(f"  no distinct candidate found for angle {angle!r}")

    runner.save("candidates.json", {"candidates": candidates})
    runner.log(f"ideation: {len(candidates)} distinct candidates across angles "
               f"{sorted({c.get('angle', '?') for c in candidates})}")
    return {"candidates": candidates}


def _angle_matches(required: str, candidate: dict) -> bool:
    """Require angle cues in the idea itself, not just a model-returned label."""
    a = required.casefold()
    content = " ".join(str(candidate.get(k, "")) for k in
                        ("name", "pitch", "problem", "target_user", "ai_core", "why_now")).casefold()
    groups = [
        (("agentic workflows",), ("agent", "tool call", "autonomous", "execute", "workflow")),
        (("multimodal",), ("multimodal", "vision", "voice", "audio", "image", "photo", "document")),
        (("rag over",), ("rag", "retriev", "knowledge base", "search over")),
        (("on-device",), ("on-device", "offline", "edge model", "low-resource")),
        (("hardware / iot",), ("hardware", "iot", "sensor", "device")),
        (("accessibility",), ("accessibility", "accessible", "disability", "low-literacy", "screen reader")),
        (("local / emerging-market",), ("emerging", "local", "africa", "kenya", "nairobi", "rural",
                                          "informal", "ussd", "low-bandwidth", "mobile money")),
        (("developer tools",), ("developer", "debug", "code", "test", "repository", "repo")),
    ]
    for prefixes, cues in groups:
        if any(prefix in a for prefix in prefixes):
            return any(cue in content for cue in cues)
    return False
