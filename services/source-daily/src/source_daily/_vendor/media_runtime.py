"""Bound cumulative provider work and per-call contention, not just one socket."""
from contextlib import contextmanager
import threading
import time

from .support import Problem


OPERATION_SECONDS = 25
LOCK_WAIT_SECONDS = 5
_context = threading.local()


def current_deadline():
    return getattr(_context, 'deadline', None)


@contextmanager
def provider_budget(*, deadline=None):
    previous = current_deadline()
    deadline = deadline if deadline is not None else time.monotonic() + OPERATION_SECONDS
    _context.deadline = min(previous, deadline) if previous is not None else deadline
    try:
        yield
    finally:
        _context.deadline = previous


def provider_timeout():
    deadline = current_deadline()
    remaining = deadline - time.monotonic() if deadline is not None else 18
    if remaining <= 0:
        raise Problem('Видеосервис отвечает слишком долго. Повторите попытку.', 502)
    return min(18, remaining)


@contextmanager
def operation_lock(lock):
    with provider_budget():
        acquired = lock.acquire(timeout=min(LOCK_WAIT_SECONDS, provider_timeout()))
        try:
            if not acquired:
                raise Problem('Предыдущая операция со звонком ещё выполняется. Повторите попытку.', 503)
            yield
        finally:
            if acquired:
                lock.release()
