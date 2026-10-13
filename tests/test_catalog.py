"""Core level (Annex C): the use cases over the in-memory catalog and fakes of the ports.
Authorization, idempotent creation, INV-SUB-001/002, INV-SEC-001/002, the saga's reservations."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from catalog.adapter.outbound.persistence.memory import InMemoryCatalog
from catalog.application.errors import Forbidden, NotFound
from catalog.application.port.inbound.caller import Caller, Role
from catalog.application.port.inbound.catalog import CreateSubject, OpenSection, ReserveSeat
from catalog.application.usecase.catalog import CatalogService
from catalog.domain.model.errors import BusinessRuleViolation, InvalidStatusTransition, ValidationError
from catalog.domain.model.reservation import ReservationStatus
from catalog.domain.model.slot import Slot
from tests.fakes import (
    ANA,
    CARLOS,
    ISW_604,
    ISW_604_G1,
    ISW_604_G3,
    RED_501_G1,
    SECTIONS,
    SUBJECTS,
    FixedClock,
    SequenceIds,
)

LAURA = UUID("11111111-1111-4111-8111-111111111111")
PATRICIA = UUID("33333333-3333-4333-8333-333333333331")
admin = Caller(PATRICIA, Role.ADMIN, "Patricia Mora")
laura = Caller(LAURA, Role.STUDENT, "Laura Gómez")
carlos = Caller(CARLOS, Role.PROFESSOR, "Carlos Ramírez")
ana = Caller(ANA, Role.PROFESSOR, "Ana Torres")
saga = Caller(None, Role.SERVICE, "qone-workflow", "qone-workflow")
KEY = "subject-6f1d2c3e-9b7a-4e1f-8c2d-0a1b2c3d4e5f"
SLOTS = (Slot.parse("TUE", "10:00", "12:00"),)


@pytest.fixture
def world():
    store = InMemoryCatalog(list(SUBJECTS), list(SECTIONS))
    clock = FixedClock()
    return store, clock, CatalogService(store, store, store, clock, SequenceIds())


class TestSubjects:
    def test_anyone_signed_in_lists_newest_first_with_the_semester_filter(self, world):
        _, _, service = world
        page = service.list_subjects(laura, None, 1, 20)
        assert page.total == 3 and page.items[0].code == "ISW-701"  # same timestamp: by id, newest first
        assert [s.code for s in service.list_subjects(saga, 6, 1, 20).items] == ["ISW-604"]
        with pytest.raises(ValidationError) as caught:
            service.list_subjects(laura, 13, 1, 101)
        assert [d.field for d in caught.value.details] == ["limit", "semester"]

    def test_an_admin_creates_a_subject_with_its_event_and_the_key_replays(self, world):
        store, _, service = world
        result = service.create_subject(admin, CreateSubject("ISW-801", "Compiladores", 3, 8, ("ISW-604",)), KEY)
        assert result.created and result.item.id == UUID("99999999-9999-4999-8999-999999999901")
        assert [e.event_type for e in store.outbox] == ["SubjectCreated"]
        again = service.create_subject(admin, CreateSubject("ISW-999", "Other", 1, 1), KEY)
        assert not again.created and again.item == result.item
        assert service.get_subject(laura, "ISW-801").prerequisites == ("ISW-604",)

    def test_a_repeated_code_is_inv_sub_001(self, world):
        _, _, service = world
        with pytest.raises(BusinessRuleViolation) as caught:
            service.create_subject(admin, CreateSubject("ISW-604", "Dup", 3, 6), KEY)
        assert str(caught.value).startswith("INV-SUB-001")

    def test_an_unknown_or_cyclic_prerequisite_is_refused(self, world):
        _, _, service = world
        with pytest.raises(BusinessRuleViolation) as caught:
            service.create_subject(admin, CreateSubject("ISW-801", "Compiladores", 3, 8, ("NOP-999",)), KEY)
        assert "unknown prerequisite" in str(caught.value)

    @pytest.mark.parametrize("caller", [laura, carlos, saga])
    def test_only_an_admin_creates(self, world, caller):
        _, _, service = world
        with pytest.raises(Forbidden):
            service.create_subject(caller, CreateSubject("ISW-801", "Compiladores", 3, 8), KEY)

    def test_get_validates_the_code_and_answers_not_found(self, world):
        _, _, service = world
        with pytest.raises(ValidationError):
            service.get_subject(laura, "bad code")
        with pytest.raises(NotFound):
            service.get_subject(laura, "NOP-999")


class TestSections:
    def test_sections_of_a_subject_and_the_admin_and_professor_lists(self, world):
        _, _, service = world
        page = service.sections_of(laura, "ISW-604", "2026-B", 1, 20)
        assert {s.group_number for s in page.items} == {1, 3} and page.total == 2
        assert service.sections_of(laura, "ISW-604", "2025-A", 1, 20).total == 0
        with pytest.raises(NotFound):
            service.sections_of(laura, "NOP-999", None, 1, 20)
        assert service.list_sections(admin, None, None, 1, 20).total == 3
        assert [s.subject_code for s in service.list_sections(admin, None, ANA, 1, 20).items] == ["ISW-604"]
        assert {s.id for s in service.my_sections(carlos, "2026-B", 1, 20).items} == {ISW_604_G1.id, RED_501_G1.id}
        with pytest.raises(Forbidden):
            service.list_sections(carlos, None, None, 1, 20)
        with pytest.raises(Forbidden):
            service.my_sections(laura, None, 1, 20)

    def test_an_admin_opens_a_section_with_every_seat_free(self, world):
        store, clock, service = world
        command = OpenSection("ISW-604", "2026-B", 4, ANA, "Ana Torres", 25, SLOTS)
        result = service.open_section(admin, command, "section-key-0001")
        assert result.created and result.item.seats_available == 25 and result.item.subject_name == ISW_604.name
        assert store.outbox[-1].event_type == "SectionCreated"
        assert not service.open_section(admin, command, "section-key-0001").created
        assert service.get_section(laura, result.item.id) == result.item

    def test_an_unknown_subject_a_repeated_group_and_overlapping_slots(self, world):
        _, _, service = world
        with pytest.raises(NotFound):
            service.open_section(
                admin, OpenSection("NOP-999", "2026-B", 1, ANA, "Ana Torres", 25, SLOTS), "section-key-0002"
            )
        with pytest.raises(BusinessRuleViolation) as caught:
            service.open_section(
                admin, OpenSection("ISW-604", "2026-B", 1, ANA, "Ana Torres", 25, SLOTS), "section-key-0003"
            )
        assert "group 1" in str(caught.value)
        overlapping = (Slot.parse("MON", "08:00", "10:00"), Slot.parse("MON", "09:00", "11:00"))
        with pytest.raises(BusinessRuleViolation) as caught:
            service.open_section(
                admin, OpenSection("ISW-604", "2026-B", 5, ANA, "Ana Torres", 25, overlapping), "section-key-0004"
            )
        assert str(caught.value).startswith("INV-SEC-002")


class TestReservations:
    def reserve(self, service, section=ISW_604_G1, key="saga-1:reserve-seat", student=LAURA):
        return service.reserve(saga, ReserveSeat(section.id, student, uuid4()), key)

    def test_the_saga_reserves_a_seat_for_fifteen_minutes_and_the_key_replays(self, world):
        store, clock, service = world
        result = self.reserve(service)
        reservation = result.item
        assert result.created and reservation.status is ReservationStatus.RESERVED
        assert reservation.expires_at == clock.now() + timedelta(minutes=15)
        assert store.find(ISW_604_G1.id).seats_available == 26
        assert [e.event_type for e in store.outbox] == ["SeatReserved"] and store.outbox[0].payload[
            "seatsAvailable"
        ] == 26
        again = self.reserve(service)
        assert not again.created and again.item == reservation and store.find(ISW_604_G1.id).seats_available == 26

    def test_a_full_section_is_no_seats_and_one_seat_goes_to_exactly_one_reservation(self, world):
        store, _, service = world
        with pytest.raises(BusinessRuleViolation) as caught:
            self.reserve(service, ISW_604_G3, "saga-2:reserve-seat")
        assert str(caught.value).startswith("NO_SEATS") and store.outbox == []
        one_seat = store.sections[ISW_604_G1.id]
        store.sections[ISW_604_G1.id] = one_seat.__class__(**{**one_seat.__dict__, "seats_available": 1})
        first = self.reserve(service, key="saga-3:reserve-seat")
        with pytest.raises(BusinessRuleViolation):
            self.reserve(service, key="saga-4:reserve-seat", student=uuid4())
        assert first.created and store.find(ISW_604_G1.id).seats_available == 0

    def test_release_returns_the_seat_once_and_commit_makes_it_definitive(self, world):
        store, _, service = world
        reservation = self.reserve(service).item
        service.release(saga, ISW_604_G1.id, reservation.id)
        assert store.find(ISW_604_G1.id).seats_available == 27 and store.outbox[-1].event_type == "SeatReleased"
        service.release(saga, ISW_604_G1.id, reservation.id)  # idempotent: no second seat, no second event
        assert store.find(ISW_604_G1.id).seats_available == 27 and len(store.outbox) == 2
        with pytest.raises(InvalidStatusTransition):
            service.commit(saga, ISW_604_G1.id, reservation.id)
        other = self.reserve(service, key="saga-5:reserve-seat").item
        committed = service.commit(saga, ISW_604_G1.id, other.id)
        assert committed.status is ReservationStatus.COMMITTED and store.outbox[-1].event_type == "SeatCommitted"
        assert service.commit(saga, ISW_604_G1.id, other.id) == committed and len(store.outbox) == 4
        with pytest.raises(InvalidStatusTransition):
            service.release(saga, ISW_604_G1.id, other.id)

    def test_only_the_service_token_touches_reservations_and_unknown_ids_are_not_found(self, world):
        _, _, service = world
        for caller in (laura, carlos, admin):
            with pytest.raises(Forbidden):
                service.reserve(caller, ReserveSeat(ISW_604_G1.id, LAURA, uuid4()), KEY)
        with pytest.raises(NotFound):
            service.reserve(saga, ReserveSeat(uuid4(), LAURA, uuid4()), KEY)
        with pytest.raises(NotFound):
            service.commit(saga, ISW_604_G1.id, uuid4())
        with pytest.raises(ValidationError):
            service.reserve(saga, ReserveSeat(ISW_604_G1.id, LAURA, uuid4()), "short")
