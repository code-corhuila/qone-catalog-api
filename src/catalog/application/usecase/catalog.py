"""The operations of the catalog service (CA-02..CA-12) over the ports. Authorization by role
happens here (07-api/authentication.md access matrix: reads for everyone signed in, creation
for ADMIN, the professor's own sections, reservations for the saga's SERVICE token only).
Input bounds carry the contract's field names so the HTTP adapter only translates."""

from datetime import timedelta
from uuid import UUID

from catalog.application.errors import Forbidden, NotFound
from catalog.application.port.inbound.caller import Caller, Role
from catalog.application.port.inbound.catalog import (
    MAX_LIMIT,
    CatalogUseCases,
    Created,
    CreateSubject,
    OpenSection,
    Page,
    ReserveSeat,
)
from catalog.application.port.outbound.ports import (
    Clock,
    DuplicateCode,
    DuplicateGroup,
    IdGenerator,
    ReservationStore,
    SectionStore,
    SubjectStore,
)
from catalog.domain.model.errors import BusinessRuleViolation, FieldError, ValidationError
from catalog.domain.model.events import seat_committed, seat_released, seat_reserved, section_created, subject_created
from catalog.domain.model.reservation import DEFAULT_TTL, Reservation
from catalog.domain.model.section import TERM_PATTERN, Section
from catalog.domain.model.subject import CODE_PATTERN, Subject, ensure_acyclic

KEY_MIN, KEY_MAX = 8, 128
SIGNED_IN = (Role.STUDENT, Role.PROFESSOR, Role.ADMIN, Role.SERVICE)


