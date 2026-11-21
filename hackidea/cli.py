"""HackIdea CLI: hackidea run --context "..." [--depth quick|standard|deep] [--interactive]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hackidea.config import Config
from hackidea.runner import Runner, STAGE_ORDER


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hackidea",
                                description="Find the single best hackathon idea for a context, then plan the build.")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the full pipeline (or one stage / resume)")
    run.add_argument("--context", "-c", help="free-text hackathon context")
    run.add_argument("--context-file", help="read context from a file")
    run.add_argument("--depth", choices=["quick", "standard", "deep"], default=None,
                     help="scales searches & candidates (default: env DEPTH or standard)")
    run.add_argument("--interactive", "-i", action="store_true",
                     help="ask up to 3 clarifying questions when key inputs are missing")
    run.add_argument("--resume", metavar="RUN_DIR", help="reuse an existing runs/<ts>/ directory")
    run.add_argument("--stage", choices=STAGE_ORDER, help="run only this stage (needs prior artifacts)")
    run.add_argument("--runs-dir", default=None, help="override runs directory")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = Config.from_env(depth=args.depth)
        if args.runs_dir:
            cfg.runs_dir = args.runs_dir
        context_text = args.context
        if args.context_file:
            context_text = Path(args.context_file).read_text(encoding="utf-8")
        interactive = args.interactive
        if not context_text and not args.resume:
            if sys.stdin.isatty():
                interactive = True
                context_text = input("Paste hackathon context (theme, duration, team, sponsors...): ").strip()
            else:
                print("error: provide --context/--context-file or --resume", file=sys.stderr)
                return 2
        if not context_text and not args.resume:
            print("error: empty context", file=sys.stderr)
            return 2
        runner = Runner(cfg, run_dir=args.resume, interactive=interactive)
        run_dir = runner.run(context_text=context_text,
                             resume_from=args.stage if args.resume and args.stage else None,
                             only_stage=args.stage)
        print(f"\n[hackidea] artifacts: {run_dir.resolve()}", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"[hackidea] FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())