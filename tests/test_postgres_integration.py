"""Integration level (Annex C): the real engine with the schema and the seeds of
qone-catalog-db, through TEST_DATABASE_URL. Skipped, not failed, when the variable is absent.
Includes the saga's concurrency check: two reservations of the last seat, exactly one success."""

import os
import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from catalog.application.port.outbound.ports import DuplicateCode, DuplicateGroup
from catalog.domain.model.errors import BusinessRuleViolation
from catalog.domain.model.events import seat_reserved, section_created, subject_created
from catalog.domain.model.reservation import Reservation, ReservationStatus
from catalog.domain.model.section import Section
from catalog.domain.model.slot import Slot
from catalog.domain.model.subject import Subject

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
ISW_604 = UUID("44444444-4444-4444-8444-444444444409")
ISW_604_G1 = UUID("55555555-5555-4555-8555-555555555505")
ANA = UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(scope="module")
def pool():
    from catalog.adapter.outbound.persistence.postgres import connect

    pool = connect(URL, max_size=4)
    yield pool
    pool.close()


@pytest.fixture
def stores(pool):
    """Starts from the seed and leaves the database as it found it: the demo depends on it."""
    from catalog.adapter.outbound.persistence.postgres import (
        PostgresReservationStore,
        PostgresSectionStore,
        PostgresSubjectStore,
    )

    reset(pool)
    yield PostgresSubjectStore(pool), PostgresSectionStore(pool), PostgresReservationStore(pool)
    reset(pool)


def reset(pool):
    with pool.connection() as conn:
        conn.execute("TRUNCATE catalog.idempotency_key, catalog.outbox, catalog.seat_reservation")
        conn.execute(
            "DELETE FROM catalog.section_slot WHERE section_id IN (SELECT id FROM catalog.section "
            "WHERE group_number >= 50)"
        )
        conn.execute("DELETE FROM catalog.section WHERE group_number >= 50")
        conn.execute(
            "DELETE FROM catalog.subject_prerequisite WHERE subject_id IN (SELECT id FROM "
            "catalog.subject WHERE code LIKE 'IT-%%')"
        )
        conn.execute("DELETE FROM catalog.subject WHERE code LIKE 'IT-%%'")
        conn.execute("UPDATE catalog.section SET seats_available = 27 WHERE id = %s", (ISW_604_G1,))


def test_the_seed_reads_back_with_prerequisites_slots_and_the_graph(stores):
    subjects, sections, _ = stores
    isw604 = subjects.find_by_code("ISW-604")
    assert isw604.id == ISW_604 and isw604.prerequisites == ("RED-501",)
    graph = subjects.graph()
    assert graph["ISW-701"] == ("ISW-604",) and graph["RED-501"] == ()
    g1 = sections.find(ISW_604_G1)
    assert (
        g1.subject_code == "ISW-604" and g1.seats_available == 27 and [s.day.value for s in g1.slots] == ["MON", "WED"]
    )
    items, total = sections.page(ISW_604, "2026-B", None, 1, 20)
    assert total == 3 and {s.group_number for s in items} == {1, 2, 3}
    page, total = subjects.page(6, 1, 2)
    assert total == 5 and len(page) == 2


def test_a_subject_is_created_once_per_key_with_its_prerequisites_and_event(stores, pool):
    subjects, _, _ = stores
    subject = Subject.create(uuid4(), "IT-801", "Compiladores", 3, 8, ["ISW-604"], NOW)
    stored, created = subjects.create(subject, "it-subject-key-1", subject_created(subject))
    assert created and subjects.find_by_code("IT-801").prerequisites == ("ISW-604",)
    other = Subject.create(uuid4(), "IT-802", "Otra", 3, 8, [], NOW)
    again, created_again = subjects.create(other, "it-subject-key-1", subject_created(other))
    assert not created_again and again.code == "IT-801" and subjects.find_by_code("IT-802") is None
    with pytest.raises(DuplicateCode):
        subjects.create(
            Subject.create(uuid4(), "IT-801", "Dup", 3, 8, [], NOW), "it-subject-key-2", subject_created(subject)
        )
    with pool.connection() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM catalog.outbox WHERE event_type = 'SubjectCreated'").fetchone()["n"]
            == 1
        )


