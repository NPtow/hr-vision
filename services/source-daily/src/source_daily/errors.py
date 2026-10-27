class ServiceError(Exception):
    def __init__(self, code, status=502, *, retryable=False):
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(code)


class LeaseLost(ServiceError):
    def __init__(self):
        super().__init__('lease_lost', 409)
