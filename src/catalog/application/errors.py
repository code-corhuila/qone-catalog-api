"""Errors of the application layer; the HTTP adapter translates them (403, 404) in one place."""


class Forbidden(Exception):
    def __init__(self, message: str = "this role may not perform the operation") -> None:
        super().__init__(message)
        self.message = message


class NotFound(Exception):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message)
        self.message = message


class Unavailable(Exception):
    """A dependency the service needs does not answer: the ready probe says 503 (HU-PLT-004)."""

    def __init__(self, message: str = "a dependency is not reachable") -> None:
        super().__init__(message)
        self.message = message
