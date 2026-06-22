"""LLMProvider ABC, FakeLLMProvider, real adapters, and make_provider switchboard.

Adapters use stdlib urllib.request (NO openai/anthropic/zhipu SDKs).
Adapters are unit-testable offline via _build_request() without live HTTP calls.
"""

from __future__ import annotations

import abc
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from engine.log import get_logger
from kernel.observability import get_tracer

log = get_logger("llm")


def _record_usage(gen, parsed: dict) -> None:
    """Push usage AND the completion text onto a generation handle. Never raises."""
    try:
        usage = parsed.get("usage") or {}
        norm = {
            "input": usage.get("prompt_tokens") if usage.get("prompt_tokens") is not None
                     else usage.get("input_tokens"),
            "output": usage.get("completion_tokens") if usage.get("completion_tokens") is not None
                      else usage.get("output_tokens"),
            "total": usage.get("total_tokens"),
        }
        norm = {k: v for k, v in norm.items() if v is not None}
        # Output text: OpenAI/zhipu shape choices[0].message.content; fall back to the
        # whole message (tool-call turns have content=None) or the raw parsed object.
        out = None
        try:
            msg = (parsed.get("choices") or [{}])[0].get("message") or {}
            out = msg.get("content") or (json.dumps(msg, ensure_ascii=False) if msg else None)
        except Exception:
            out = None
        gen.finish(output=out, usage=norm or None)
    except Exception:
        log.debug("_record_usage failed (non-fatal)")


