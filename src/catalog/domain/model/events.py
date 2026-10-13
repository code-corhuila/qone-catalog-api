"""Events the catalog publishes (02-domain/domain-events.md), written to the outbox in the
transaction of the change: SubjectCreated, SectionCreated, SeatReserved, SeatReleased,
SeatCommitted. Payloads are camelCase, the shape the API returns."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from catalog.domain.model.reservation import Reservation
from catalog.domain.model.section import Section
from catalog.domain.model.subject import Subject


@dataclass(frozen=True)
class DomainEvent:
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: dict[str, Any]
    occurred_at: datetime


def _rfc3339(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def subject_created(subject: Subject) -> DomainEvent:
    payload = {
        "subjectId": str(subject.id),
        "code": subject.code,
        "name": subject.name,
        "credits": subject.credits,
        "semester": subject.semester,
        "prerequisites": list(subject.prerequisites),
    }
    return DomainEvent("SubjectCreated", "subject", subject.id, payload, subject.created_at)


def section_created(section: Section) -> DomainEvent:
    payload = {
        "sectionId": str(section.id),
        "subjectId": str(section.subject_id),
        "subjectCode": section.subject_code,
        "subjectName": section.subject_name,
        "term": section.term,
        "groupNumber": section.group_number,
        "professorId": str(section.professor_id),
        "professorName": section.professor_name,
        "capacity": section.capacity,
        "seatsAvailable": section.seats_available,
        "slots": [
            {"day": s.day.value, "start": s.start.strftime("%H:%M"), "end": s.end.strftime("%H:%M")}
            for s in section.slots
        ],
    }
    return DomainEvent("SectionCreated", "section", section.id, payload, section.created_at)


def _seat_event(event_type: str, reservation: Reservation, seats_available: int, moment: datetime) -> DomainEvent:
    payload = {
        "reservationId": str(reservation.id),
        "sectionId": str(reservation.section_id),
        "studentId": str(reservation.student_id),
        "sagaId": str(reservation.saga_id),
        "seatsAvailable": seats_available,
        "occurredAt": _rfc3339(moment),
    }
    return DomainEvent(event_type, "section", reservation.section_id, payload, moment)


def seat_reserved(reservation: Reservation, seats_available: int) -> DomainEvent:
    return _seat_event("SeatReserved", reservation, seats_available, reservation.created_at)


def seat_released(reservation: Reservation, seats_available: int) -> DomainEvent:
    return _seat_event("SeatReleased", reservation, seats_available, reservation.updated_at)


def seat_committed(reservation: Reservation, seats_available: int) -> DomainEvent:
    return _seat_event("SeatCommitted", reservation, seats_available, reservation.updated_at)
