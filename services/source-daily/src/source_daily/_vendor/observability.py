"""Adapter instrumentation hook; never emits provider bodies, URLs or tokens."""
import functools
import logging
import time


def operation(name):
    def decorate(fn):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            started = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                logging.getLogger('source.operations').debug('%s elapsed_ms=%d', name, int((time.monotonic()-started)*1000))
        return run
    return decorate