def _parse_json_object(raw: str) -> dict | None:
    """Parse a JSON object from an LLM response, tolerating ```json code fences
    and surrounding prose (LLMs add them despite instructions). Returns the
    dict, or None if no JSON object can be recovered."""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    candidates = [s]
    lo, hi = s.find("{"), s.rfind("}")
    if 0 <= lo < hi:
        candidates.append(s[lo:hi + 1])  # outermost {...} if prose surrounds it
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class LLMProvider(abc.ABC):
    """Provider-agnostic LLM interface.

    complete(system, user, *, model=None, max_tokens=None) -> str
        max_tokens=None falls back to the provider's instance default
        (DEFAULT_MAX_TOKENS). Reasoning models (e.g. glm-5.1) spend
        completion tokens on hidden reasoning before emitting content, so the
        default must be generous or content comes back empty/truncated.
    complete_json(system, user, schema, **kw) -> dict
        Calls complete + parses JSON; retries once on parse failure.
    """

    @abc.abstractmethod
    def complete(self, system: str, user: str, *,
                 model: str | None = None, max_tokens: int | None = None) -> str:
        """Return a text completion."""

    def complete_json(self, system: str, user: str, schema: dict,
                      **kw) -> dict:
        """Return a parsed JSON dict; retries once on bad JSON.

        Raises ValueError if both attempts fail to produce valid JSON.
        """
        last_raw = ""
        for attempt in range(2):
            raw = self.complete(system, user, **kw)
            last_raw = raw
            result = _parse_json_object(raw)
            if result is not None:
                return result
            log.debug("complete_json parse failure attempt=%d raw=%r", attempt, raw[:80])
        raise ValueError(
            f"complete_json failed after 2 attempts; last raw: {last_raw!r:.120}")

    def complete_messages(self, messages: list[dict], *,
                          model: str | None = None, max_tokens: int | None = None) -> str:
        """Multi-turn completion: send a full role/content message list, return the
        assistant text. This is what the conversational repair loop uses, so the
        model sees its own prior output + the validation errors and fixes
        incrementally — instead of re-prompting blind each round."""
        raise NotImplementedError

    def supports_tools(self) -> bool:
        """Whether this provider implements complete_with_tools (the tool loop)."""
        return False

    def complete_with_tools(self, messages: list[dict], tools: list[dict],
                            tool_executor, *, model: str | None = None,
                            max_tokens: int | None = None,
                            max_tool_rounds: int = 3) -> str:
        """Research-then-write tool loop: see _run_tool_loop. Default: unsupported."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fake provider (offline, deterministic, for tests)
# ---------------------------------------------------------------------------

class FakeLLMProvider(LLMProvider):
    """Deterministic no-network provider for tests.

    responses       – list of text strings cycled for complete()
    json_responses  – list of dicts cycled for complete_json() (bypasses JSON parsing)

    If both are None, complete() echoes the user prompt.
    Calls are recorded in self.calls as (system, user) tuples.
    """

    def __init__(self, responses: list[str] | None = None,
                 json_responses: list[dict] | None = None):
        self._responses = responses
        self._json_responses = json_responses
        self._resp_idx = 0
        self._json_idx = 0
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *,
                 model: str | None = None, max_tokens: int = 1024) -> str:
        self.calls.append((system, user))
        if self._responses is not None:
            text = self._responses[self._resp_idx % len(self._responses)]
            self._resp_idx += 1
            log.debug("FakeLLMProvider.complete idx=%d → %r", self._resp_idx - 1, text[:40])
            return text
        # echo mode
        return f"[echo] {user}"

    def complete_json(self, system: str, user: str, schema: dict, **kw) -> dict:
        self.calls.append((system, user))
        if self._json_responses is not None:
            result = self._json_responses[self._json_idx % len(self._json_responses)]
            self._json_idx += 1
            log.debug("FakeLLMProvider.complete_json idx=%d", self._json_idx - 1)
            return result
        # Fall through to text-based complete + JSON parse (uses parent complete_json logic
        # but we need to avoid double-recording; call super which calls self.complete)
        # Pop the call we just recorded, let super's complete() re-record it.
        self.calls.pop()
        return super().complete_json(system, user, schema, **kw)

    def complete_messages(self, messages: list[dict], *,
                          model: str | None = None, max_tokens: int | None = None) -> str:
        # Record (system, last-user-content) so existing call-inspection still works.
        system = next((m.get("content", "") for m in messages
                       if m.get("role") == "system"), "")
        last_user = next((m.get("content", "") for m in reversed(messages)
                          if m.get("role") == "user"), "")
        self.calls.append((system, last_user))
        if self._json_responses is not None:
            result = self._json_responses[self._json_idx % len(self._json_responses)]
            self._json_idx += 1
            return json.dumps(result, ensure_ascii=False)
        if self._responses is not None:
            text = self._responses[self._resp_idx % len(self._responses)]
            self._resp_idx += 1
            return text
        return f"[echo] {last_user}"


class ScriptedToolProvider(FakeLLMProvider):
    """Offline-deterministic tool-loop fake. `script` is a list of assistant
    turns: {"tool_calls":[{"name","arguments"}...]} (executed, then continue)
    or {"content": "<final>"} (returned). Records (name, arguments) the model
    emitted in self.tool_invocations. NO network. See plan DECISION-2."""

    def __init__(self, *, script: list[dict], **kw):
        super().__init__(**kw)
        self._script = list(script)
        self.tool_invocations: list[tuple[str, dict]] = []

    def supports_tools(self) -> bool:
        return True

    def complete_with_tools(self, messages, tools, tool_executor, *,
                            model=None, max_tokens=None, max_tool_rounds=3):
        rounds = 0
        for turn in self._script:
            if "content" in turn:
                return turn["content"]
            if rounds >= max_tool_rounds:
                break  # cap hit: fall through to forced-final
            for call in turn.get("tool_calls", []):
                name, args = call["name"], call.get("arguments", {})
                self.tool_invocations.append((name, args))
                tool_executor(name, args)  # drive the REAL executor (records/side-effects)
            rounds += 1
        # Forced-final: return the first content turn if any, else empty string.
        for turn in self._script:
            if "content" in turn:
                return turn["content"]
        return ""


# ---------------------------------------------------------------------------
# Tool-loop orchestrator (pure; no urllib; adapters inject post/parse)
# ---------------------------------------------------------------------------

def _run_tool_loop(
    messages: list[dict],
    tools: list[dict],
    tool_executor,
    *,
    post,
    parse,
    append_result,
    max_tool_rounds: int = 3,
) -> str:
    """Drive a research-then-write tool loop.

    messages:       mutable conversation (role/content[/tool_calls/tool_call_id]).
    tools:          the provider-shaped `tools` schema array.
    tool_executor:  callable(name:str, arguments:dict) -> str (JSON string).
    post(messages, tools) -> dict:  the ONLY I/O seam (real: _do_post wrapper;
                    test: a fake returning scripted dicts).
    parse(resp) -> (text:str|None, tool_calls:list[dict]):  normalize the
                    provider response. tool_call dict: {"id","name","arguments"}.
    append_result(messages, call, result_str):  append assistant echo + tool
                    result in provider-specific shape (OpenAI vs Anthropic).
    max_tool_rounds: cap on tool-emitting rounds; on cap, ONE final post with
                    tools=None forces a textual answer. log.warning on cap.

    Returns the final assistant text.
    """
    rounds = 0
    while True:
        if rounds >= max_tool_rounds:
            # Cap hit: force one final call with no tools so the model must answer.
            log.warning("tool loop hit max_tool_rounds=%d; forcing final", max_tool_rounds)
            resp = post(messages, None)
            text, _ = parse(resp)
            return text or ""

        resp = post(messages, tools)
        text, tool_calls = parse(resp)

        if not tool_calls:
            # Model returned a normal answer — done.
            return text or ""

        # Execute each tool call and feed results back into messages.
        for call in tool_calls:
            with get_tracer().span("tool", tool_name=call["name"]):
                result_str = tool_executor(call["name"], call["arguments"])
            append_result(messages, call, result_str)

        rounds += 1


# ---------------------------------------------------------------------------
# Real adapters (urllib, unit-testable via _build_request, no live calls in tests)
# ---------------------------------------------------------------------------

def _http_post_json(url: str, headers: dict, data: bytes, timeout: int) -> dict:
    """Execute a single HTTP POST and return the parsed JSON body.

    This is the only urllib I/O seam in the codebase, intentionally thin so
    tests can monkeypatch it without touching the retry/tracing logic in
    _do_post.
    """
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _do_post(url: str, headers: dict, body: dict, timeout: int = 300,
             *, max_retries: int = 4) -> dict:
    """Perform a JSON POST and return the parsed response body.

    timeout defaults to 300s because reasoning models (glm-4.7/5.1) hold the
    (non-streaming) connection open while they think + write chapter-length
    output; the old 30s default timed out mid-generation. For full 32K-token
    turns, prefer streaming or raise this further.
    """
    data = json.dumps(body).encode("utf-8")
    # One generation observation per logical LLM call (spanning any retries).
    # NoopTracer offline → zero overhead; LangfuseTracer with creds → full
    # trace + token/cost, auto-nested under the active "turn" span.
    with get_tracer().generation("llm", model=body.get("model"),
                                 max_tokens=body.get("max_tokens"),
                                 input=body.get("messages")) as gen:
        for attempt in range(max_retries + 1):
            try:
                parsed = _http_post_json(url, headers, data, timeout)
                _record_usage(gen, parsed)
                return parsed
            except urllib.error.HTTPError as e:
                # 429/5xx are transient (rate limit, busy model); back off and retry
                # so a burst (compare run) or a popular model (glm-5.1) can't kill the turn.
                if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = min(2 ** attempt, 30)  # 1,2,4,8,... capped at 30s
                    log.warning("_do_post HTTP %d (%s); retry %d/%d after %ds",
                                e.code, e.reason, attempt + 1, max_retries, wait)
                    time.sleep(wait)
                    continue
                raise
            except (TimeoutError, urllib.error.URLError) as e:
                # Transient read-timeout / connection error (a slow glm-5.1 generation,
                # API congestion, or a dropped connection). HTTPError is handled above;
                # this catches socket read-timeouts (live-caught: a 300s read timeout
                # killed turn 5 of a long session) and connection-level failures. Retry
                # with the same backoff so one flaky call can't kill a long session.
                if attempt < max_retries:
                    wait = min(2 ** attempt, 30)
                    log.warning("_do_post network error (%s); retry %d/%d after %ds",
                                e, attempt + 1, max_retries, wait)
                    time.sleep(wait)
                    continue
                raise


def _openai_chat_body(model: str, messages: list[dict], max_tokens: int,
                      tools: list[dict] | None = None) -> dict:
    """OpenAI-compatible chat/completions body for a full message list.

    tools: when non-None, adds the 'tools' key to the body (function-calling).
    """
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if tools is not None:
        body["tools"] = tools
    return body


def _openai_parse(resp: dict) -> tuple[str | None, list[dict]]:
    """Parse an OpenAI/GLM response into (final_text, tool_calls).

    tool_calls shape: [{"id", "name", "arguments": dict}]
    Returns (None, tool_calls) when the model emits tool calls,
    (text, []) when the model returns a normal content response.
    """
    choice = resp["choices"][0]
    msg = choice["message"]
    if choice.get("finish_reason") == "tool_calls" and msg.get("tool_calls"):
        calls = []
        for tc in msg["tool_calls"]:
            fn = tc["function"]
            calls.append({
                "id": tc["id"],
                "name": fn["name"],
                "arguments": json.loads(fn.get("arguments") or "{}"),
                "arguments_raw": fn.get("arguments") or "{}",
            })
        return None, calls
    return msg.get("content"), []


def _openai_append_result(messages: list[dict], call: dict, result_str: str) -> None:
    """Append the assistant tool-call echo + tool result to messages (OpenAI shape).

    OpenAI requires the assistant message echoing the tool_calls to appear
    BEFORE the tool result message.
    """
    messages.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call["id"],
            "type": "function",
            "function": {"name": call["name"], "arguments": call.get("arguments_raw", "{}")},
        }],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": call["id"],
        "content": result_str,
    })


class OpenAIProvider(LLMProvider):
    """OpenAI chat/completions adapter (stdlib urllib, OpenAI-compatible API)."""

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MAX_TOKENS = 8192

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 max_tokens: int | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS

    def _build_request(self, system: str, user: str, *,
                       max_tokens: int = 1024,
                       model: str | None = None) -> tuple[str, dict, dict]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return url, headers, body

    def complete(self, system: str, user: str, *,
                 model: str | None = None, max_tokens: int | None = None) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        url, headers, body = self._build_request(system, user,
                                                  max_tokens=mt, model=model)
        log.debug("OpenAIProvider.complete url=%s model=%s max_tokens=%d", url, body["model"], mt)
        resp = _do_post(url, headers, body)
        return resp["choices"][0]["message"]["content"]

    def complete_messages(self, messages: list[dict], *,
                          model: str | None = None, max_tokens: int | None = None) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = _openai_chat_body(model or self.model, messages, mt)
        log.debug("OpenAIProvider.complete_messages url=%s msgs=%d max_tokens=%d", url, len(messages), mt)
        resp = _do_post(url, headers, body)
        return resp["choices"][0]["message"]["content"]

    def supports_tools(self) -> bool:
        return True

    def complete_with_tools(self, messages: list[dict], tools: list[dict],
                            tool_executor, *, model: str | None = None,
                            max_tokens: int | None = None,
                            max_tool_rounds: int = 3) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        m = model or self.model

        def post(msgs, tls):
            body = _openai_chat_body(m, msgs, mt, tools=tls)
            return _do_post(url, headers, body)

        with get_tracer().span("tool_loop"):
            return _run_tool_loop(
                messages, tools, tool_executor,
                post=post, parse=_openai_parse,
                append_result=_openai_append_result,
                max_tool_rounds=max_tool_rounds,
            )


class ZhipuProvider(LLMProvider):
    """Zhipu AI (智谱) adapter — OpenAI-compatible API with Zhipu's base URL."""

    DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
    # glm-5.1 is a reasoning model writing chapter-length narration; reasoning
    # tokens count against this cap. Endpoint accepts >=131072; 32768 gives huge
    # headroom (the legacy skill capped chapters at 2000 content tokens, but that
    # was a non-thinking model — here reasoning stacks on top). finish_reason=stop
    # ends normal turns well short of the cap. Override via GLM_MAX_TOKENS.
    DEFAULT_MAX_TOKENS = 32768

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 max_tokens: int | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS

    def _build_request(self, system: str, user: str, *,
                       max_tokens: int = 1024,
                       model: str | None = None) -> tuple[str, dict, dict]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return url, headers, body

    def complete(self, system: str, user: str, *,
                 model: str | None = None, max_tokens: int | None = None) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        url, headers, body = self._build_request(system, user,
                                                  max_tokens=mt, model=model)
        log.debug("ZhipuProvider.complete url=%s model=%s max_tokens=%d", url, body["model"], mt)
        resp = _do_post(url, headers, body)
        return resp["choices"][0]["message"]["content"]

    def complete_messages(self, messages: list[dict], *,
                          model: str | None = None, max_tokens: int | None = None) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = _openai_chat_body(model or self.model, messages, mt)
        log.debug("ZhipuProvider.complete_messages url=%s msgs=%d max_tokens=%d", url, len(messages), mt)
        resp = _do_post(url, headers, body)
        return resp["choices"][0]["message"]["content"]

    def supports_tools(self) -> bool:
        return True

    def complete_with_tools(self, messages: list[dict], tools: list[dict],
                            tool_executor, *, model: str | None = None,
                            max_tokens: int | None = None,
                            max_tool_rounds: int = 3) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        m = model or self.model

        def post(msgs, tls):
            body = _openai_chat_body(m, msgs, mt, tools=tls)
            return _do_post(url, headers, body)

        with get_tracer().span("tool_loop"):
            return _run_tool_loop(
                messages, tools, tool_executor,
                post=post, parse=_openai_parse,
                append_result=_openai_append_result,
                max_tool_rounds=max_tool_rounds,
            )


