"""Stage orchestration: runs/<timestamp>/ with one JSON artifact per stage, resumable."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from hackidea.cache import DiskCache
from hackidea.llm import make_llm
from hackidea.search import make_search, fetch_page

STAGE_ORDER = ["context", "research", "ideation", "filtering", "scoring",
               "adversarial", "decision", "blueprint"]


class Runner:
    def __init__(self, cfg, llm=None, search=None, run_dir=None, fetch=None,
                 interactive=False, ask=None):
        self.cfg = cfg
        self.preset = cfg.preset
        self.cache = DiskCache(cfg.cache_dir)
        self.llm = llm if llm is not None else make_llm(cfg)
        self.search = search if search is not None else make_search(cfg, self.cache)
        self.fetch = fetch if fetch is not None else (lambda url: fetch_page(url, cfg, self.cache))
        self.run_dir = Path(run_dir) if run_dir else None
        self.interactive = interactive
        self.ask = ask or input
        self.ctx: dict = {}

    # ---- artifacts ----
    def new_run(self) -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        self.run_dir = Path(self.cfg.runs_dir) / ts
        self.run_dir.mkdir(parents=True, exist_ok=True)
        return self.run_dir

    def save(self, name: str, obj) -> Path:
        assert self.run_dir is not None
        p = self.run_dir / name
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    def save_text(self, name: str, value: str) -> Path:
        assert self.run_dir is not None
        p = self.run_dir / name
        p.write_text(value, encoding="utf-8")
        return p

    def load(self, name: str):
        p = self.run_dir / name
        if not p.exists():
            raise FileNotFoundError(f"missing artifact {p} (run earlier stages first)")
        return json.loads(p.read_text(encoding="utf-8"))

    def log(self, msg: str) -> None:
        print(f"[hackidea] {msg}", file=sys.stderr, flush=True)

    def llm_request_count(self) -> int:
        return int(getattr(self.llm, "request_count", len(getattr(self.llm, "calls", []))))

    # ---- pipeline ----
    def run(self, context_text: str | None = None, resume_from: str | None = None,
            only_stage: str | None = None) -> Path:
        from hackidea import context as context_stage
        from hackidea.stages import (research, ideation, filtering, scoring,
                                     adversarial, decision, blueprint)
        from hackidea.report import build_final

        handlers = {
            "context": lambda: context_stage.run(self, context_text or ""),
            "research": lambda: research.run(self),
            "ideation": lambda: ideation.run(self),
            "filtering": lambda: filtering.run(self),
            "scoring": lambda: scoring.run(self),
            "adversarial": lambda: adversarial.run(self),
            "decision": lambda: decision.run(self),
            "blueprint": lambda: blueprint.run(self),
        }
        if only_stage and only_stage not in handlers:
            raise SystemExit(f"unknown stage {only_stage!r}; choose from {STAGE_ORDER}")

        if self.run_dir is None:
            self.new_run()
        state_path = self.run_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"completed": []}
        state.setdefault("stage_runs", [])
        if (self.run_dir / "context.json").exists():
            self.ctx = self.load("context.json")

        start_idx = STAGE_ORDER.index(resume_from) if resume_from else 0
        if resume_from and resume_from not in STAGE_ORDER:
            raise SystemExit(f"unknown stage {resume_from!r}")

        for i, name in enumerate(STAGE_ORDER):
            if only_stage and name != only_stage:
                continue
            if i < start_idx and name != "context":
                continue
            # A resume from a later stage already has its parsed context on disk.
            # Keep it and skip extraction so we do not overwrite it with empty input.
            if name == "context" and self.ctx and (resume_from is None or start_idx > 0) and only_stage != "context":
                state["completed"] = list(dict.fromkeys(state["completed"] + [name]))
                continue
            if name == "context" and not context_text and not self.ctx:
                raise SystemExit("no context: pass --context or --resume a run dir")
            self.log(f"=== stage {i+1}/8: {name} ===")
            t0 = time.time()
            calls_before = self.llm_request_count()
            try:
                handlers[name]()
            except BaseException as exc:
                calls_this_stage = self.llm_request_count() - calls_before
                seconds = round(time.time() - t0, 1)
                state["last_stage_seconds"] = seconds
                state["llm_requests_last_stage"] = calls_this_stage
                state["llm_requests_total"] = state.get("llm_requests_total", 0) + calls_this_stage
                state["stage_runs"].append({"stage": name, "seconds": seconds,
                                            "llm_requests": calls_this_stage,
                                            "status": "failed", "error": type(exc).__name__})
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                raise
            calls_this_stage = self.llm_request_count() - calls_before
            state["completed"] = [s for s in state["completed"] if s != name] + [name]
            state["last_stage_seconds"] = round(time.time() - t0, 1)
            state["llm_requests_last_stage"] = calls_this_stage
            state["llm_requests_total"] = state.get("llm_requests_total", 0) + calls_this_stage
            state["stage_runs"].append({"stage": name,
                                        "seconds": state["last_stage_seconds"],
                                        "llm_requests": calls_this_stage,
                                        "status": "completed"})
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            self.log(f"--- {name} done in {state['last_stage_seconds']}s")
            if only_stage:
                break

        if not only_stage or only_stage == "blueprint":
            report = build_final(self)
            out = self.save_text("final_report.md", report)
            self.log(f"final report: {out}")
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            print(report)
        return self.run_dir
