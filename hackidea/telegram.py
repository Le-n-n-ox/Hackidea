"""Telegram long-polling interface for running HackIdea from a private chat."""
from __future__ import annotations

import os
import json
import mimetypes
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import requests

from hackidea.config import Config, load_dotenv
from hackidea.runner import Runner

API_ROOT = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE_LENGTH = 3900


def safe_failure_hint(exc: BaseException) -> str:
    """Explain common LLM failures without exposing provider response bodies or secrets."""
    detail = str(exc)
    status_match = re.search(r"\bHTTP (\d{3})\b", detail)
    if status_match:
        status = int(status_match.group(1))
        if status in (401, 403):
            return f"LLM provider returned HTTP {status}: check that the API key is valid and this model is enabled for the account."
        if status == 404:
            return "LLM provider returned HTTP 404: check the model ID and API base URL (normally ending in /v1)."
        if status == 429:
            return "LLM provider returned HTTP 429: check rate limits, account quota, and billing with the provider."
        if status >= 500:
            return f"LLM provider returned HTTP {status}: it may be temporarily unavailable; try again later."
        return f"LLM provider returned HTTP {status}: check the provider's model and request compatibility."
    lowered = detail.lower()
    if "json invalid after" in lowered or "no parseable json" in lowered:
        return "The LLM responded, but did not return the structured format this pipeline requires. Check that the selected model supports the requested output."
    if "empty content" in lowered:
        return "The LLM returned an empty response. Check that the selected model supports chat completions."
    if "connectionerror" in lowered or "connecttimeout" in lowered or "timed out" in lowered:
        return "Could not connect to the LLM endpoint. Check the API base URL and internet connection."
    return f"LLM request failed ({type(exc).__name__}). Check the provider, model ID, API base URL, and available quota."


def safe_telegram_failure_hint(exc: BaseException) -> str:
    """Return useful Telegram upload diagnostics without exposing the token-bearing URL."""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            description = response.json().get("description")
        except (ValueError, AttributeError):
            description = None
        if description:
            return f"HTTP {response.status_code}: {description}"
        return f"HTTP {response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "Telegram request timed out"
    if isinstance(exc, requests.ConnectionError):
        return "could not connect to Telegram"
    return type(exc).__name__


