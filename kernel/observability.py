from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager

from engine.log import get_logger

log = get_logger("kernel.observability")


class _NoopGen:
    """Inert generation handle: finish() accepts usage/output and does nothing."""

    def finish(self, *, output=None, usage=None):
        pass


class NoopTracer:
    """Tracer used offline / in tests: every method is inert."""

    @contextmanager
    def span(self, name, **attrs):
        yield None

    @contextmanager
    def generation(self, name, **attrs):
        """Inert LLM-call observation. Yields a handle whose finish() is a no-op."""
        yield _NoopGen()

    def event(self, name, **attrs):
        pass


class _LangfuseGen:
    """Generation handle: forwards finish(output, usage) to the Langfuse SDK,
    swallowing any SDK mismatch so an LLM turn never dies on tracing."""

    def __init__(self, gen):
        self._gen = gen

    def finish(self, *, output=None, usage=None):
        try:
            kw = {}
            if output is not None:
                kw["output"] = output
            if usage:
                kw["usage_details"] = usage
            if kw:
                self._gen.update(**kw)
        except Exception:
            log.debug("langfuse generation update failed")


class LangfuseTracer:
    """Thin wrapper over the Langfuse SDK. Constructed only when creds exist;
    langfuse is imported lazily so the dependency is optional at runtime."""

    def __init__(self):
        from langfuse import Langfuse  # lazy: only when creds present
        self._lf = Langfuse()  # reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST from env

    @contextmanager
    def span(self, name, **attrs):
        span = self._lf.start_span(name=name, input=attrs or None)
        try:
            yield span
        finally:
            try:
                span.end()
            except Exception:
                log.debug("langfuse span end failed for %s", name)

    @contextmanager
    def generation(self, name, **attrs):
        """An LLM-call observation (model + input + output + token usage → cost).
        Nests under any active span via the SDK's context. Degrades to an inert
        handle if start_generation is unavailable in the installed SDK."""
        model = attrs.pop("model", None)
        try:
            gen = self._lf.start_generation(name=name, model=model,
                                            input=attrs or None)
        except Exception:
            log.debug("langfuse start_generation failed for %s", name)
            yield _NoopGen()
            return
        try:
            yield _LangfuseGen(gen)
        finally:
            try:
                gen.end()
            except Exception:
                log.debug("langfuse generation end failed for %s", name)

    def event(self, name, **attrs):
        try:
            self._lf.create_event(name=name, metadata=attrs or None)
        except Exception:
            log.debug("langfuse event failed for %s", name)


class _DebugGen:
    """Generation handle for DebugTracer: stores output/usage; the record is
    written by the generation() ctx-mgr on exit (so a missing finish() still logs)."""
    def __init__(self):
        self.output = None
        self.usage = None

    def finish(self, *, output=None, usage=None):
        if output is not None:
            self.output = output
        if usage is not None:
            self.usage = usage


class DebugTracer:
    """Local structured tracer → JSONL (span tree + generations + events).
    Process-singleton (span stack must persist across get_tracer() calls)."""
    _PATH_KEYS = ("turn", "tool_name", "attempt", "step")

    def __init__(self, path, run="run"):
        self._path = path
        self._run = run
        self._seq = 0
        self._stack = []   # list[(seq, name, attrs)]

    def _next(self):
        self._seq += 1
        return self._seq

    @classmethod
    def _label(cls, name, attrs):
        for k in cls._PATH_KEYS:
            if attrs and k in attrs:
                return f"{name}:{attrs[k]}"
        return name

    def _path_str(self, leaf=None):
        parts = [self._label(n, a) for _, n, a in self._stack]
        if leaf is not None:
            parts.append(leaf)
        return "▸".join(parts)

    def _write(self, rec):
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            log.debug("DebugTracer write failed")

    @contextmanager
    def span(self, name, **attrs):
        seq = self._next()
        parent = self._stack[-1][0] if self._stack else None
        self._stack.append((seq, name, attrs))
        own_path = self._path_str()
        start = time.time()
        self._write({"run": self._run, "seq": seq, "ts": start, "type": "span_start",
                     "name": name, "path": own_path, "parent_seq": parent,
                     "attrs": attrs or None})
        try:
            yield None
        finally:
            dur = int((time.time() - start) * 1000)
            self._stack.pop()
            self._write({"run": self._run, "seq": self._next(), "ts": time.time(),
                         "type": "span_end", "name": name, "path": own_path,
                         "parent_seq": parent, "ref_seq": seq, "dur_ms": dur})

    @contextmanager
    def generation(self, name, **attrs):
        seq = self._next()
        parent = self._stack[-1][0] if self._stack else None
        own_path = self._path_str(leaf=name)
        start = time.time()
        h = _DebugGen()
        try:
            yield h
        finally:
            self._write({"run": self._run, "seq": seq, "ts": start, "type": "gen",
                         "name": name, "path": own_path, "parent_seq": parent,
                         "input": attrs.get("input"),
                         "attrs": {k: v for k, v in attrs.items() if k != "input"} or None,
                         "output": h.output, "usage": h.usage,
                         "dur_ms": int((time.time() - start) * 1000)})

    def event(self, name, **attrs):
        seq = self._next()
        parent = self._stack[-1][0] if self._stack else None
        self._write({"run": self._run, "seq": seq, "ts": time.time(), "type": "event",
                     "name": name, "path": self._path_str(leaf=name),
                     "parent_seq": parent, "attrs": attrs or None})


_DEBUG_TRACER = None


def get_tracer():
    """RPG_DEBUG_TRACE → DebugTracer (singleton); else Langfuse (if key); else Noop."""
    global _DEBUG_TRACER
    dbg = os.environ.get("RPG_DEBUG_TRACE")
    if dbg:
        if _DEBUG_TRACER is None or _DEBUG_TRACER._path != dbg:
            _DEBUG_TRACER = DebugTracer(dbg, run=os.environ.get("RPG_DEBUG_RUN", "run"))
        return _DEBUG_TRACER
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        try:
            return LangfuseTracer()
        except Exception as e:  # missing SDK / bad config -> degrade gracefully
            log.debug("Langfuse unavailable (%s); falling back to NoopTracer", e)
    return NoopTracer()


def dump(label: str, payload) -> None:
    """Debug-dump a kernel artifact (assembled context, turn-commit, validation)
    to the rpg debug log. Only emits when RPG_DEBUG / RPG_LOG_LEVEL=DEBUG."""
    try:
        body = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        body = repr(payload)
    log.debug("DUMP %s: %s", label, body)
