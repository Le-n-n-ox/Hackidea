import json
from pathlib import Path

from hackidea.config import Config
from hackidea.context import parse_fields
from hackidea.llm import MockLLM
from hackidea.telegram import TelegramBot


class FakeTelegram(TelegramBot):
    def __init__(self, tmp_path):
        super().__init__("fake", {7})
        self._state_path = tmp_path / "telegram.json"
        self.state = {"drafts": {}, "last_update_id": -1}
        self.messages = []
        self.documents = []

    def send(self, chat_id, message):
        self.messages.append((chat_id, message))

    def send_document(self, chat_id, path, caption=""):
        self.documents.append((chat_id, str(path), caption))


def update(update_id, text):
    return {"update_id": update_id, "message": {
        "text": text, "chat": {"id": 10, "type": "private"}, "from": {"id": 7}}}


def test_intake_merges_parts_and_does_not_start_on_greeting(tmp_path):
    bot = FakeTelegram(tmp_path)
    bot.handle(update(1, "hello"))
    bot.handle(update(2, "THEME: Education"))
    draft = bot.state["drafts"]["7"]
    assert "hello" in draft["text"] and "THEME: Education" in draft["text"]
    assert not bot._run_active
    assert "Added" in bot.messages[-1][1]


def test_restart_dedupes_telegram_updates(tmp_path):
    bot = FakeTelegram(tmp_path)
    bot.handle(update(8, "brief part"))
    bot.handle(update(8, "brief part"))
    assert bot.state["drafts"]["7"]["text"].count("brief part") == 1


def test_command_handlers_depth_status_cancel(tmp_path):
    bot = FakeTelegram(tmp_path)
    bot.handle(update(1, "/depth quick"))
    assert bot.state["drafts"]["7"]["depth"] == "quick"
    bot.handle(update(2, "/status"))
    assert bot.messages[-1][1] == "No run is active."
    bot.handle(update(3, "/cancel"))
    assert "cleared" in bot.messages[-1][1]


def test_pdf_delivery_uses_pdf_mime_with_fake_telegram_api(tmp_path):
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"ok": True}

    class FakeAPI:
        def __init__(self):
            self.calls = []
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    bot = FakeTelegram(tmp_path)
    fake_api = FakeAPI()
    bot._sessions.session = fake_api
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-fake")
    TelegramBot.send_document(bot, 10, pdf, "Plan")
    upload = fake_api.calls[0][1]
    assert upload["files"]["document"][0] == "plan.pdf"
    assert upload["files"]["document"][2] == "application/pdf"


def test_context_parser_separates_attendees_and_infers_api_sponsor(tmp_path):
    brief = Path(__file__).parent / "fixtures" / "hospitality_brief.txt"
    text = brief.read_text(encoding="utf-8")
    reply = {"theme": "Hospitality & Tourism", "team": [{"role": "Developers & Engineers", "skills": []}],
             "sponsors": [], "sponsor_apis": [], "judging_criteria": {}, "duration_hours": 12,
             "tracks": [], "preferred_tech": [], "banned_tech": [], "budget_constraints": "",
             "audience_region": "East Africa", "extra_notes": ""}
    cfg = Config(runs_dir=str(tmp_path / "runs"), cache_dir=str(tmp_path / "cache"))
    parsed = parse_fields(type("R", (), {"llm": MockLLM({"context": reply})})(), text)
    assert parsed["team"] == []
    assert "Africa's Talking" in parsed["sponsors"]


def test_context_parser_does_not_turn_education_audience_into_team(tmp_path):
    text = (Path(__file__).parent / "fixtures" / "edtech_brief.txt").read_text(encoding="utf-8")
    reply = {"theme": "Edtech Solutions", "team": [{"role": "Teachers", "skills": []}],
             "sponsors": [], "sponsor_apis": [], "judging_criteria": {}, "duration_hours": 11,
             "tracks": [], "preferred_tech": [], "banned_tech": [], "budget_constraints": "",
             "audience_region": "Nairobi", "extra_notes": ""}
    parsed = parse_fields(type("R", (), {"llm": MockLLM({"context": reply})})(), text)
    assert parsed["team"] == []
    assert "Africa's Talking" in parsed["sponsors"]

