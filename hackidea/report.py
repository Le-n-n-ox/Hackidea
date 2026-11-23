"""Render the structured blueprint as Markdown, responsive HTML, and a PDF."""
from __future__ import annotations

import html
import re
import time
from pathlib import Path
from xml.sax.saxutils import escape

from hackidea.blueprint_document import ARCHITECTURE, code_files, validate_timeline


STACK_REASONS = {
    "frontend": "One small page keeps the event demo focused.",
    "backend": "One Python app serves the page and the booking endpoints.",
    "database": "SQLite keeps fixtures and bookings in one local file.",
    "primary_ai": "A structured extraction call gives the app typed booking fields.",
    "sms": "The sponsor sandbox makes the confirmation part of the actual demo flow.",
    "hosting": "A laptop avoids a deployment step during a 12-hour build.",
}

def _get_sources(runner) -> tuple[list[dict], list[dict]]:
    docs_path = runner.run_dir / "blueprint_research.json"
    docs = runner.load("blueprint_research.json").get("sources", []) if docs_path.exists() else []
    research_path = runner.run_dir / "research_log.json"
    original = runner.load("research_log.json").get("sources", []) if research_path.exists() else []
    problems = [s for s in original if s.get("fetched") and s.get("relevant")
                and s.get("source_type") == "user_problems"]
    return docs, problems


def _citation(sources: list[dict], identity: str, label: str | None = None) -> str:
    source = next((s for s in sources if s.get("id") == identity), None)
    if not source:
        return ""
    return f" [{label or source['title']}]({source['url']})"


def _md_escape(value) -> str:
    return str(value or "").replace("\r", "").strip()


