"""Finite, phase-routed LLM scripts for offline protocol tests.

Every request must match exactly one route and consume one explicitly scripted
response. Call ``assert_consumed()`` after exercising the application: it also
reports protocol failures that application fallback code may have swallowed.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from typing import Any, NoReturn

from llm.provider import LLMProvider, _parse_json_object

Request = dict[str, Any]
Route = Callable[[Request], bool]
Response = Any  # Text, an explicit JSON value, or a request -> value callback.


class StrictScriptedProvider(LLMProvider):
    """Dispatch requests to independent, non-cycling response queues.

    ``routes`` and ``scripts`` must name the same phases. Predicates and dynamic
    responses receive independent snapshots of ``{"messages": ..., "options":
    ...}``; mutating them cannot rewrite the request or another callback's input.
    ``calls`` records those fields plus the uniquely matched ``phase`` (otherwise
    ``None``), including requests that fail. ``consumed`` and ``remaining`` are
    per-phase count snapshots. There are no implicit responses or retries.
    """

    def __init__(self, routes: dict[str, Route],
                 scripts: dict[str, list[Response]]):
        if routes.keys() != scripts.keys():
            raise AssertionError(
                "Route/script phases differ: "
                f"routes without scripts={sorted(routes.keys() - scripts.keys())!r}; "
                f"scripts without routes={sorted(scripts.keys() - routes.keys())!r}")
        self._routes = dict(routes)
        self._scripts = {phase: deque(deepcopy(responses))
                         for phase, responses in scripts.items()}
        self._consumed = dict.fromkeys(routes, 0)
        self._errors: list[str] = []
        self.calls: list[dict[str, Any]] = []

    @property
    def consumed(self) -> dict[str, int]:
        return dict(self._consumed)

    @property
    def remaining(self) -> dict[str, int]:
        return {phase: len(responses) for phase, responses in self._scripts.items()}

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(self._errors)

    def _fail(self, message: str) -> NoReturn:
        self._errors.append(message)
        raise AssertionError(message)

    def supports_tools(self) -> bool:
        return False

    def complete(self, system: str, user: str, **kwargs) -> str:
        return self.complete_messages([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ], **kwargs)

    def complete_messages(self, messages: list[dict], **kwargs) -> str:
        request = {"messages": deepcopy(messages), "options": deepcopy(kwargs)}
        call = {**deepcopy(request), "phase": None}
        self.calls.append(call)
        call_number = len(self.calls)
        matches = []
        for phase, predicate in self._routes.items():
            try:
                matched = predicate(deepcopy(request))
                if matched:
                    matches.append(phase)
            except Exception as exc:
                self._fail(f"Call {call_number}: route {phase!r} raised "
                           f"{type(exc).__name__}: {exc}")

        if not matches:
            self._fail(f"Call {call_number}: no route matched request {request!r}")
        if len(matches) != 1:
            self._fail(f"Call {call_number}: multiple routes matched {matches!r}")

        phase = matches[0]
        call["phase"] = phase
        if not self._scripts[phase]:
            self._fail(f"Call {call_number}: script for phase {phase!r} exhausted "
                       f"after {self._consumed[phase]} response(s)")
        response = self._scripts[phase].popleft()
        self._consumed[phase] += 1
        try:
            if callable(response):
                response = response(deepcopy(request))
            return response if isinstance(response, str) else json.dumps(
                response, ensure_ascii=False)
        except Exception as exc:
            self._fail(f"Call {call_number}: response for phase {phase!r} raised "
                       f"{type(exc).__name__}: {exc}")

    def complete_json(self, system: str, user: str, schema: dict, **kwargs) -> dict:
        """Use the normal provider parser, with exactly one scripted request.

        Deliberately malformed scripted text can raise ``ValueError`` here; it
        is not a protocol error. Any application retry needs its own response.
        """
        raw = self.complete(system, user, **kwargs)
        result = _parse_json_object(raw)
        if result is None:
            raise ValueError(f"Scripted response is not a JSON object: {raw!r}")
        return result

    def assert_consumed(self) -> None:
        """Fail for any unused response or previously raised protocol error."""
        problems = list(self._errors)
        for phase, remaining in self.remaining.items():
            if remaining:
                problems.append(f"Phase {phase!r}: {remaining} unconsumed "
                                f"response(s), {self._consumed[phase]} consumed")
        if problems:
            raise AssertionError("Strict scripted provider verification failed:\n"
                                 + "\n".join(problems))
