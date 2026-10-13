"""Core level: the outbox payloads carry the API shape."""

from datetime import UTC, datetime
from uuid import uuid4

from catalog.domain.model.events import seat_reserved, section_created, subject_created
from catalog.domain.model.reservation import Reservation
from catalog.domain.model.section import Section
from catalog.domain.model.slot import Slot
from catalog.domain.model.subject import Subject

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


def test_subject_section_and_seat_events():
    subject = Subject.create(uuid4(), "ISW-801", "Compiladores", 3, 8, ["ISW-602"], NOW)
    assert subject_created(subject).payload["prerequisites"] == ["ISW-602"]
    section = Section.open(
        uuid4(),
        subject.id,
        subject.code,
        subject.name,
        "2026-B",
        1,
        uuid4(),
        "Ana Torres",
        30,
        [Slot.parse("MON", "08:00", "10:00")],
        NOW,
    )
    event = section_created(section)
    assert (
        event.payload["slots"] == [{"day": "MON", "start": "08:00", "end": "10:00"}]
        and event.payload["seatsAvailable"] == 30
    )
    reservation = Reservation.take(uuid4(), section.id, uuid4(), uuid4(), NOW)
    seat = seat_reserved(reservation, 29)
    assert (
        seat.event_type == "SeatReserved" and seat.aggregate_id == section.id and seat.payload["seatsAvailable"] == 29
    )
