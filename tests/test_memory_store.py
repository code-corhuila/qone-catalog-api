"""The in-memory stores keep the semantics the PostgreSQL adapter must keep."""

from uuid import uuid4

import pytest

from catalog.adapter.outbound.persistence.memory import InMemoryCatalog
from catalog.application.port.outbound.ports import DuplicateCode, DuplicateGroup
from catalog.domain.model.events import seat_reserved, section_created, subject_created
from catalog.domain.model.reservation import Reservation
from catalog.domain.model.section import Section
from catalog.domain.model.slot import Slot
from catalog.domain.model.subject import Subject
from tests.fakes import ANA, CREATED, ISW_604, ISW_604_G1, SECTIONS, SUBJECTS


@pytest.fixture
def store():
    return InMemoryCatalog(list(SUBJECTS), list(SECTIONS))


def test_a_used_key_returns_the_first_item_and_the_unique_rules_hold(store):
    new = Subject.create(uuid4(), "ISW-801", "Compiladores", 3, 8, [], CREATED)
    first, created = store.create(new, "key-subject-0001", subject_created(new))
    again, created_again = store.create(
        Subject.create(uuid4(), "ISW-802", "Otra", 3, 8, [], CREATED), "key-subject-0001", subject_created(new)
    )
    assert created and not created_again and again == first and len(store.outbox) == 1
    with pytest.raises(DuplicateCode):
        store.create(
            Subject.create(uuid4(), "ISW-801", "Dup", 3, 8, [], CREATED), "key-subject-0002", subject_created(new)
        )
    dup = Section.open(
        uuid4(),
        ISW_604.id,
        "ISW-604",
        ISW_604.name,
        "2026-B",
        1,
        ANA,
        "Ana Torres",
        30,
        [Slot.parse("TUE", "08:00", "10:00")],
        CREATED,
    )
    with pytest.raises(DuplicateGroup):
        store.create(dup, "key-section-0001", section_created(dup))


def test_the_graph_and_the_pages(store):
    assert store.graph()["ISW-701"] == ("ISW-604",)
    items, total = store.page(6, 1, 20)
    assert [s.code for s in items] == ["ISW-604"] and total == 1
    items, total = store.page(ISW_604.id, "2026-B", None, 1, 20)
    assert {s.group_number for s in items} == {1, 3} and total == 2


def test_reserve_and_transition_apply_the_function_as_one_unit(store):
    def build(section):
        taken = section.take_seat()
        reservation = Reservation.take(uuid4(), section.id, uuid4(), uuid4(), CREATED)
        return taken, reservation, seat_reserved(reservation, taken.seats_available)

    reservation, created = store.reserve(ISW_604_G1.id, "key-reserve-0001", build)
    assert created and store.find(ISW_604_G1.id).seats_available == 26
    assert store.reserve(ISW_604_G1.id, "key-reserve-0001", build) == (reservation, False)
    assert store.reserve(uuid4(), "key-reserve-0002", build) is None

    def apply(section, res):
        committed, _ = res.commit(CREATED)
        return section, committed, None

    assert store.transition(ISW_604_G1.id, reservation.id, apply).status.value == "COMMITTED"
    assert store.transition(ISW_604_G1.id, uuid4(), apply) is None
