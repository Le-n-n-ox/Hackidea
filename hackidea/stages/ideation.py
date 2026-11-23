"""Stage 2: evidence-led, angle-balanced ideation with Python-enforced uniqueness."""
from __future__ import annotations

import re

from hackidea import prompts, schemas
from hackidea.concurrency import ordered_fanout

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
    evidence = [s for s in sources if s.get("relevant") and s.get("fetched") and s.get("source_type") in
                ("user_problems", "market_products", "local_context", "news_launches")]
    # Keep ideation available when a search provider returns no pages from the
    # preferred pain-point categories. Still require a fetched, theme-relevant
    # source; never seed from an irrelevant page or an unfetched result.
    if not evidence:
        evidence = [s for s in sources if s.get("relevant") and s.get("fetched")]
    if not evidence:
        raise RuntimeError("ideation needs at least one fetched, theme-relevant evidence page")
    problem_rows = runner.load("problems.json").get("problems", []) if (runner.run_dir / "problems.json").exists() else []
    source_by_url = {s["url"]: s for s in sources}
    problem_seeds = [(p, source_by_url[p["evidence_url"]]) for p in problem_rows
                     if p.get("evidence_url") in source_by_url and source_by_url[p["evidence_url"]].get("relevant")]

    angles = prompts.ANGLE_LIST
    counts = [n // len(angles) + (1 if i < n % len(angles) else 0)
              for i in range(len(angles))]
    slot_angles = {}
    slot = 0
    for angle, count in zip(angles, counts):
        for _ in range(count):
            slot += 1
            slot_angles[slot] = angle
    progress_path = runner.run_dir / "ideation_progress.json"
    if progress_path.exists():
        progress = runner.load("ideation_progress.json")
        candidates = progress.get("candidates", [])
        completed_slots = set(progress.get("completed_slots", []))
        slot_attempts = {int(k): int(v) for k, v in progress.get("slot_attempts", {}).items()}
        pending_slots = list(progress.get("pending_slots", [s for s in slot_angles if s not in completed_slots]))
        rejected = progress.get("rejected", [])
        runner.log(f"ideation: resuming with {len(candidates)} candidates; {len(completed_slots)} slots complete")
    else:
        candidates, completed_slots, slot_attempts, rejected = [], set(), {}, []
        pending_slots = list(slot_angles)

    def persist_progress():
        runner.save("ideation_progress.json", {
            "candidates": candidates,
            "completed_slots": sorted(completed_slots),
            "slot_attempts": {str(k): v for k, v in sorted(slot_attempts.items())},
            "pending_slots": list(pending_slots),
            "rejected": rejected[-24:],
        })

    while pending_slots:
        round_slots = list(pending_slots)
        accepted_before = candidates[-8:].copy()
        rejected_before = rejected[-6:].copy()
        round_rows = [(s, slot_attempts.get(s, 0) + 1, accepted_before, rejected_before)
                      for s in round_slots]

        def generate(row):
            slot_id, attempt, accepted_snapshot, rejected_snapshot = row
            angle = slot_angles[slot_id]
            if problem_seeds:
                problem, seed = problem_seeds[(slot_id + attempt - 2) % len(problem_seeds)]
            else:
                problem, seed = None, evidence[(slot_id + attempt - 2) % len(evidence)]
            system, user = prompts.ideation_one(ctx, angle, seed, accepted_snapshot + rejected_snapshot)
            if problem:
                user += (f"\n\nDistinct problem seed (avoid reusing the same user/task as another idea): "
                         f"{problem['who']} needs to {problem['task']}. Source: {problem['evidence_url']}")
            if attempt > 1 and rejected_snapshot:
                last = rejected_snapshot[-1]
                user += (f"\nYour previous candidate was rejected as a near-duplicate: "
                         f"{last.get('name')} — {last.get('problem')}. Change the user, task, and AI mechanism.")
            data = runner.llm.complete_json(system, user, schemas.CANDIDATES_SCHEMA,
                stage=f"ideation_{slot_id:02d}_try{attempt}", max_tokens=1800, tries=1,
                temperature=0.9 if attempt > 1 else 0.8, seed=slot_id * 10 + attempt)
            return dict(data["candidates"][0]), seed

        outcomes = ordered_fanout(
            round_rows, generate, workers=runner.cfg.llm_concurrency,
            cancelled=lambda: runner.cancel_requested,
            progress=lambda done, total: runner.item_progress("ideation", done, total))
        next_pending = []
        for i, (row, outcome) in enumerate(zip(round_rows, outcomes)):
            slot_id, attempt, _, _ = row
            if outcome.cancelled:
                next_pending.append(slot_id)
                pending_slots = next_pending + round_slots[i + 1:]
                persist_progress()
                continue
            angle = slot_angles[slot_id]
            slot_attempts[slot_id] = attempt
            candidate = None
            if outcome.value is None:
                rejected.append({"name": "malformed response", "problem": "No valid candidate response",
                                 "target_user": "", "ai_core": ""})
                runner.log(f"  candidate generation failed for slot {slot_id}: {type(outcome.error).__name__}")
            else:
                candidate, seed = outcome.value
                candidate["model_angle"] = candidate.get("angle", "")
                if not _angle_matches(angle, candidate):
                    runner.log(f"  candidate ignored assigned angle {angle!r}; refill round {attempt}/3")
                    rejected.append(candidate)
                    candidate = None
                else:
                    candidate["angle"] = angle
                    candidate["evidence_url"] = seed["url"]
                    candidate["evidence_title"] = seed.get("title", "")
                    if any(idea_similarity(candidate, old) >= SIMILARITY_THRESHOLD for old in candidates):
                        runner.log(f"  duplicate result for angle {angle!r}; refill round {attempt}/3")
                        rejected.append(candidate)
                        candidate = None
            if candidate is not None:
                candidate["id"] = f"c{len(candidates)+1:02d}"
                candidates.append(candidate)
                completed_slots.add(slot_id)
            elif attempt < 3:
                next_pending.append(slot_id)
            else:
                completed_slots.add(slot_id)
                runner.log(f"  no distinct candidate found for angle {angle!r}")
            pending_slots = next_pending + round_slots[i + 1:]
            persist_progress()
            runner.log(f"ideation progress: {len(candidates)}/{n} candidates; slot {slot_id}/{n} considered")
        pending_slots = next_pending
        if runner.cancel_requested:
            persist_progress()
            raise InterruptedError("ideation cancelled")
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
