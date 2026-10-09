"""Collect exhausted generation failures so genesis cannot publish fake success."""
from contextlib import contextmanager
from contextvars import ContextVar

_failures = ContextVar('rpg_generation_failures', default=None)


def record_failure(label, errors, *, exception=None):
    current = _failures.get()
    if current is not None:
        failure = {'stage':label, 'errors':list(errors)}
        if exception is not None:
            # Private, context-local identity: an earlier recovered provider
            # failure must not misclassify an unrelated host exception later.
            failure['exception'] = exception
        current.append(failure)


@contextmanager
def collect_failures():
    failures = []
    token = _failures.set(failures)
    try:
        yield failures
    finally:
        _failures.reset(token)


class WatchedProvider:
    def __init__(self, provider):
        self.provider = provider

    def __getattr__(self, name):
        value = getattr(self.provider, name)
        if not name.startswith('complete') or not callable(value):
            return value
        def invoke(*args, **kwargs):
            try:
                return value(*args, **kwargs)
            except Exception as exc:
                record_failure(name, [type(exc).__name__], exception=exc)
                raise
        return invoke