class CatalogService(CatalogUseCases):
    def __init__(
        self,
        subjects: SubjectStore,
        sections: SectionStore,
        reservations: ReservationStore,
        clock: Clock,
        ids: IdGenerator,
        reservation_ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._subjects = subjects
        self._sections = sections
        self._reservations = reservations
        self._clock = clock
        self._ids = ids
        self._ttl = reservation_ttl

    # --- subjects
    def list_subjects(self, caller: Caller, semester: int | None, page: int, limit: int) -> Page[Subject]:
        _require(caller, *SIGNED_IN)
        details = _page_details(page, limit)
        if semester is not None and (
            isinstance(semester, bool) or not isinstance(semester, int) or not 1 <= semester <= 12
        ):
            details.append(FieldError("semester", "must be an integer between 1 and 12"))
        _raise_if(details)
        items, total = self._subjects.page(semester, page, limit)
        return Page(items, page, limit, total)

    def create_subject(self, caller: Caller, command: CreateSubject, idempotency_key: str) -> Created[Subject]:
        _require(caller, Role.ADMIN)
        _check_key(idempotency_key)
        earlier = self._subjects.find_by_key(idempotency_key)
        if earlier is not None:
            return Created(earlier, created=False)
        subject = Subject.create(
            self._ids.new_id(),
            command.code,
            command.name,
            command.credits,
            command.semester,
            command.prerequisites,
            self._clock.now(),
        )
        if self._subjects.find_by_code(subject.code) is not None:
            raise _code_taken(subject.code)
        graph = self._subjects.graph()
        for code in subject.prerequisites:
            if code not in graph:
                raise BusinessRuleViolation(f"unknown prerequisite: {code}")
        ensure_acyclic(subject.code, subject.prerequisites, graph)
        try:
            stored, created = self._subjects.create(subject, idempotency_key, subject_created(subject))
        except DuplicateCode:
            raise _code_taken(subject.code) from None
        return Created(stored, created)

    def get_subject(self, caller: Caller, code: str) -> Subject:
        _require(caller, *SIGNED_IN)
        subject = self._subjects.find_by_code(_code(code))
        if subject is None:
            raise NotFound()
        return subject

    # --- sections
    def sections_of(self, caller: Caller, code: str, term: str | None, page: int, limit: int) -> Page[Section]:
        _require(caller, *SIGNED_IN)
        details = _page_details(page, limit) + _term_details(term)
        _raise_if(details)
        subject = self._subjects.find_by_code(_code(code))
        if subject is None:
            raise NotFound()
        items, total = self._sections.page(subject.id, term, None, page, limit)
        return Page(items, page, limit, total)

    def list_sections(
        self, caller: Caller, term: str | None, professor_id: UUID | None, page: int, limit: int
    ) -> Page[Section]:
        _require(caller, Role.ADMIN)
        _raise_if(_page_details(page, limit) + _term_details(term))
        items, total = self._sections.page(None, term, professor_id, page, limit)
        return Page(items, page, limit, total)

    def my_sections(self, caller: Caller, term: str | None, page: int, limit: int) -> Page[Section]:
        _require(caller, Role.PROFESSOR)
        _raise_if(_page_details(page, limit) + _term_details(term))
        items, total = self._sections.page(None, term, caller.user_id, page, limit)
        return Page(items, page, limit, total)

    def open_section(self, caller: Caller, command: OpenSection, idempotency_key: str) -> Created[Section]:
        _require(caller, Role.ADMIN)
        _check_key(idempotency_key)
        earlier = self._sections.find_by_key(idempotency_key)
        if earlier is not None:
            return Created(earlier, created=False)
        subject = self._subjects.find_by_code(_code(command.subject_code, "subjectCode"))
        if subject is None:
            raise NotFound("subject not found")
        section = Section.open(
            self._ids.new_id(),
            subject.id,
            subject.code,
            subject.name,
            command.term,
            command.group_number,
            command.professor_id,
            command.professor_name,
            command.capacity,
            list(command.slots),
            self._clock.now(),
        )
        try:
            stored, created = self._sections.create(section, idempotency_key, section_created(section))
        except DuplicateGroup:
            raise BusinessRuleViolation(
                f"group {section.group_number} of {section.subject_code} already exists in {section.term}"
            ) from None
        return Created(stored, created)

    def get_section(self, caller: Caller, section_id: UUID) -> Section:
        _require(caller, *SIGNED_IN)
        section = self._sections.find(section_id)
        if section is None:
            raise NotFound()
        return section

    # --- reservations (the saga)
    def reserve(self, caller: Caller, command: ReserveSeat, idempotency_key: str) -> Created[Reservation]:
        _require(caller, Role.SERVICE)
        _check_key(idempotency_key)
        now = self._clock.now()

        def build(section: Section):
            taken = section.take_seat()  # NO_SEATS when full (INV-SEC-001), under the store's lock
            reservation = Reservation.take(
                self._ids.new_id(), section.id, command.student_id, command.saga_id, now, self._ttl
            )
            return taken, reservation, seat_reserved(reservation, taken.seats_available)

        result = self._reservations.reserve(command.section_id, idempotency_key, build)
        if result is None:
            raise NotFound("section not found")
        reservation, created = result
        return Created(reservation, created)

    def release(self, caller: Caller, section_id: UUID, reservation_id: UUID) -> None:
        _require(caller, Role.SERVICE)
        now = self._clock.now()

        def apply(section: Section, reservation: Reservation):
            released, seat_back = reservation.release(now)
            if not seat_back:
                return section, released, None
            freed = section.give_seat_back()
            return freed, released, seat_released(released, freed.seats_available)

        if self._reservations.transition(section_id, reservation_id, apply) is None:
            raise NotFound()

    def commit(self, caller: Caller, section_id: UUID, reservation_id: UUID) -> Reservation:
        _require(caller, Role.SERVICE)
        now = self._clock.now()

        def apply(section: Section, reservation: Reservation):
            committed, changed = reservation.commit(now)
            return section, committed, seat_committed(committed, section.seats_available) if changed else None

        committed = self._reservations.transition(section_id, reservation_id, apply)
        if committed is None:
            raise NotFound()
        return committed


def _require(caller: Caller, *roles: Role) -> None:
    if caller.role not in roles:
        raise Forbidden()


def _check_key(idempotency_key: str) -> None:
    if not KEY_MIN <= len(idempotency_key or "") <= KEY_MAX:
        raise ValidationError([FieldError("Idempotency-Key", "header required, 8 to 128 characters")])


def _page_details(page: int, limit: int) -> list[FieldError]:
    details: list[FieldError] = []
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        details.append(FieldError("page", "must be 1 or greater"))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        details.append(FieldError("limit", f"must be between 1 and {MAX_LIMIT}"))
    return details


def _term_details(term: str | None) -> list[FieldError]:
    if term is not None and not TERM_PATTERN.match(term):
        return [FieldError("term", "must match ^[0-9]{4}-[AB]$")]
    return []


def _raise_if(details: list[FieldError]) -> None:
    if details:
        raise ValidationError(details)


def _code(value: str, field: str = "code") -> str:
    clean = value.strip() if isinstance(value, str) else ""
    if not CODE_PATTERN.match(clean):
        raise ValidationError([FieldError(field, "must match ^[A-Z0-9-]{3,10}$")])
    return clean


def _code_taken(code: str) -> BusinessRuleViolation:
    return BusinessRuleViolation(f"INV-SUB-001: subject code {code} already exists")