def test_a_section_is_opened_with_its_slots_and_a_repeated_group_is_refused(stores):
    _, sections, _ = stores
    section = Section.open(
        uuid4(),
        ISW_604,
        "ISW-604",
        "Sistemas Distribuidos",
        "2026-B",
        50,
        ANA,
        "Ana Torres",
        25,
        [Slot.parse("SAT", "08:00", "10:00")],
        NOW,
    )
    stored, created = sections.create(section, "it-section-key-1", section_created(section))
    assert created and sections.find(section.id).slots == section.slots
    dup = Section.open(
        uuid4(),
        ISW_604,
        "ISW-604",
        "Sistemas Distribuidos",
        "2026-B",
        50,
        ANA,
        "Ana Torres",
        25,
        [Slot.parse("SAT", "10:00", "12:00")],
        NOW,
    )
    with pytest.raises(DuplicateGroup):
        sections.create(dup, "it-section-key-2", section_created(dup))


def build_for(student: UUID):
    def build(section):
        taken = section.take_seat()
        reservation = Reservation.take(uuid4(), section.id, student, uuid4(), NOW)
        return taken, reservation, seat_reserved(reservation, taken.seats_available)

    return build


def test_reserve_commit_and_release_move_the_seat_count_in_the_database(stores):
    _, sections, reservations = stores
    first, created = reservations.reserve(ISW_604_G1, "it-reserve-key-1", build_for(uuid4()))
    assert created and sections.find(ISW_604_G1).seats_available == 26
    assert reservations.reserve(ISW_604_G1, "it-reserve-key-1", build_for(uuid4())) == (first, False)

    def release(section, r):
        released, back = r.release(NOW)
        return (section.give_seat_back() if back else section), released, None

    assert reservations.transition(ISW_604_G1, first.id, release).status is ReservationStatus.RELEASED
    assert sections.find(ISW_604_G1).seats_available == 27
    assert reservations.reserve(uuid4(), "it-reserve-key-2", build_for(uuid4())) is None


def test_two_concurrent_reservations_of_the_last_seat_give_exactly_one_success(stores, pool):
    _, sections, reservations = stores
    with pool.connection() as conn:
        conn.execute("UPDATE catalog.section SET seats_available = 1 WHERE id = %s", (ISW_604_G1,))
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt(n: int) -> None:
        barrier.wait()
        try:
            reservations.reserve(ISW_604_G1, f"it-race-key-{n}", build_for(uuid4()))
            outcomes.append("reserved")
        except BusinessRuleViolation as error:
            outcomes.append(str(error).split(":")[0])

    threads = [threading.Thread(target=attempt, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["NO_SEATS", "reserved"]
    assert sections.find(ISW_604_G1).seats_available == 0


def test_a_retry_of_the_same_key_returns_the_first_row_even_against_the_unique_indexes(stores, pool):
    subjects, _, reservations = stores
    first = Subject.create(uuid4(), "IT-901", "Primera", 3, 9, [], NOW)
    subjects.create(first, "it-retry-subject-1", subject_created(first))
    retry = Subject.create(uuid4(), "IT-901", "Reintento", 3, 9, [], NOW)
    assert subjects.create(retry, "it-retry-subject-1", subject_created(retry)) == (first, False)
    with pool.connection() as conn:
        conn.execute("UPDATE catalog.section SET seats_available = 1 WHERE id = %s", (ISW_604_G1,))
    reservation, _ = reservations.reserve(ISW_604_G1, "it-retry-reserve-1", build_for(uuid4()))
    assert reservations.reserve(ISW_604_G1, "it-retry-reserve-1", build_for(uuid4())) == (reservation, False)
