"""In-memory stores: the fakes of the ports for the core and HTTP tests, with the semantics the
PostgreSQL adapter must keep: a key used once, unique code and group, the seat operations
applied to the current section and reservation as one unit."""

from uuid import UUID

from catalog.application.port.outbound.ports import DuplicateCode, DuplicateGroup, ReserveFn, TransitionFn
from catalog.domain.model.events import DomainEvent
from catalog.domain.model.reservation import Reservation
from catalog.domain.model.section import Section
from catalog.domain.model.subject import Subject


class InMemoryCatalog:
    """One object implements the three stores so the seat operations see the same sections."""

    def __init__(self, subjects: list[Subject] | None = None, sections: list[Section] | None = None) -> None:
        self.subjects: dict[UUID, Subject] = {s.id: s for s in subjects or []}
        self.sections: dict[UUID, Section] = {s.id: s for s in sections or []}
        self.reservations: dict[UUID, Reservation] = {}
        self.keys: dict[str, UUID] = {}
        self.outbox: list[DomainEvent] = []

    # --- SubjectStore
    def find_by_code(self, code: str) -> Subject | None:
        return next((s for s in self.subjects.values() if s.code == code), None)

    def find_by_key(self, idempotency_key: str):
        target = self.keys.get(idempotency_key)
        if target is None:
            return None
        return self.subjects.get(target) or self.sections.get(target) or self.reservations.get(target)

    def graph(self) -> dict[str, tuple[str, ...]]:
        return {s.code: s.prerequisites for s in self.subjects.values()}

    def create(self, item, idempotency_key: str, event: DomainEvent):
        earlier = self.find_by_key(idempotency_key)
        if earlier is not None:
            return earlier, False
        if isinstance(item, Subject):
            if self.find_by_code(item.code) is not None:
                raise DuplicateCode()
            self.subjects[item.id] = item
        else:
            if any(
                s.subject_id == item.subject_id and s.term == item.term and s.group_number == item.group_number
                for s in self.sections.values()
            ):
                raise DuplicateGroup()
            self.sections[item.id] = item
        self.keys[idempotency_key] = item.id
        self.outbox.append(event)
        return item, True

    def page(self, *args):
        if len(args) == 3:  # subjects: semester, page, limit
            semester, page, limit = args
            rows = [s for s in self.subjects.values() if semester is None or s.semester == semester]
        else:  # sections: subject_id, term, professor_id, page, limit
            subject_id, term, professor_id, page, limit = args
            rows = [
                s
                for s in self.sections.values()
                if (subject_id is None or s.subject_id == subject_id)
                and (term is None or s.term == term)
                and (professor_id is None or s.professor_id == professor_id)
            ]
        rows.sort(key=lambda r: (r.created_at, str(r.id)), reverse=True)
        start = (page - 1) * limit
        return rows[start : start + limit], len(rows)

    # --- SectionStore
    def find(self, *args):
        if len(args) == 1:
            return self.sections.get(args[0])
        section_id, reservation_id = args
        reservation = self.reservations.get(reservation_id)
        return reservation if reservation and reservation.section_id == section_id else None

    # --- ReservationStore
    def reserve(self, section_id: UUID, idempotency_key: str, build: ReserveFn):
        earlier = self.find_by_key(idempotency_key)
        if earlier is not None:
            return earlier, False
        section = self.sections.get(section_id)
        if section is None:
            return None
        section, reservation, event = build(section)
        self.sections[section.id] = section
        self.reservations[reservation.id] = reservation
        self.keys[idempotency_key] = reservation.id
        self.outbox.append(event)
        return reservation, True

    def transition(self, section_id: UUID, reservation_id: UUID, apply: TransitionFn):
        reservation = self.find(section_id, reservation_id)
        if reservation is None:
            return None
        section, reservation, event = apply(self.sections[section_id], reservation)
        self.sections[section.id] = section
        self.reservations[reservation.id] = reservation
        if event is not None:
            self.outbox.append(event)
        return reservation
