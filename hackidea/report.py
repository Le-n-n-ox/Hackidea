"""Assemble the final single-idea markdown report (spec sections 1-14)."""
from __future__ import annotations

import re
import time

from hackidea.search import normalize_url


def split_sections(md: str) -> dict[int, str]:
    """Split blueprint markdown into {section_number: text} keyed by '## N.' headers."""
    out: dict[int, str] = {}
    matches = list(re.finditer(r"^##\s+(\d+)\.\s.*$", md, re.MULTILINE))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        out[int(m.group(1))] = md[m.start():end].strip()
    return out


def build_final(runner) -> str:
    ctx = runner.ctx
    decision = runner.load("decision.json")
    scoring = runner.load("scoring_table.json")
    stability = runner.load("stability.json")
    tournament = runner.load("tournament.json")
    redteam = {r["id"]: r for r in runner.load("redteam.json")["reviews"]}
    by_id = {c["id"]: c for c in runner.load("candidates.json")["candidates"]}
    sources = runner.load("research_log.json")["sources"]
    winner = by_id[decision["winner_id"]]

    parts: dict[int, str] = {}
    for key in ("a", "b", "c", "d"):
        p = runner.run_dir / f"blueprint_{key}.md"
        if p.exists():
            parts.update(split_sections(p.read_text(encoding="utf-8")))

    # --- section 2: why it wins (programmatic, evidence-bound) ---
    wr = next((r for r in scoring["rows"] if r["id"] == winner["id"]), {})
    rt = redteam.get(winner["id"], {})
    s2 = ["## 2. Why it wins",
          f"**Confidence: {decision['confidence_level']} ({decision['confidence_pct']:.0f}%).** "
          f"Key assumption that would change the pick: {decision['key_assumption']}",
          "", "### Why this beat the others"]
    for b in decision["why_beats_others"]:
        line = f"- {b['point']}"
        if b.get("evidence_url"):
            line += f" ([source]({b['evidence_url']}))"
        s2.append(line)
    s2 += ["", "### Scores and head-to-head",
           f"Winner weighted score: **{wr.get('weighted_total', '?')}** "
           f"(rank {wr.get('rank', '?')} of {len(scoring['rows'])}). "
           f"Tournament wins: {tournament['wins'].get(winner['id'], 0)}.",
           "", scoring.get("table_md", ""), "",
           f"Weighting note: {scoring.get('note', '')}", "",
           f"Ranking stability: {stability['resolution']}", "",
           "### Red-team summary (winner)",
           f"Strongest objection: {rt.get('strongest_objection', 'n/a')}"]
    for risk in rt.get("risks", [])[:4]:
        s2.append(f"- **{risk['category']}** ({risk['severity']}): {risk['risk']} "
                  f"→ Mitigation: {risk['mitigation']}")
    parts[2] = "\n".join(s2)

    # --- section 14: sources (ONLY actually fetched pages) ---
    seen, src_lines = set(), []
    for s in sources:
        if not s.get("fetched"):
            continue
        u = normalize_url(s["url"])
        if u in seen:
            continue
        seen.add(u)
        date = f" ({s['date']})" if s.get("date") else ""
        src_lines.append(f"{len(src_lines)+1}. [{s.get('title') or u}]({u}){date} — "
                         f"{s.get('source_type', '')}. {s.get('takeaway', '')}")
    parts[14] = "## 14. Sources\n\nOnly pages actually fetched during research are cited.\n\n" + \
                ("\n".join(src_lines) if src_lines else "_No pages could be fetched; see research_log.json._")

    # --- header ---
    head = [f"# {winner['name']} — HackIdea build blueprint",
            f"*{winner['pitch']}*", "",
            f"- **Theme:** {ctx.get('theme', '')}",
            f"- **Duration:** {ctx.get('duration_hours')}h | **Team:** "
            + ", ".join(m['role'] for m in ctx.get('team', [])),
            f"- **Depth:** {runner.cfg.depth} | **Run:** {runner.run_dir.name} "
            f"| **Generated:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"]
    if ctx.get("assumptions"):
        head.append("- **Assumptions:** " + " ".join(ctx["assumptions"]))
    head.append("")

    body = []
    for n in range(1, 15):
        if n in parts:
            body.append(parts[n])
        else:
            body.append(f"## {n}. (missing)\n\n_Section not generated — re-run: "
                        f"hackidea run --resume {runner.run_dir} --stage blueprint_")
    return "\n".join(head) + "\n\n" + "\n\n".join(body) + "\n"