class AnthropicProvider(LLMProvider):
    """Anthropic /v1/messages adapter (stdlib urllib)."""

    DEFAULT_BASE_URL = "https://api.anthropic.com"
    ANTHROPIC_VERSION = "2023-06-01"
    DEFAULT_MAX_TOKENS = 8192

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 max_tokens: int | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS

    def _build_request(self, system: str, user: str, *,
                       max_tokens: int = 1024,
                       model: str | None = None) -> tuple[str, dict, dict]:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
        body: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [
                {"role": "user", "content": user},
            ],
        }
        return url, headers, body

    def complete(self, system: str, user: str, *,
                 model: str | None = None, max_tokens: int | None = None) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        url, headers, body = self._build_request(system, user,
                                                  max_tokens=mt, model=model)
        log.debug("AnthropicProvider.complete url=%s model=%s max_tokens=%d", url, body["model"], mt)
        resp = _do_post(url, headers, body)
        return resp["content"][0]["text"]

    def complete_messages(self, messages: list[dict], *,
                          model: str | None = None, max_tokens: int | None = None) -> str:
        mt = max_tokens if max_tokens is not None else self.max_tokens
        system = "\n\n".join(m.get("content", "") for m in messages
                             if m.get("role") == "system")
        convo = [m for m in messages if m.get("role") != "system"]
        url = f"{self.base_url}/v1/messages"
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json",
                   "anthropic-version": self.ANTHROPIC_VERSION}
        body = {"model": model or self.model, "max_tokens": mt,
                "system": system, "messages": convo}
        log.debug("AnthropicProvider.complete_messages url=%s msgs=%d max_tokens=%d", url, len(convo), mt)
        resp = _do_post(url, headers, body)
        return resp["content"][0]["text"]


