"""Pluggable LLM provider (OpenAI-compatible HTTP) + robust JSON extraction/validation/retry."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import requests

from hackidea import schemas


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Pull the first valid JSON object/array out of an LLM reply.

    Handles: bare JSON, ```json fences, prose before/after, trailing commas tolerated
    only insofar as json.loads allows (we do NOT silently 'repair' beyond whitespace).
    Raises ValueError if nothing parseable is found.
    """
    if text is None:
        raise ValueError("empty LLM response")
    text = text.strip()
    # 1) whole thing
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2) fenced blocks
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
    # 3) balanced scan from first { or [
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(opener, start + 1)
    raise ValueError("no parseable JSON found in LLM response")


class BaseLLM:
    """Shared JSON-with-validation-and-retry loop on top of chat()."""

    def chat(self, messages: list[dict], max_tokens: int | None = None,
             temperature: float = 0.7, seed: int | None = None) -> str:
        raise NotImplementedError

    def complete_json(self, system: str, user: str, schema: dict, *,
                      stage: str = "", max_tokens: int | None = None,
                      temperature: float = 0.4, seed: int | None = None,
                      tries: int = 3) -> dict:
        """Ask for JSON, extract, validate against schema, retry with error feedback."""
        marker = f"\n[STAGE:{stage}]" if stage else ""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user + marker +
             "\n\nRespond with ONLY valid JSON matching the requested shape. No prose, no markdown fences."},
        ]
        last_err: Exception | None = None
        for _ in range(tries):
            chat_options = {}
            if getattr(self, "supports_json_schema", False):
                # NVIDIA's supported endpoints accept OpenAI-compatible JSON
                # Schema output. Keep strict=False because pipeline schemas
                # intentionally include optional fields and open-ended maps.
                name = re.sub(r"[^a-zA-Z0-9_-]", "_", stage) or "hackidea_response"
                chat_options["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": name[:64], "strict": False, "schema": schema},
                }
                if getattr(self, "needs_reasoning_headroom", False):
                    # Reasoning tokens count against max_tokens on this endpoint.
                    # Small stage caps can truncate otherwise valid JSON before
                    # the answer body is complete.
                    max_tokens = max(max_tokens or 0, 4000)
            elif getattr(self, "supports_json_mode", False):
                chat_options["response_format"] = {"type": "json_object"}
            raw = self.chat(messages, max_tokens=max_tokens, temperature=temperature,
                            seed=seed, **chat_options)
            try:
                data = extract_json(raw)
                schemas.validate(data, schema)
                return data
            except (ValueError, schemas.jsonschema.ValidationError) as e:
                last_err = e
                messages.append({"role": "assistant", "content": raw[:6000]})
                messages.append({"role": "user", "content":
                                 f"Your reply was invalid: {str(e)[:400]}. "
                                 "Return ONLY corrected JSON matching the requested shape."})
        raise LLMError(f"stage {stage}: JSON invalid after {tries} tries: {last_err}")


class OpenAICompatLLM(BaseLLM):
    """Works with any /v1/chat/completions endpoint: OpenAI, OpenRouter, Nous Portal
    via `hermes proxy`, Ollama, vLLM, etc. Provider swap = env vars only."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session()
        self.request_count = 0
        self.uses_mistral_large_4 = (
            cfg.llm_base_url.startswith("https://integrate.api.nvidia.com")
            and cfg.llm_model == "mistralai/mistral-large-4-1t-a52b"
        )
        self.uses_nemotron_bolt = (
            cfg.llm_base_url.startswith("https://integrate.api.nvidia.com")
            and cfg.llm_model == "nvidia/nemotron-3.5-bolt-e4b"
        )
        self.supports_json_schema = cfg.llm_structured_output == "json_schema"
        self.supports_json_mode = cfg.llm_structured_output in ("json_schema", "json_object")
        self.needs_reasoning_headroom = self.uses_mistral_large_4

    def chat(self, messages: list[dict], max_tokens: int | None = None,
             temperature: float = 0.7, seed: int | None = None,
             response_format: dict | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self.cfg.llm_model,
            "messages": messages,
            "max_tokens": max_tokens or self.cfg.llm_max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if self.uses_mistral_large_4:
            # NVIDIA's Mistral Large 4 endpoint otherwise includes reasoning
            # tokens in the answer body; keep JSON pipeline calls concise.
            payload["reasoning_effort"] = "low"
        # NVIDIA's Nemotron Nano Omni reasoning endpoint puts its internal
        # reasoning in `reasoning_content` and can leave `content` empty unless
        # thinking is disabled. HackIdea consumes the final answer only.
        if (self.uses_nemotron_bolt
                or (self.cfg.llm_base_url.startswith("https://integrate.api.nvidia.com")
                    and self.cfg.llm_model.startswith("nvidia/nemotron-3-nano-omni"))):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if seed is not None:
            payload["seed"] = seed
        url = f"{self.cfg.llm_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.cfg.llm_api_key or 'local'}",
                   "Content-Type": "application/json"}
        last_err: Exception | None = None
        for attempt in range(self.cfg.llm_max_retries + 1):
            try:
                self.request_count += 1
                resp = self.session.post(url, json=payload, headers=headers,
                                         timeout=self.cfg.llm_timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = float(resp.headers.get("Retry-After", 0)) or min(2 ** attempt * 3, 60)
                    last_err = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"].get("content") or ""
                if not content.strip():
                    raise LLMError("empty content in LLM response")
                return content
            except (requests.RequestException, LLMError, KeyError, ValueError) as e:
                last_err = e
                if attempt < self.cfg.llm_max_retries:
                    time.sleep(min(2 ** attempt * 3, 60))
        raise LLMError(f"LLM call failed after retries: {last_err}")


class MockLLM(BaseLLM):
    """Scripted LLM for tests. Responses keyed by the [STAGE:name] marker."""

    def __init__(self, scripts: dict[str, Any], default: Any = None):
        self.scripts = scripts
        self.default = default
        self.calls: list[str] = []

    def _pick(self, messages: list[dict]) -> str:
        text = "\n".join(m.get("content", "") for m in messages)
        m = re.findall(r"\[STAGE:(\w+)\]", text)
        stage = m[-1] if m else ""
        self.calls.append(stage)
        val = self.scripts.get(stage, self.default)
        if val is None:
            raise LLMError(f"MockLLM has no script for stage {stage!r}")
        if callable(val):
            val = val(text)
        return val if isinstance(val, str) else json.dumps(val)

    def chat(self, messages, max_tokens=None, temperature=0.7, seed=None,
             response_format=None):
        return self._pick(messages)


def make_llm(cfg, mock: MockLLM | None = None):
    if mock is not None:
        return mock
    if cfg.llm_provider in ("openai", "openai_compat", "hermes-proxy", "ollama"):
        return OpenAICompatLLM(cfg)
    raise ValueError(f"unknown LLM_PROVIDER {cfg.llm_provider!r} (use openai-compatible env settings)")