def _architecture_svg() -> str:
    positions = {node["id"]: (20 + (index % 3) * 230, 24 + (index // 3) * 112)
                 for index, node in enumerate(ARCHITECTURE["nodes"])}
    colors = {"user": "#e3f3ed", "app": "#d9e9ff", "ai": "#f2e5ff",
              "data": "#fff0d7", "external": "#ffe1de"}
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 270" role="img" aria-label="Booking flow architecture">',
             '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#52625c"/></marker></defs>']
    for source, target in ARCHITECTURE["edges"]:
        sx, sy = positions[source]
        tx, ty = positions[target]
        x1, y1, x2, y2 = sx + 180, sy + 34, tx, ty + 34
        if abs(y1 - y2) < 2:
            x1 += 5
            x2 -= 8
        parts.append(f'<path d="M{x1},{y1} L{x2},{y2}" stroke="#52625c" stroke-width="2" fill="none" marker-end="url(#arrow)"/>')
    for node in ARCHITECTURE["nodes"]:
        x, y = positions[node["id"]]
        fill = colors[node["kind"]]
        label = html.escape(node["label"])
        parts.append(f'<rect x="{x}" y="{y}" width="180" height="68" rx="12" fill="{fill}" stroke="#6c8077"/>')
        parts.append(f'<text x="{x+90}" y="{y+40}" text-anchor="middle" font-family="Arial,sans-serif" font-size="14" fill="#15251d">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _architecture_flow() -> list[str]:
    labels = {n["id"]: n["label"] for n in ARCHITECTURE["nodes"]}
    return [f"{labels[source]} -> {labels[target]}" for source, target in ARCHITECTURE["edges"]]


def _stack_rows(config: dict, sources: list[dict]) -> list[tuple[str, str, str]]:
    rows = []
    citations = {
        "backend": _citation(sources, "fastapi_quickstart", "FastAPI first steps"),
        "primary_ai": _citation(sources, "gemini_python", "Google GenAI Python guide") +
                      _citation(sources, "gemini_pricing", "Gemini API pricing"),
        "sms": _citation(sources, "at_python_sdk", "Africa's Talking Python SDK") +
               _citation(sources, "at_sandbox", "Africa's Talking sandbox guide"),
    }
    labels = {"primary_ai": "AI model", "sms": "SMS"}
    for key in ("frontend", "backend", "database", "primary_ai", "sms", "hosting"):
        rows.append((labels.get(key, key.replace("_", " ").title()),
                     config[key], STACK_REASONS[key] + citations.get(key, "")))
    return rows


def _sources_markdown(docs: list[dict], problem_sources: list[dict]) -> str:
    lines = ["## Sources", "", "### Problem evidence"]
    if problem_sources:
        for source in problem_sources:
            lines.append(f"- [{source.get('title') or source['url']}]({source['url']}) — supports the stated user task.")
    else:
        lines.append("- No fetched page is cited as direct evidence for the operator problem.")
    lines.extend(["", "### Documentation"])
    for source in docs:
        clean_title = re.sub(r"\s+", " ", source.get("title", source["id"])).strip()
        purpose = ", ".join(source.get("used_for", []))
        lines.append(f"- [{clean_title}]({source['url']}) — {purpose}.")
    lines.extend(["", "### Existing products"])
    lines.append("- No existing-product comparison is used in this build plan.")
    return "\n".join(lines)


def build_final(runner) -> str:
    """Build a phone-first report using only validated blueprint fields."""
    content = runner.load("blueprint_content.json")
    validate_timeline(content)
    config = runner.load("blueprint.json")["build_config"]
    decision = runner.load("decision.json")
    docs, problem_sources = _get_sources(runner)
    winner = next(c for c in runner.load("candidates.json")["candidates"]
                  if c["id"] == decision["winner_id"])
    files = code_files(content)
    summary = content["summary"]
    timeline = content["timeline"]["calendar"]

    lines = [f"# {winner['name']}", "", "## Decide in five minutes", "",
             f"**Build:** {summary['pitch']}", "", f"**For:** {summary['audience']}", "",
             "**Three-step demo**"]
    lines.extend(f"{i}. {step}" for i, step in enumerate(summary["demo_steps"], 1))
    lines.extend(["", "**Why this idea**"])
    lines.extend(f"- {reason}" for reason in summary["reasons"])
    lines.extend(["", f"**Biggest risk:** {summary['biggest_risk']}",
                  f"**Plan:** {summary['risk_plan']}", "",
                  f"**Confidence: {summary['confidence_level'].title()}.** {summary['confidence_note']}",
                  summary["confidence_action"], "", "---", "", "## Build plan", "",
                  "### 1. Idea and user", "",
                  f"**Working hypothesis:** {winner.get('problem','')}", "",
                  f"**User:** {winner.get('target_user','')}", "",
                  "### 2. Stack recommendations", "",
                  "| Part | Decision and reason |", "|---|---|"])
    for title, choice, reason in _stack_rows(config, docs):
        lines.append(f"| {title} | **{choice}** — {reason} |")
    lines.extend(["", "**What the official docs say:**"])
    at_source = _citation(docs, "at_sandbox", "Africa's Talking sandbox guide")
    sdk_source = _citation(docs, "at_python_sdk", "official Python SDK")
    gemini_price = _citation(docs, "gemini_pricing", "Gemini API pricing")
    gemini_sdk = _citation(docs, "gemini_python", "Google GenAI Python guide")
    fastapi_source = _citation(docs, "fastapi_quickstart", "FastAPI first steps")
    if at_source:
        lines.append(f"- The sponsor's sandbox is a test environment using its simulator; the test message appears in the simulator rather than a handset.{at_source}")
    if sdk_source:
        lines.append(f"- The official Python package documents sandbox initialization and the SMS send call.{sdk_source}")
    if gemini_price:
        lines.append(f"- Google's pricing page lists the Gemini 3.5 Flash-Lite Standard tier input and output as free of charge.{gemini_price}")
    if gemini_sdk:
        lines.append(f"- Google's Python SDK guide documents structured JSON responses.{gemini_sdk}")
    if fastapi_source:
        lines.append(f"- FastAPI's guide shows the route/application pattern used by this starter.{fastapi_source}")

    lines.extend(["", "### 3. Architecture", "",
                  "![Booking flow architecture](architecture.svg)", "",
                  "The diagram is generated from these typed nodes and edges:"])
    lines.extend(f"- {item}" for item in _architecture_flow())
    lines.extend(["", "### 4. Frontend and backend", "",
                  content["frontend"]["flow"], "", "**Screens**"])
    lines.extend(f"- **{row['name']}:** {row['purpose']}" for row in content["frontend"]["screens"])
    lines.extend(["", "**Polish quickly**"])
    lines.extend(f"- {item}" for item in content["frontend"]["polish"])
    lines.extend(["", "**Files**"])
    lines.extend(f"- `{row['path']}` — {row['purpose']}" for row in content["backend"]["files"])
    lines.extend(["", "**App routes**"])
    lines.extend(f"- `{row['method']} {row['path']}` — {row['purpose']}" for row in content["backend"]["routes"])
    lines.append("")
    lines.extend(f"{index}. {step}" for index, step in enumerate(content["backend"]["flow"], 1))
    lines.extend(["", f"**Errors:** {content['backend']['errors']}", "",
                  "### 5. AI integration", "", content["ai"]["job"], "",
                  "**Prompt (`prompts/booking.txt`)**", "", "```text",
                  content["ai"]["prompt"], "```", "",
                  "**Expected fields:** " + ", ".join(f"`{x}`" for x in content["ai"]["output_fields"]), "",
                  "**Evaluation cases**"])
    lines.extend(f"- {row['case']} → expect {row['expected']}" for row in content["ai"]["evaluation"])
    lines.extend(["", f"**Manual fallback:** {content['ai']['fallback']}", "",
                  "### 6. Data and sample fixtures", "",
                  content["data"]["source"], "", "**Schema (`data/schema.json`)**", "", "```json",
                  __import__("json").dumps(content["sample_schema"], indent=2), "```", "",
                  "**Sample data (`data/tours.json`)**", "", "```json",
                  __import__("json").dumps(content["data"]["fixtures"], indent=2, ensure_ascii=False), "```", "",
                  f"**Check:** {content['data']['validation']}", "",
                  "### 7. Timeline and team", "",
                  f"The calendar below covers all {content['timeline']['total_hours']:g} hours without gaps. The brief did not supply names, so aliases map to the listed team slots.", ""])
    for row in timeline:
        lines.append(f"- **{row['start']:g}–{row['end']:g}h · {row['owner']}** — {row['task']}")
    lines.extend(["", f"**Cut line:** {content['timeline']['cut_line']}", "", "**Work split**"])
    lines.extend(f"- **{row['owner']}:** {row['focus']}" for row in content["team_workstreams"])
    lines.extend(["", "### 8. Setup that runs", "",
                  "Create a folder, then use one of these command sets:", "", "**Windows PowerShell**", "", "```powershell",
                  "py -m venv .venv", ".venv\\Scripts\\Activate.ps1", "python -m pip install -r requirements.txt",
                  "Copy-Item .env.example .env  # add API keys and the simulator test number",
                  "python -m uvicorn main:app --reload", "```", "", "**Linux**", "", "```bash",
                  "python3 -m venv .venv", "source .venv/bin/activate", "python -m pip install -r requirements.txt",
                  "cp .env.example .env  # add API keys and the simulator test number",
                  "python -m uvicorn main:app --reload", "```", "",
                  "Open `http://127.0.0.1:8000`. Set `AT_API_KEY` to the sandbox app key and `AT_TEST_PHONE` to the simulator's registered test number. The app uses the sponsor sandbox directly; it does not fake a successful SMS.", ""])
    for path in ("prompts/booking.txt", "data/schema.json", "data/tours.json", "main.py", "requirements.txt", ".env.example"):
        language = "json" if path.endswith(".json") else "python" if path.endswith(".py") else "text"
        lines.extend([f"**`{path}`**", "", f"```{language}", files[path].rstrip(), "```", ""])

    lines.extend(["### 9. Demo and pitch", ""])
    lines.extend(f"- **{row['minute']} — {row['show']}** {row['say']}" for row in content["demo"]["steps"])
    lines.extend(["", "**Pitch**"])
    lines.extend(f"- **{key.title()}:** {value}" for key, value in content["demo"]["pitch"].items())
    lines.extend(["", "**Judge questions**"])
    lines.extend(f"- **{row['question']}** {row['answer']}" for row in content["demo"]["questions"])
    lines.extend(["", "### 10. Risks", ""])
    for row in content["risks"]["items"]:
        if _too_similar(row["risk"], summary["biggest_risk"]):
            continue
        lines.append(f"- **{row['risk']}** — {row['mitigation']}")
    lines.extend(["", "### Check before you rely on this plan", "",
                  "- Test the chosen model key and the sponsor sandbox with the exact event account before rehearsal.",
                  "- Use only simulator test recipients and synthetic tour rows. Do not collect PINs or trigger real-money activity.", "",
                  _sources_markdown(docs, problem_sources), "", "## Appendix: Why this idea was selected", "",
                  content["appendix"]["selection"], ""])
    return "\n".join(lines)


def _too_similar(left: str, right: str) -> bool:
    from difflib import SequenceMatcher
    a = re.sub(r"\W+", " ", left.lower()).strip()
    b = re.sub(r"\W+", " ", right.lower()).strip()
    return bool(a and b and SequenceMatcher(None, a, b).ratio() >= 0.72)


def build_html(runner) -> str:
    content = runner.load("blueprint_content.json")
    markdown = build_final(runner)
    docs, _ = _get_sources(runner)
    sources = "".join(f"<li><a href=\"{html.escape(s['url'], quote=True)}\">{html.escape(s['title'])}</a></li>" for s in docs)
    summary = content["summary"]
    winner_id = runner.load("decision.json")["winner_id"]
    winner = next(c for c in runner.load("candidates.json")["candidates"] if c["id"] == winner_id)
    hero = (f"<header><p class=\"eyebrow\">Hackathon build plan · {html.escape(str(runner.ctx.get('duration_hours') or 12))} hours</p>"
            f"<h1>{html.escape(str(winner.get('name','HackIdea')))}</h1>"
            f"<p class=\"lead\">{html.escape(summary['pitch'])}</p><p><b>For:</b> {html.escape(summary['audience'])}</p>"
            f"<h2>Decide in five minutes</h2><ol>{''.join('<li>'+html.escape(x)+'</li>' for x in summary['demo_steps'])}</ol>"
            f"<ul>{''.join('<li>'+html.escape(x)+'</li>' for x in summary['reasons'])}</ul>"
            f"<p><b>Biggest risk:</b> {html.escape(summary['biggest_risk'])} <b>Plan:</b> {html.escape(summary['risk_plan'])}</p>"
            f"<p class=\"confidence\"><b>Confidence: {html.escape(summary['confidence_level'].title())}.</b> {html.escape(summary['confidence_note'])}<br>{html.escape(summary['confidence_action'])}</p></header>")
    sections = _markdown_to_html(markdown)
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HackIdea build plan</title>
<style>
:root{color-scheme:light;--ink:#182a22;--muted:#50645a;--green:#126649;--paper:#f4f7f4}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:820px;margin:auto;padding:1rem}.hero,article>section{background:white;border:1px solid #dce6df;border-radius:18px;padding:1.2rem;margin:1rem 0;box-shadow:0 5px 20px #1635220a}.hero{border-top:7px solid var(--green)}h1{font-size:clamp(2rem,7vw,3.2rem);line-height:1.08;margin:.3rem 0 1rem}.lead{font-size:1.2rem}.eyebrow{color:var(--green);font-weight:700;text-transform:uppercase;letter-spacing:.08em}.confidence{background:#eef7f1;border-radius:10px;padding:.8rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f3;padding:.8rem;border-radius:8px}code{overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.94rem}td,th{padding:.55rem;text-align:left;vertical-align:top;border-bottom:1px solid #dce6df}a{color:var(--green)}svg{width:100%;height:auto}@media(max-width:560px){body{font-size:16px}.table-wrap tr{display:block;border:1px solid #dce6df;margin:.5rem 0;border-radius:9px}.table-wrap td,.table-wrap th{display:block;border:0;padding:.35rem}.table-wrap th{color:var(--green)}}@media(min-width:800px){main{padding:2rem}.hero,article>section{padding:1.7rem}}
</style><main>""" + hero + """<article>""" + sections + "</article></main></html>"


def _html_inline(text: str) -> str:
    value = html.escape(text)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
                   r"<a href=\"\2\">\1</a>", value)
    return value


def _markdown_to_html(markdown: str) -> str:
    output = []
    lines = markdown.splitlines()
    i = 0
    in_list = None
    def close_list():
        nonlocal in_list
        if in_list:
            output.append(f"</{in_list}>"); in_list = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("# ") or line == "## Decide in five minutes":
            i += 1; continue
        if not line.strip() or line.strip() == "---":
            close_list(); i += 1; continue
        if line.startswith("```"):
            close_list(); code = []; i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i]); i += 1
            if i < len(lines): i += 1
            output.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue
        image = re.fullmatch(r"!\[([^\]]*)\]\(architecture\.svg\)", line)
        if image:
            close_list(); output.append(_architecture_svg()); i += 1; continue
        if line.startswith("|---"):
            i += 1; continue
        if line.startswith("| Part |"):
            close_list(); rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [cell.strip() for cell in lines[i].strip("|").split("|")]
                if not all(set(cell) <= {"-", ":"} for cell in cells): rows.append(cells)
                i += 1
            if rows:
                output.append('<div class="table-wrap"><table><tbody>')
                for row_idx, row in enumerate(rows):
                    tag = "th" if row_idx == 0 else "td"
                    output.append("<tr>" + "".join(f"<{tag}>{_html_inline(cell)}</{tag}>" for cell in row) + "</tr>")
                output.append("</tbody></table></div>")
            continue
        if line.startswith("### ") or line.startswith("## "):
            close_list(); level = "h3" if line.startswith("### ") else "h2"
            output.append(f"<{level}>{_html_inline(line.lstrip('# ').strip())}</{level}>"); i += 1; continue
        if line.startswith("- "):
            if in_list != "ul": close_list(); output.append("<ul>"); in_list = "ul"
            output.append(f"<li>{_html_inline(line[2:])}</li>"); i += 1; continue
        number = re.match(r"^(\d+)\. (.*)$", line)
        if number:
            if in_list != "ol": close_list(); output.append("<ol>"); in_list = "ol"
            output.append(f"<li>{_html_inline(number.group(2))}</li>"); i += 1; continue
        close_list()
        output.append(f"<p>{_html_inline(line)}</p>"); i += 1
    close_list()
    return "".join(output)


def _inline_markup(text: str) -> str:
    value = escape(str(text))
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", value)
    value = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<link href='\2' color='#126649'>\1</link>", value)
    value = value.replace("\n", "<br/>")
    return value


def _build_pdf(path: Path, runner) -> int:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, PageBreak,
                                    PageTemplate, Paragraph, Spacer, Table, TableStyle)

    content = runner.load("blueprint_content.json")
    md = build_final(runner)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HeroTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=25, leading=29, textColor=colors.HexColor("#126649"), alignment=TA_LEFT, spaceAfter=10))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading1"], fontSize=17, leading=21, textColor=colors.HexColor("#126649"), spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="Subsection", parent=styles["Heading2"], fontSize=12.5, leading=16, textColor=colors.HexColor("#182a22"), spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="BodySmall", parent=styles["BodyText"], fontSize=9.4, leading=13, spaceAfter=5))
    styles.add(ParagraphStyle(name="CodeSmall", parent=styles["Code"], fontName="Courier", fontSize=6.8, leading=8.6, leftIndent=6, rightIndent=6, backColor=colors.HexColor("#f2f5f3"), borderPadding=6, spaceAfter=5, splitLongWords=True))
    styles.add(ParagraphStyle(name="BulletSmall", parent=styles["BodyText"], fontSize=9.2, leading=12.2, leftIndent=11, firstLineIndent=-7, bulletIndent=0, spaceAfter=3))

    class Diagram(Flowable):
        def __init__(self):
            super().__init__(); self.width = 175 * mm; self.height = 61 * mm
        def draw(self):
            c = self.canv
            nodes = ARCHITECTURE["nodes"]
            positions = {node["id"]: (12 * mm + (i % 3) * 58 * mm, 42 * mm - (i // 3) * 31 * mm)
                         for i, node in enumerate(nodes)}
            fills = {"user": "#e3f3ed", "app": "#d9e9ff", "ai": "#f2e5ff", "data": "#fff0d7", "external": "#ffe1de"}
            for source, target in ARCHITECTURE["edges"]:
                sx, sy = positions[source]; tx, ty = positions[target]
                c.setStrokeColor(colors.HexColor("#52625c")); c.setLineWidth(1)
                c.line(sx + 47 * mm, sy - 2 * mm, tx, ty - 2 * mm)
                c.line(tx - 2 * mm, ty - 2 * mm, tx, ty - 2 * mm)
            for node in nodes:
                x, y = positions[node["id"]]
                c.setFillColor(colors.HexColor(fills[node["kind"]])); c.setStrokeColor(colors.HexColor("#6c8077"))
                c.roundRect(x, y - 8 * mm, 47 * mm, 16 * mm, 2 * mm, stroke=1, fill=1)
                c.setFillColor(colors.HexColor("#182a22")); c.setFont("Helvetica", 6.8)
                c.drawCentredString(x + 23.5 * mm, y - 1 * mm, node["label"][:35])

    class PageDoc(BaseDocTemplate):
        def __init__(self, filename):
            super().__init__(str(filename), pagesize=A4, leftMargin=17*mm, rightMargin=17*mm, topMargin=16*mm, bottomMargin=15*mm, title="HackIdea build plan")
            frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="normal")
            self.addPageTemplates([PageTemplate(id="all", frames=frame, onPage=self._page)])
        def _page(self, canvas, doc):
            canvas.saveState(); canvas.setFont("Helvetica", 7.5); canvas.setFillColor(colors.HexColor("#61736a"))
            canvas.drawString(17*mm, 9*mm, str(runner.load("candidates.json")["candidates"][0].get("name", "HackIdea")))
            canvas.drawRightString(A4[0]-17*mm, 9*mm, f"{doc.page}")
            canvas.restoreState()

    story = []
    title = runner.load("decision.json")["winner_id"]
    winner = next(c for c in runner.load("candidates.json")["candidates"] if c["id"] == title)
    summary = content["summary"]
    story += [Paragraph(escape(winner["name"]), styles["HeroTitle"]),
              Paragraph("DECIDE IN FIVE MINUTES", styles["Subsection"]),
              Paragraph("<b>Build:</b> " + _inline_markup(summary["pitch"]), styles["BodySmall"]),
              Paragraph("<b>For:</b> " + _inline_markup(summary["audience"]), styles["BodySmall"]),
              Paragraph("<b>Three-step demo</b>", styles["BodySmall"])]
    for i, step in enumerate(summary["demo_steps"], 1):
        story.append(Paragraph(f"{i}. {_inline_markup(step)}", styles["BulletSmall"]))
    story.append(Paragraph("<b>Why this idea</b>", styles["BodySmall"]))
    for reason in summary["reasons"]:
        story.append(Paragraph("- " + _inline_markup(reason), styles["BulletSmall"]))
    story.extend([Paragraph("<b>Biggest risk:</b> " + _inline_markup(summary["biggest_risk"]), styles["BodySmall"]),
                  Paragraph("<b>Plan:</b> " + _inline_markup(summary["risk_plan"]), styles["BodySmall"]),
                  Paragraph(f"<b>Confidence: {escape(summary['confidence_level'].title())}.</b> " + _inline_markup(summary["confidence_note"]), styles["BodySmall"]),
                  Paragraph(_inline_markup(summary["confidence_action"]), styles["BodySmall"]), PageBreak()])

    for section in re.split(r"(?=^#{2,3} )", md, flags=re.MULTILINE):
        if not section.strip() or section.startswith("# ") or section.startswith("## Decide in five minutes"):
            continue
        lines = section.strip().splitlines()
        header = lines[0].lstrip("# ").strip()
        if header == "Build plan":
            story.append(Paragraph("Build plan", styles["Section"])); continue
        if header == "3. Architecture":
            story.append(Paragraph(header, styles["Subsection"])); story.append(Diagram()); continue
        story.append(Paragraph(escape(header), styles["Section"] if header.startswith("Appendix") else styles["Subsection"]))
        buffer = []
        fenced = False
        for line in lines[1:]:
            if line.startswith("```"):
                if fenced:
                    text = "<br/>".join(escape(x) for x in buffer)
                    story.append(Paragraph(text, styles["CodeSmall"])); buffer = []; fenced = False
                else:
                    if buffer:
                        story.append(Paragraph("<br/>".join(_inline_markup(x) for x in buffer), styles["BodySmall"])); buffer = []
                    fenced = True
                continue
            if fenced:
                buffer.append(line); continue
            if not line.strip():
                if buffer:
                    text = "<br/>".join(_inline_markup(x) for x in buffer)
                    story.append(Paragraph(text, styles["BodySmall"])); buffer = []
                continue
            if line.startswith("### "):
                if buffer:
                    story.append(Paragraph("<br/>".join(_inline_markup(x) for x in buffer), styles["BodySmall"])); buffer = []
                story.append(Paragraph(escape(line[4:]), styles["Subsection"])); continue
            if line.startswith("|---") or line.startswith("| Part"):
                continue
            if line.startswith("|"):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if len(cells) == 2:
                    table = Table([[Paragraph(_inline_markup(c), styles["BodySmall"]) for c in cells]], colWidths=[35*mm, 143*mm])
                    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f3f7f4")),
                                               ("BOX", (0,0), (-1,-1), .25, colors.HexColor("#d7e1da")),
                                               ("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 5),
                                               ("RIGHTPADDING", (0,0), (-1,-1), 5)]))
                    story.append(table)
                continue
            if line.startswith("- ") or re.match(r"\d+\. ", line):
                if buffer:
                    story.append(Paragraph("<br/>".join(_inline_markup(x) for x in buffer), styles["BodySmall"])); buffer=[]
                story.append(Paragraph("- " + _inline_markup(re.sub(r"^(- |\d+\. )", "", line)), styles["BulletSmall"]))
            else:
                buffer.append(line)
        if buffer:
            text = "<br/>".join(escape(x) if fenced else _inline_markup(x) for x in buffer)
            story.append(Paragraph(text, styles["CodeSmall"] if fenced else styles["BodySmall"]))
        story.append(Spacer(1, 3*mm))
    doc = PageDoc(path)
    doc.build(story)
    raw = path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page\b", raw))


def write_outputs(runner, markdown: str) -> dict[str, str | int]:
    runner.save_text("final_report.md", markdown)
    html_path = runner.run_dir / "final_report.html"
    html_path.write_text(build_html(runner), encoding="utf-8")
    svg_path = runner.run_dir / "architecture.svg"
    svg_path.write_text(_architecture_svg(), encoding="utf-8")
    pdf_path = runner.run_dir / "final_report.pdf"
    page_count = _build_pdf(pdf_path, runner)
    return {"markdown": str(runner.run_dir / "final_report.md"), "html": str(html_path),
            "pdf": str(pdf_path), "pages": page_count}
