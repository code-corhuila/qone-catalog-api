"""What the catalog service needs (Annex C, outbound ports). The seat operations are
transactions the store owns: it locks the section row, hands the current section and
reservation to a function of the use case that applies the domain rules, and persists what
comes back together with the key and the event (INV-SEC-001 under SELECT ... FOR UPDATE)."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from catalog.domain.model.events import DomainEvent
from catalog.domain.model.reservation import Reservation
from catalog.domain.model.section import Section
from catalog.domain.model.subject import Subject


class DuplicateCode(Exception):
    """The unique index on the subject code fired (INV-SUB-001)."""


class DuplicateGroup(Exception):
    """The unique index on (subject, term, group) fired."""


class SubjectStore(Protocol):
    def find_by_code(self, code: str) -> Subject | None: ...

    def find_by_key(self, idempotency_key: str) -> Subject | None: ...

    def graph(self) -> dict[str, tuple[str, ...]]:
        """Every subject code with its prerequisite codes, for INV-SUB-002."""

    def create(self, subject: Subject, idempotency_key: str, event: DomainEvent) -> tuple[Subject, bool]:
        """One transaction: the subject, its prerequisites, the key and the event. Raises DuplicateCode."""

    def page(self, semester: int | None, page: int, limit: int) -> tuple[list[Subject], int]: ...


class SectionStore(Protocol):
    def find(self, section_id: UUID) -> Section | None: ...

    def find_by_key(self, idempotency_key: str) -> Section | None: ...

    def create(self, section: Section, idempotency_key: str, event: DomainEvent) -> tuple[Section, bool]:
        """One transaction: the section, its slots, the key and the event. Raises DuplicateGroup."""

    def page(
        self, subject_id: UUID | None, term: str | None, professor_id: UUID | None, page: int, limit: int
    ) -> tuple[list[Section], int]: ...


ReserveFn = Callable[[Section], tuple[Section, Reservation, DomainEvent]]
TransitionFn = Callable[[Section, Reservation], tuple[Section, Reservation, DomainEvent | None]]


class ReservationStore(Protocol):
    def find_by_key(self, idempotency_key: str) -> Reservation | None: ...

    def find(self, section_id: UUID, reservation_id: UUID) -> Reservation | None: ...

    def reserve(self, section_id: UUID, idempotency_key: str, build: ReserveFn) -> tuple[Reservation, bool]:
        """Locks the section, calls `build(section)` and persists the section, the reservation,
        the key and the event in ONE transaction. A used key rolls back and returns the first
        reservation. Raises NotFound-like None when the section does not exist (returns None)."""

    def transition(self, section_id: UUID, reservation_id: UUID, apply: TransitionFn) -> Reservation | None:
        """Locks the section and the reservation, calls `apply`, persists both and the event (if any)
        in ONE transaction. None when the reservation does not exist for that section."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...
