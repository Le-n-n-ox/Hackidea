"""Telegram long-polling interface for running HackIdea from a private chat."""
from __future__ import annotations

import os
import re
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


class TelegramBot:
    def __init__(self, token: str, allowed_user_ids: set[int]):
        self.token = token
        self.allowed_user_ids = allowed_user_ids
        self._sessions = threading.local()
        self._run_lock = threading.Lock()
        self._run_active = False

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
                files={"document": (path.name, document, "text/markdown")},
                timeout=60,
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram document upload failed"))

    @staticmethod
    def help_text() -> str:
        return (
            "Send one message describing your hackathon (theme, duration, team, "
            "sponsors, judging criteria, constraints). I’ll research it and return "
            "the winning idea and build blueprint.\n\n"
            "Commands:\n/start or /help — show this help\n/id — show your Telegram user ID\n"
            "Each new text message starts a new run. Set DEPTH=quick for a faster, "
            "lower-cost first run."
        )

    def handle(self, update: dict) -> None:
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
        if command == "/cancel":
            with self._run_lock:
                active = self._run_active
            if active:
                self.send(chat_id, "A run is in progress. It cannot be cancelled, but I’ll send the report when it finishes.")
            else:
                self.send(chat_id, "There is no active run to cancel.")
            return
        if not text:
            return
        if user_id not in self.allowed_user_ids:
            if not self.allowed_user_ids:
                self.send(chat_id, "Runs are disabled until the owner is configured. Send /id, then add that number to TELEGRAM_ALLOWED_USER_IDS in .env and restart the bot.")
            else:
                self.send(chat_id, "This bot is private. Your Telegram account is not on its allowed-user list.")
            return

        cfg = Config.from_env()
        with self._run_lock:
            active = self._run_active
            if not active:
                self._run_active = True
        if active:
            self.send(chat_id, "A HackIdea run is already in progress. I’ll send its report before starting another.")
            return
        try:
            self.send(chat_id, f"Got it. Starting the {cfg.depth} HackIdea run now. This can take several minutes; I’ll send the report when it’s ready.")
            worker = threading.Thread(
                target=self._run_and_deliver,
                args=(chat_id, text, cfg),
                name="hackidea-run",
                daemon=True,
            )
            worker.start()
        except Exception:
            with self._run_lock:
                self._run_active = False
            raise

    def _run_and_deliver(self, chat_id: int, text: str, cfg: Config) -> None:
        try:
            try:
                run_dir = Runner(cfg).run(context_text=text)
            except (Exception, SystemExit) as exc:
                print(f"[hackidea-telegram] run failed ({type(exc).__name__})", file=sys.stderr, flush=True)
                try:
                    self.send(chat_id, f"The run failed. {safe_failure_hint(exc)}")
                except (Exception, SystemExit) as send_exc:
                    print(f"[hackidea-telegram] failure notice delivery failed ({type(send_exc).__name__})", file=sys.stderr, flush=True)
                return

            report_path = run_dir / "final_report.md"
            if not report_path.exists():
                self.send(chat_id, "The run finished, but no report file was written.")
                return
            try:
                self.send_document(chat_id, report_path, "Your HackIdea report is ready.")
            except (Exception, SystemExit) as exc:
                # The pipeline succeeded; keep that distinct from a Telegram upload failure.
                print(f"[hackidea-telegram] report delivery failed ({type(exc).__name__})", file=sys.stderr, flush=True)
                try:
                    self.send(chat_id, "The run completed, but I couldn't attach the report. Here it is as text instead:")
                    self.send(chat_id, report_path.read_text(encoding="utf-8"))
                except (Exception, SystemExit) as send_exc:
                    print(f"[hackidea-telegram] text fallback failed ({type(send_exc).__name__})", file=sys.stderr, flush=True)
        except (Exception, SystemExit) as exc:
            print(f"[hackidea-telegram] run delivery failed ({type(exc).__name__})", file=sys.stderr, flush=True)
        finally:
            with self._run_lock:
                self._run_active = False

    def run_forever(self) -> None:
        print("[hackidea-telegram] polling for Telegram messages", file=sys.stderr, flush=True)
        offset = None
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
