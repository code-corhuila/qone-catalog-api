"""The Section aggregate: a group of a subject in a term with its professor (a snapshot chosen by
the portal; catalog never reads identity), its weekly slots and its seats.

- INV-SEC-002: the slots of one section do not overlap (interval logic, here).
- INV-SEC-001: seats never go below zero. `take_seat` refuses with NO_SEATS; the adapter runs
  it under a row lock so two concurrent reservations on one seat produce exactly one success.
"""

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from catalog.domain.model.errors import BusinessRuleViolation, FieldError, ValidationError
from catalog.domain.model.slot import Slot, any_overlap

TERM_PATTERN = re.compile(r"^[0-9]{4}-[AB]$")


@dataclass(frozen=True)
class Section:
    id: UUID
    subject_id: UUID
    subject_code: str
    subject_name: str
    term: str
    group_number: int
    professor_id: UUID
    professor_name: str
    capacity: int
    seats_available: int
    slots: tuple[Slot, ...]
    created_at: datetime

    @staticmethod
    def open(
        section_id: UUID,
        subject_id: UUID,
        subject_code: str,
        subject_name: str,
        term: str,
        group_number: int,
        professor_id: UUID,
        professor_name: str,
        capacity: int,
        slots: list[Slot],
        now: datetime,
    ) -> "Section":
        details: list[FieldError] = []
        if not isinstance(term, str) or not TERM_PATTERN.match(term):
            details.append(FieldError("term", "must match ^[0-9]{4}-[AB]$"))
        if isinstance(group_number, bool) or not isinstance(group_number, int) or not 1 <= group_number <= 99:
            details.append(FieldError("groupNumber", "must be an integer between 1 and 99"))
        clean_professor = professor_name.strip() if isinstance(professor_name, str) else ""
        if not 2 <= len(clean_professor) <= 120:
            details.append(FieldError("professorName", "must have 2 to 120 characters"))
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 500:
            details.append(FieldError("capacity", "must be an integer between 1 and 500"))
        if not slots:
            details.append(FieldError("slots", "at least one slot is required"))
        if details:
            raise ValidationError(details)
        if any_overlap(slots):
            raise BusinessRuleViolation("INV-SEC-002: the slots of a section must not overlap")
        moment = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        return Section(
            id=section_id,
            subject_id=subject_id,
            subject_code=subject_code,
            subject_name=subject_name,
            term=term,
            group_number=group_number,
            professor_id=professor_id,
            professor_name=clean_professor,
            capacity=capacity,
            seats_available=capacity,
            slots=tuple(sorted(slots)),
            created_at=moment,
        )

    @property
    def is_full(self) -> bool:
        return self.seats_available <= 0

    def take_seat(self) -> "Section":
        """One seat less, or NO_SEATS (INV-SEC-001)."""
        if self.is_full:
            raise BusinessRuleViolation(
                f"NO_SEATS: section {self.subject_code} group {self.group_number} has no seats available"
            )
        return replace(self, seats_available=self.seats_available - 1)

    def give_seat_back(self) -> "Section":
        """One seat more, never above the capacity."""
        return replace(self, seats_available=min(self.capacity, self.seats_available + 1))
