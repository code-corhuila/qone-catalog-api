"""What the catalog service offers (Annex C, inbound ports): CA-02..CA-12 as commands, results
and the use-case interface the HTTP adapter depends on."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from catalog.application.port.inbound.caller import Caller
from catalog.domain.model.reservation import Reservation
from catalog.domain.model.section import Section
from catalog.domain.model.slot import Slot
from catalog.domain.model.subject import Subject

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True)
class CreateSubject:
    code: str
    name: str
    credits: int
    semester: int
    prerequisites: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenSection:
    subject_code: str
    term: str
    group_number: int
    professor_id: UUID
    professor_name: str
    capacity: int
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class ReserveSeat:
    section_id: UUID
    student_id: UUID
    saga_id: UUID


@dataclass(frozen=True)
class Created[T]:
    item: T
    created: bool
    """False when the Idempotency-Key had been used: the item is the one created first."""


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    page: int
    limit: int
    total: int

    @property
    def total_pages(self) -> int:
        return -(-self.total // self.limit) if self.total else 0


class CatalogUseCases(Protocol):
    def list_subjects(self, caller: Caller, semester: int | None, page: int, limit: int) -> Page[Subject]:
        """CA-02, anyone signed in. Newest first."""

    def create_subject(self, caller: Caller, command: CreateSubject, idempotency_key: str) -> Created[Subject]:
        """CA-03, ADMIN. INV-SUB-001 and INV-SUB-002 are business rule violations."""

    def get_subject(self, caller: Caller, code: str) -> Subject:
        """CA-04."""

    def sections_of(self, caller: Caller, code: str, term: str | None, page: int, limit: int) -> Page[Section]:
        """CA-05; an unknown subject is NotFound."""

    def list_sections(
        self, caller: Caller, term: str | None, professor_id: UUID | None, page: int, limit: int
    ) -> Page[Section]:
        """CA-06, ADMIN."""

    def my_sections(self, caller: Caller, term: str | None, page: int, limit: int) -> Page[Section]:
        """CA-07, PROFESSOR: the sections whose professorId is the token's subject."""

    def open_section(self, caller: Caller, command: OpenSection, idempotency_key: str) -> Created[Section]:
        """CA-09, ADMIN. Unknown subject is NotFound; a repeated group is a business rule violation."""

    def get_section(self, caller: Caller, section_id: UUID) -> Section:
        """CA-08."""

    def reserve(self, caller: Caller, command: ReserveSeat, idempotency_key: str) -> Created[Reservation]:
        """CA-10, SERVICE. A full section is NO_SEATS (business rule violation)."""

    def release(self, caller: Caller, section_id: UUID, reservation_id: UUID) -> None:
        """CA-11, SERVICE. Idempotent on a released or expired reservation; a committed one is an invalid transition."""

    def commit(self, caller: Caller, section_id: UUID, reservation_id: UUID) -> Reservation:
        """CA-12, SERVICE. Idempotent on a committed reservation; released or expired is an invalid transition."""
