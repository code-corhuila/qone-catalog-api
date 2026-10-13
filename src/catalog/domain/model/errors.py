"""Typed errors of the catalog domain. The domain raises these; it does not know HTTP."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


class DomainError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ValidationError(DomainError):
    """The shape of the input is invalid: one entry per field."""

    details: list[FieldError] = field(default_factory=list)

    def __init__(self, details: list[FieldError]) -> None:
        super().__init__("the request has invalid fields")
        self.details = details


class BusinessRuleViolation(DomainError):
    """An invariant forbids the operation (INV-SUB-001, INV-SUB-002, INV-SEC-001, INV-SEC-002)."""


class InvalidStatusTransition(DomainError):
    """The state rule of a reservation forbids the transition."""