# ---------------------------------------------------------------------------
# Switchboard
# ---------------------------------------------------------------------------

_PROVIDER_MAP = {
    "fake": None,  # special-cased below
    "openai": OpenAIProvider,
    "zhipu": ZhipuProvider,
    "anthropic": AnthropicProvider,
}

_ENV_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def make_provider(kind: str, *, model: str | None,
                  base_url: str | None = None,
                  api_key: str | None = None,
                  max_tokens: int | None = None) -> LLMProvider:
    """Return a configured LLMProvider.

    kind: 'fake' | 'openai' | 'zhipu' | 'anthropic'
    api_key: falls back to env OPENAI_API_KEY / ZHIPU_API_KEY / ANTHROPIC_API_KEY
    max_tokens: per-call output-token cap (None → provider DEFAULT_MAX_TOKENS,
        8192). It caps OUTPUT only (not the context window); set it generous
        because reasoning models spend completion tokens on hidden reasoning
        before content. finish_reason=stop ends generation early, so a high cap
        costs nothing on normal turns — it only prevents truncation.
    """
    kind = kind.lower()
    if kind not in _PROVIDER_MAP:
        raise ValueError(f"Unknown provider kind: {kind!r}. "
                         f"Valid: {sorted(_PROVIDER_MAP)}")
    if kind == "fake":
        return FakeLLMProvider()

    cls = _PROVIDER_MAP[kind]
    resolved_key = api_key or os.environ.get(_ENV_KEY_MAP[kind], "")
    log.debug("make_provider kind=%s model=%s base_url=%s max_tokens=%s",
              kind, model, base_url, max_tokens)
    return cls(model=model, api_key=resolved_key, base_url=base_url,
               max_tokens=max_tokens)