class TelegramBot:
    def __init__(self, token: str, allowed_user_ids: set[int]):
        self.token = token
        self.allowed_user_ids = allowed_user_ids
        self._sessions = threading.local()
        self._run_lock = threading.Lock()
        self._run_active = False
        self._cancel_requested = False
        self._state_path = Path("runs") / ".telegram_state.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.state = {"drafts": {}, "last_update_id": -1}
        if self.state.get("active_run"):
            self.state["interrupted_run"] = self.state.pop("active_run")
            self._save_state()
        self._run_active = False

    def _save_state(self):
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_path)

    def _session(self) -> requests.Session:
        """Keep requests sessions thread-local while a pipeline runs in the background."""
        session = getattr(self._sessions, "session", None)
        if session is None:
            session = requests.Session()
            self._sessions.session = session
        return session

    def api(self, method: str, **params):
        response = self._session().post(
            API_ROOT.format(token=self.token, method=method),
            json=params,
            timeout=35,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", f"Telegram {method} failed"))
        return data["result"]

    def send(self, chat_id: int, message: str) -> None:
        # Telegram limits text messages to 4096 characters. Keep a little margin.
        remaining = message
        while len(remaining) > MAX_MESSAGE_LENGTH:
            cut = remaining.rfind("\n", 0, MAX_MESSAGE_LENGTH)
            if cut < MAX_MESSAGE_LENGTH // 2:
                cut = MAX_MESSAGE_LENGTH
            self.api("sendMessage", chat_id=chat_id, text=remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")
        if remaining:
            self.api("sendMessage", chat_id=chat_id, text=remaining)

    def send_document(self, chat_id: int, path: Path, caption: str = "") -> None:
        """Upload a local report as a Telegram document attachment."""
        with path.open("rb") as document:
            response = self._session().post(
                API_ROOT.format(token=self.token, method="sendDocument"),
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (path.name, document,
                                    mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
                timeout=60,
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram document upload failed"))

    @staticmethod
    def help_text() -> str:
        return ("Send the event brief in as many messages as needed. Then /review, correct with /set field value, "
                "choose /depth quick|standard|deep, and /confirm to start.\n\n"
                "Commands: /review /confirm /depth /set /status /cancel /alternatives /last /help /id")

    def handle(self, update: dict) -> None:
        update_id = update.get("update_id")
        if update_id is not None and update_id <= self.state.get("last_update_id", -1):
            return
        if update_id is not None:
            self.state["last_update_id"] = update_id
            self._save_state()
        message = update.get("message") or update.get("edited_message")
        if not message or not isinstance(message.get("text"), str):
            return
        chat_id = message["chat"]["id"]
        if message["chat"].get("type") != "private":
            self.send(chat_id, "Please use this bot in a private chat.")
            return
        user = message.get("from") or {}
        user_id = user.get("id")
        text = message["text"].strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""

        if command in ("/start", "/help"):
            self.send(chat_id, self.help_text())
            return
        if command == "/id":
            self.send(chat_id, f"Your Telegram user ID is {user_id}.")
            return
        if user_id not in self.allowed_user_ids and command not in ("/start", "/help"):
            if not self.allowed_user_ids:
                self.send(chat_id, "Runs are disabled until the owner is configured. Send /id, then add that number to TELEGRAM_ALLOWED_USER_IDS in .env and restart the bot.")
            else:
                self.send(chat_id, "This bot is private. Your Telegram account is not on its allowed-user list.")
            return
        if command == "/status":
            active = self.state.get("active_run")
            interrupted = self.state.get("interrupted_run")
            current = (f"Run stage: {active.get('stage', 'starting')}"
                       + (f" ({active['items_done']}/{active['items_total']} items)"
                          if active.get("items_total") else "")) if active else None
            self.send(chat_id, current if active else
                      (f"The previous run stopped during {interrupted.get('stage', 'unknown')} after a restart." if interrupted else "No run is active."))
            return
        if command == "/last":
            report = self.state.get("last_report")
            if report and Path(report).exists(): self.send_document(chat_id, Path(report), "Most recent report.")
            elif self.state.get("last_markdown") and Path(self.state["last_markdown"]).exists():
                self.send_document(chat_id, Path(self.state["last_markdown"]), "Most recent report (Markdown).")
            else: self.send(chat_id, "No completed report is available.")
            return
        if command == "/alternatives":
            latest = self.state.get("last_run_dir")
            if not latest or not Path(latest, "candidates.json").exists():
                self.send(chat_id, "No completed run has finalists yet."); return
            run = Path(latest)
            rows = json.loads((run / "candidates.json").read_text(encoding="utf-8"))["candidates"]
            winner = json.loads((run / "decision.json").read_text(encoding="utf-8"))["winner_id"]
            scores = {r["id"]: r for r in json.loads((run / "scoring_table.json").read_text(encoding="utf-8")).get("rows", [])}
            finalist_ids = json.loads((run / "stability.json").read_text(encoding="utf-8")).get("final_order", [])
            lines = [f"{c['id']}: {c['name']} — {c.get('pitch','')} (score {scores.get(c['id'],{}).get('weighted_total','?')})"
                     for c in rows if c["id"] in finalist_ids and c["id"] != winner]
            self.send(chat_id, "Other finalists:\n" + "\n".join(lines) + "\nRequest a blueprint with /blueprint candidate_id.")
            return
        if command == "/blueprint":
            latest = self.state.get("last_run_dir")
            candidate_id = text.partition(" ")[2].strip()
            if not latest or not candidate_id:
                self.send(chat_id, "Use /blueprint candidate_id after /alternatives."); return
            source = Path(latest)
            try:
                rows = json.loads((source / "candidates.json").read_text(encoding="utf-8"))["candidates"]
                candidate = next(c for c in rows if c["id"] == candidate_id)
                finalists = json.loads((source / "stability.json").read_text(encoding="utf-8")).get("final_order", [])
                if candidate_id not in finalists:
                    raise StopIteration
            except (OSError, ValueError, StopIteration):
                self.send(chat_id, "That candidate ID is not in the most recent run."); return
            self.send(chat_id, f"Building a separate blueprint for {candidate['name']}.")
            threading.Thread(target=self._alternative_blueprint,
                             args=(chat_id, source, candidate_id), daemon=True).start()
            return
        if command == "/cancel":
            with self._run_lock:
                active = self._run_active
            if active:
                self._cancel_requested = True
                if getattr(self, "_runner", None) is not None:
                    self._runner.cancel_requested = True
                self.send(chat_id, "Cancellation requested. I’ll stop at the next stage boundary.")
            else:
                self.state.setdefault("drafts", {}).pop(str(user_id), None)
                self._save_state()
                self.send(chat_id, "No run is active; cleared the unconfirmed brief, if any.")
            return
        if command == "/depth":
            value = text.partition(" ")[2].strip().lower()
            if value not in ("quick", "standard", "deep"):
                self.send(chat_id, "Choose /depth quick, /depth standard, or /depth deep."); return
            draft = self.state.setdefault("drafts", {}).setdefault(str(user_id), {"text": ""})
            draft["depth"] = value; self._save_state(); self.send(chat_id, f"Depth set to {value}."); return
        if command == "/set":
            fields = text.split(maxsplit=2)
            if len(fields) < 3:
                self.send(chat_id, "Use /set field value (theme, duration_hours, team, sponsors, sponsor_apis, judging_criteria, audience_region)."); return
            draft = self.state.setdefault("drafts", {}).setdefault(str(user_id), {"text": "", "parsed": {}})
            draft.setdefault("overrides", {})[fields[1]] = fields[2]
            draft["reviewed"] = False; self._save_state(); self.send(chat_id, f"Updated {fields[1]}; use /review."); return
        if command == "/review":
            draft = self.state.get("drafts", {}).get(str(user_id))
            if not draft or not draft.get("text"):
                self.send(chat_id, "Send the hackathon brief first; parts are combined until you use /review."); return
            try:
                cfg = Config.from_env(depth=draft.get("depth"))
                from hackidea.context import parse_fields
                parsed = parse_fields(Runner(cfg), draft["text"])
                parsed.update(self._overrides(draft.get("overrides", {})))
                draft["parsed"] = parsed; draft["reviewed"] = True; self._save_state()
                self.send(chat_id, self._review(parsed, draft.get("depth", cfg.depth)))
            except Exception as exc:
                self.send(chat_id, f"Could not parse the brief ({type(exc).__name__}). Add more detail and retry /review.")
            return
        if command == "/confirm":
            draft = self.state.get("drafts", {}).get(str(user_id), {})
            parsed = draft.get("parsed", {})
            if draft.get("consumed"):
                self.send(chat_id, "This confirmed brief was already submitted. Send a new brief to start another run."); return
            if not draft.get("reviewed"):
                self.send(chat_id, "Use /review before confirming."); return
            missing = [k for k in ("theme", "duration_hours", "team") if not parsed.get(k)]
            if missing:
                self.send(chat_id, "Missing essentials: " + ", ".join(missing) + ". Set them with /set field value."); return
            with self._run_lock:
                if self._run_active:
                    self.send(chat_id, "A run is already in progress."); return
                self._run_active = True
            cfg = Config.from_env(depth=draft.get("depth"))
            self.state["active_run"] = {"chat_id": chat_id, "stage": "starting"}; self._save_state()
            draft["consumed"] = True; self._save_state()
            self.send(chat_id, f"Confirmed. Starting a {cfg.depth} run; /status shows progress and /cancel stops at a stage boundary.")
            threading.Thread(target=self._run_and_deliver, args=(chat_id, draft["text"], cfg, parsed), daemon=True).start()
            return
        if not text:
            return

        draft = self.state.setdefault("drafts", {}).setdefault(str(user_id), {"text": "", "depth": Config.from_env().depth})
        draft["text"] = (draft.get("text", "") + "\n\n" + text).strip()
        draft["reviewed"] = False; draft["consumed"] = False; self._save_state()
        self.send(chat_id, f"Added {len(text)} characters ({len(draft['text'])} total). Send more, then /review when complete.")

    @staticmethod
    def _overrides(overrides: dict) -> dict:
        out = {}
        for key, value in overrides.items():
            if key == "duration_hours":
                m = re.search(r"\d+(?:\.\d+)?", str(value))
                if m: out[key] = float(m.group())
            elif key == "team": out[key] = [{"role": x.strip(), "skills": []} for x in str(value).split(",") if x.strip()]
            elif key in ("sponsors", "sponsor_apis"): out[key] = [x.strip() for x in str(value).split(",") if x.strip()]
            else: out[key] = value
        return out

    @staticmethod
    def _review(ctx: dict, depth: str) -> str:
        return ("What I understood:\n"
                f"Theme: {ctx.get('theme') or 'MISSING'}\nDuration: {ctx.get('duration_hours') or 'MISSING'} hours\n"
                "Your team: " + (", ".join(x.get("role", "") for x in ctx.get("team", [])) or "MISSING") + "\n"
                "Sponsors: " + (", ".join(ctx.get("sponsors", [])) or "none found") + "\n"
                "Required APIs: " + (", ".join(ctx.get("sponsor_apis", [])) or "none found") + "\n"
                "Judging criteria: " + (", ".join(ctx.get("judging_criteria", {})) or "not found") + "\n"
                f"Depth: {depth}\nAssumptions: {'; '.join(ctx.get('assumptions', [])) or 'none'}\n"
                "Correct with /set field value; choose /depth; /confirm starts the run.")

    def _run_and_deliver(self, chat_id: int, text: str, cfg: Config, parsed=None) -> None:
        try:
            try:
                runner = Runner(cfg)
                self._runner = runner
                runner.new_run()
                self.state.setdefault("active_run", {}).update(stage="context", run_dir=str(runner.run_dir))
                self._save_state()
                runner.progress_callback = self._stage_progress
                runner.item_progress_callback = self._stage_progress
                run_dir = runner.run(context_text=text, parsed_context=parsed)
            except InterruptedError:
                self.send(chat_id, "Run cancelled at the stage boundary.")
                return
            except (Exception, SystemExit) as exc:
                print(f"[hackidea-telegram] run failed ({type(exc).__name__})", file=sys.stderr, flush=True)
                try:
                    self.send(chat_id, f"The run failed. {safe_failure_hint(exc)}")
                except (Exception, SystemExit) as send_exc:
                    print(f"[hackidea-telegram] failure notice delivery failed ({type(send_exc).__name__})", file=sys.stderr, flush=True)
                return

            report_path = run_dir / "final_report.md"
            pdf_path = run_dir / "final_report.pdf"
            if not report_path.exists() or not pdf_path.exists():
                self.send(chat_id, "The run finished, but no report file was written.")
                return
            try:
                self.state["last_report"] = str(pdf_path)
                self.state["last_markdown"] = str(report_path); self._save_state()
                self.state["last_run_dir"] = str(run_dir); self._save_state()
                self.send(chat_id, self._summary(run_dir))
                self.send_document(chat_id, pdf_path, "Your HackIdea build plan is ready.")
            except (Exception, SystemExit) as exc:
                # The pipeline succeeded; keep that distinct from a Telegram upload failure.
                reason = safe_telegram_failure_hint(exc)
                print(f"[hackidea-telegram] report delivery failed ({reason})", file=sys.stderr, flush=True)
                try:
                    self.send(chat_id, f"The run completed, but I couldn't attach the report ({reason}). Here it is as text instead:")
                    self.send(chat_id, report_path.read_text(encoding="utf-8"))
                except (Exception, SystemExit) as send_exc:
                    print(f"[hackidea-telegram] text fallback failed ({type(send_exc).__name__})", file=sys.stderr, flush=True)
        except (Exception, SystemExit) as exc:
            print(f"[hackidea-telegram] run delivery failed ({type(exc).__name__})", file=sys.stderr, flush=True)
        finally:
            with self._run_lock:
                self._run_active = False
                self._cancel_requested = False
                self._runner = None
                self.state.pop("active_run", None); self._save_state()

    def _alternative_blueprint(self, chat_id: int, source: Path, candidate_id: str):
        target = source.parent / f"{source.name}_alternative_{candidate_id}"
        try:
            shutil.copytree(source, target, dirs_exist_ok=True)
            decision_path = target / "decision.json"
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["winner_id"] = candidate_id
            decision_path.write_text(json.dumps(decision, indent=2), encoding="utf-8")
            Runner(Config.from_env(), run_dir=target).run(resume_from="blueprint", only_stage="blueprint")
            report = target / "final_report.md"
            alt_report = target / f"alternative_{candidate_id}_blueprint.md"
            report.replace(alt_report)
            pdf = target / "final_report.pdf"
            alt_pdf = target / f"alternative_{candidate_id}_blueprint.pdf"
            pdf.replace(alt_pdf)
            self.send_document(chat_id, alt_pdf, f"Alternative blueprint for {candidate_id}.")
        except Exception as exc:
            self.send(chat_id, f"Alternative blueprint failed ({type(exc).__name__}).")

    def _stage_progress(self, stage: str, number: int, total: int, within_stage: bool = False) -> None:
        active = self.state.setdefault("active_run", {})
        active["stage"] = stage
        if within_stage:
            active["items_done"] = number
            active["items_total"] = total
            self._save_state()
        else:
            active.pop("items_done", None); active.pop("items_total", None)
            self._save_state()
            self.send(active["chat_id"], f"Completed stage {number}/{total}: {stage}.")

    @staticmethod
    def _summary(run_dir: Path) -> str:
        try:
            decision = json.loads((run_dir / "decision.json").read_text(encoding="utf-8"))
            candidates = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))["candidates"]
            winner = next(c for c in candidates if c["id"] == decision["winner_id"])
            reviews = json.loads((run_dir / "redteam.json").read_text(encoding="utf-8"))["reviews"]
            review = next(r for r in reviews if r["id"] == winner["id"])
            risks = "; ".join(r.get("risk", "") for r in review.get("risks", [])[:2]) or "none recorded"
            return f"Winner: {winner['name']}\n{winner.get('pitch','')}\nConfidence: {decision['confidence_level']} ({decision['confidence_pct']}%)\nTop risks: {risks}"
        except Exception:
            return "Your HackIdea run is complete."

    def run_forever(self) -> None:
        print("[hackidea-telegram] polling for Telegram messages", file=sys.stderr, flush=True)
        offset = int(self.state.get("last_update_id", -1)) + 1
        while True:
            try:
                updates = self.api("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
                for update in updates:
                    offset = update["update_id"] + 1
                    try:
                        self.handle(update)
                    except requests.RequestException as exc:
                        # Request errors may include the token-bearing Telegram URL.
                        print(f"[hackidea-telegram] Telegram request failed ({type(exc).__name__})", file=sys.stderr, flush=True)
                    except Exception as exc:
                        print(f"[hackidea-telegram] update failed ({type(exc).__name__})", file=sys.stderr, flush=True)
            except (requests.RequestException, RuntimeError) as exc:
                print(f"[hackidea-telegram] polling failed ({type(exc).__name__}); retrying in 5 seconds", file=sys.stderr, flush=True)
                time.sleep(5)


def main() -> int:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env before starting the Telegram bot.", file=sys.stderr)
        return 2
    raw_ids = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    try:
        allowed_user_ids = {int(value.strip()) for value in raw_ids.split(",") if value.strip()}
    except ValueError:
        print("TELEGRAM_ALLOWED_USER_IDS must be comma-separated numeric Telegram user IDs.", file=sys.stderr)
        return 2
    if not allowed_user_ids:
        print("No allowed users configured. The bot will answer /id and help, but will not start pipeline runs until TELEGRAM_ALLOWED_USER_IDS is set.", file=sys.stderr)
    try:
        TelegramBot(token, allowed_user_ids).run_forever()
    except KeyboardInterrupt:
        print("\n[hackidea-telegram] stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
