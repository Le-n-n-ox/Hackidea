import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hackidea.concurrency import ordered_fanout
from hackidea.config import Config
from hackidea.llm import BaseLLM, MockLLM, ProviderResponseError
from hackidea.search import MultiSearch, SearchHit
from hackidea.stages import ideation


def test_ordered_fanout_preserves_order_and_isolates_failures():
    def work(item):
        value, delay = item
        time.sleep(delay)
        if value == 2:
            raise ValueError("single item failed")
        return value

    out = ordered_fanout([(1, .04), (2, .01), (3, .02)], work, workers=3)
    assert [x.value for x in out] == [1, None, 3]
    assert isinstance(out[1].error, ValueError)


def test_ordered_fanout_stops_submitting_after_cancellation_and_joins_workers():
    cancelled = threading.Event()
    started = []
    lock = threading.Lock()

    def work(item):
        with lock:
            started.append(item)
        if item == 0:
            cancelled.set()
        time.sleep(.02)
        return item

    out = ordered_fanout(range(20), work, workers=2, cancelled=cancelled.is_set)
    assert len(started) < 20
    assert all((row.value == i and not row.cancelled) if i in started
               else (row.value is None and row.cancelled)
               for i, row in enumerate(out))


def test_mock_llm_enforces_request_cap_across_threads():
    lock = threading.Lock()
    active = 0
    peak = 0

    def response(_):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.025)
        with lock:
            active -= 1
        return {"ok": True}

    llm = MockLLM({"cap": response}, concurrency=2)
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    def call(i):
        return llm.complete_json("sys", f"request {i}", schema, stage="cap")
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(call, range(8)))
    assert all(row == {"ok": True} for row in rows)
    assert peak == 2
    assert llm.request_count == 8


class _RateLimitLLM(BaseLLM):
    def __init__(self):
        super().__init__(concurrency=2)
        self.cfg = type("Cfg", (), {"llm_max_retries": 2})()
        self.lock = threading.Lock()
        self.calls = 0
        self.first_429_at = None
        self.third_start = None

    def _chat(self, messages, **kwargs):
        with self.lock:
            self.calls += 1
            call = self.calls
            if call == 1:
                self.first_429_at = time.monotonic()
                raise ProviderResponseError(429, .12, "HTTP 429: quota")
            if call == 3:
                self.third_start = time.monotonic()
        time.sleep(.035)
        return '{"ok": true}'


def test_rate_limit_pauses_waiting_workers_and_retries_inside_chat():
    llm = _RateLimitLLM()
    llm.set_current_stage("rate")
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(llm.complete_json, "sys", "user", schema, stage="rate") for _ in range(3)]
        assert all(f.result() == {"ok": True} for f in futures)
    assert llm.third_start - llm.first_429_at >= .11
    assert llm.metrics_snapshot("rate")["rate_limits"] == 1
    assert llm.request_count == 4  # 3 calls plus one transparent 429 retry


def test_multisearch_fast_provider_survives_slow_provider_under_concurrent_queries():
    class Slow:
        def search(self, query, max_results=8):
            time.sleep(.25)
            return [SearchHit(f"https://slow.test/{query}", "slow", "slow")]

    class Fast:
        def search(self, query, max_results=8):
            return [SearchHit(f"https://fast.test/{query}", "fast", "fast")]

    multi = MultiSearch([("slow", Slow()), ("fast", Fast())], deadline=.08,
                        provider_interval=0, query_workers=4)
    started = time.monotonic()
    out = ordered_fanout([f"q{i}" for i in range(4)],
                         lambda q: multi.search(q), workers=4)
    elapsed = time.monotonic() - started
    assert elapsed < .18
    assert all(any(hit.url.startswith("https://fast.test/") for hit in row.value) for row in out)
    assert multi.metrics_snapshot("unknown")["deadline_cutoffs"] == 4
    time.sleep(.27)  # let the deliberately slow fake requests finish cleanly


class _IdeationRunner:
    def __init__(self, tmp_path):
        self.ctx = {"theme": "Tourism"}
        self.preset = Config(depth="quick").preset
        self.cfg = Config(depth="quick", llm_concurrency=4)
        self.run_dir = tmp_path
        self.llm = MockLLM({}, default=self._candidate, concurrency=4)
        self.cancel_requested = False
        self.logs = []

    def _candidate(self, text):
        import re
        angle = re.search(r"Required angle: (.+)", text).group(1).split("\n", 1)[0]
        return {"candidates": [{"name": "SameProduct", "pitch": "One product for rural users",
            "problem": "Rural operators struggle to coordinate travelers", "target_user": "rural operators",
            "ai_core": "agent workflow vision retrieval offline sensor accessible Africa developer code",
            "why_now": "A current need", "angle": angle, "data_needed": "sample records", "sponsor_fit": ""}]}

    def load(self, name):
        import json
        if name == "research_log.json":
            return {"sources": [{"url": "https://source.test/a", "title": "Tourism", "text": "tourism",
                                 "fetched": True, "relevant": True, "source_type": "local_context"}]}
        path = self.run_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"problems": []}

    def save(self, name, value):
        import json
        (self.run_dir / name).write_text(json.dumps(value), encoding="utf-8")

    def log(self, value):
        self.logs.append(value)

    def item_progress(self, *args):
        pass


def test_ideation_rounds_reject_duplicates_and_resume_progress(tmp_path):
    runner = _IdeationRunner(tmp_path)
    result = ideation.run(runner)
    assert len(result["candidates"]) == 1
    assert len({c["name"] for c in result["candidates"]}) == 1
    calls_after_first = runner.llm.request_count
    assert calls_after_first > 12  # duplicate slots were refilled in later rounds
    resumed = ideation.run(runner)
    assert resumed["candidates"] == result["candidates"]
    assert runner.llm.request_count == calls_after_first
