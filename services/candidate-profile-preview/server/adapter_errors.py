"""Minimal canonical domain error contract; deliberately no application imports."""
class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def require(condition, message, status=409):
    if not condition:
        raise Problem(message, status)
