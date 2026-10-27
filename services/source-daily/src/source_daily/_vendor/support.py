"""Minimal adapter primitives; deliberately no imports from the HR application."""
import datetime as dt

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def require(condition, message, status=409):
    if not condition:
        raise Problem(message, status)


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
