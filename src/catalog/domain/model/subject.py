"""The Subject aggregate (02-domain, 06-data/models.md schema catalog). The code is the business
key (INV-SUB-001, unique: checked by the use case, guaranteed by the database). Prerequisites
are codes; the graph must stay acyclic (INV-SUB-002), a rule that needs the whole graph and is
checked here with `ensure_acyclic` on every write, never in a CHECK."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from catalog.domain.model.errors import BusinessRuleViolation, FieldError, ValidationError

CODE_PATTERN = re.compile(r"^[A-Z0-9-]{3,10}$")


@dataclass(frozen=True)
class Subject:
    id: UUID
    code: str
    name: str
    credits: int
    semester: int
    prerequisites: tuple[str, ...]
    created_at: datetime

    @staticmethod
    def create(
        subject_id: UUID,
        code: str,
        name: str,
        credits: int,
        semester: int,
        prerequisites: Iterable[str],
        now: datetime,
    ) -> "Subject":
        details: list[FieldError] = []
        clean_code = code.strip() if isinstance(code, str) else ""
        if not CODE_PATTERN.match(clean_code):
            details.append(FieldError("code", "must match ^[A-Z0-9-]{3,10}$"))
        clean_name = name.strip() if isinstance(name, str) else ""
        if not 2 <= len(clean_name) <= 120:
            details.append(FieldError("name", "must have 2 to 120 characters"))
        if isinstance(credits, bool) or not isinstance(credits, int) or not 1 <= credits <= 10:
            details.append(FieldError("credits", "must be an integer between 1 and 10"))
        if isinstance(semester, bool) or not isinstance(semester, int) or not 1 <= semester <= 12:
            details.append(FieldError("semester", "must be an integer between 1 and 12"))
        prereqs = tuple(p.strip() if isinstance(p, str) else "" for p in prerequisites)
        if any(not CODE_PATTERN.match(p) for p in prereqs):
            details.append(FieldError("prerequisites", "every code must match ^[A-Z0-9-]{3,10}$"))
        elif len(set(prereqs)) != len(prereqs):
            details.append(FieldError("prerequisites", "must not repeat a code"))
        elif clean_code in prereqs:
            details.append(FieldError("prerequisites", "a subject cannot be its own prerequisite"))
        if details:
            raise ValidationError(details)
        moment = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        return Subject(subject_id, clean_code, clean_name, credits, semester, prereqs, moment)


def ensure_acyclic(code: str, prerequisites: Iterable[str], graph: Mapping[str, Iterable[str]]) -> None:
    """INV-SUB-002: adding `code -> prerequisites` to `graph` (code -> its prerequisites) must not
    create a cycle. Raises BusinessRuleViolation naming the cycle's entry point."""
    extended: dict[str, tuple[str, ...]] = {k: tuple(v) for k, v in graph.items()}
    extended[code] = tuple(prerequisites)
    seen: set[str] = set()
    stack = list(extended[code])
    while stack:
        current = stack.pop()
        if current == code:
            raise BusinessRuleViolation(f"INV-SUB-002: {code} would depend on itself through its prerequisites")
        if current in seen:
            continue
        seen.add(current)
        stack.extend(extended.get(current, ()))
