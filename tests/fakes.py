"""Fakes of the trivial ports and a small seed for the core and HTTP tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from catalog.domain.model.section import Section
from catalog.domain.model.slot import Slot
from catalog.domain.model.subject import Subject

CREATED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
CARLOS = UUID("22222222-2222-4222-8222-222222222221")
ANA = UUID("22222222-2222-4222-8222-222222222222")


def subject(n: int, code: str, name: str, credits: int, semester: int, prereqs: tuple[str, ...] = ()) -> Subject:
    return Subject(UUID(f"44444444-4444-4444-8444-4444444444{n:02d}"), code, name, credits, semester, prereqs, CREATED)


RED_501 = subject(3, "RED-501", "Redes de Datos", 3, 5)
ISW_604 = subject(9, "ISW-604", "Sistemas Distribuidos", 4, 6, ("RED-501",))
ISW_701 = subject(11, "ISW-701", "Seguridad Informática", 3, 7, ("ISW-604",))
SUBJECTS = [RED_501, ISW_604, ISW_701]


def section(
    n: int, subj: Subject, group: int, professor: UUID, professor_name: str, capacity: int, seats: int, slots
) -> Section:
    return Section(
        UUID(f"55555555-5555-4555-8555-5555555555{n:02d}"),
        subj.id,
        subj.code,
        subj.name,
        "2026-B",
        group,
        professor,
        professor_name,
        capacity,
        seats,
        tuple(Slot.parse(*s) for s in slots),
        CREATED,
    )


ISW_604_G1 = section(
    5, ISW_604, 1, CARLOS, "Carlos Ramírez", 30, 27, [("MON", "08:00", "10:00"), ("WED", "08:00", "10:00")]
)
ISW_604_G3 = section(7, ISW_604, 3, ANA, "Ana Torres", 3, 0, [("FRI", "08:00", "12:00")])
RED_501_G1 = section(9, RED_501, 1, CARLOS, "Carlos Ramírez", 30, 30, [("FRI", "08:00", "11:00")])
SECTIONS = [ISW_604_G1, ISW_604_G3, RED_501_G1]


class FixedClock:
    def __init__(self, start: datetime = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def tick(self, **delta) -> datetime:
        self.current = self.current + timedelta(**delta)
        return self.current


class SequenceIds:
    def __init__(self, start: int = 1) -> None:
        self.next = start

    def new_id(self) -> UUID:
        value = UUID(f"99999999-9999-4999-8999-9999999999{self.next:02d}")
        self.next += 1
        return value
