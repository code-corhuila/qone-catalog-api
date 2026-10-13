"""Core level: slots, overlap, the Section aggregate and its seats."""

from datetime import UTC, datetime, time
from uuid import uuid4

import pytest

from catalog.domain.model.errors import BusinessRuleViolation, ValidationError
from catalog.domain.model.section import Section
from catalog.domain.model.slot import Day, Slot, any_overlap

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
MON_8_10 = Slot.parse("MON", "08:00", "10:00")


class TestSlot:
    def test_parse_reads_the_contract_strings(self):
        assert MON_8_10 == Slot(Day.MON, time(8, 0), time(10, 0))

    @pytest.mark.parametrize(
        ("day", "start", "end", "field"),
        [
            ("SUN", "08:00", "10:00", "slots.day"),
            ("MON", "8:00", "10:00", "slots.start"),
            ("MON", "08:00", "25:00", "slots.end"),
            ("MON", "10:00", "08:00", "slots.end"),
        ],
    )
    def test_an_invalid_slot_names_its_field(self, day, start, end, field):
        with pytest.raises(ValidationError) as caught:
            Slot.parse(day, start, end)
        assert caught.value.details[0].field == field

    def test_overlap_shares_the_day_and_the_interval(self):
        assert MON_8_10.overlaps(Slot.parse("MON", "09:00", "11:00"))
        assert not MON_8_10.overlaps(Slot.parse("MON", "10:00", "12:00")), (
            "ending when another starts is not an overlap"
        )
        assert not MON_8_10.overlaps(Slot.parse("TUE", "08:00", "10:00"))
        assert any_overlap([MON_8_10, Slot.parse("WED", "08:00", "10:00"), Slot.parse("MON", "09:30", "10:30")])


def open_section(**overrides):
    args = dict(
        section_id=uuid4(),
        subject_id=uuid4(),
        subject_code="ISW-604",
        subject_name="Sistemas Distribuidos",
        term="2026-B",
        group_number=1,
        professor_id=uuid4(),
        professor_name="Carlos Ramírez",
        capacity=30,
        slots=[Slot.parse("WED", "08:00", "10:00"), MON_8_10],
        now=NOW,
    )
    args.update(overrides)
    return Section.open(**args)


class TestSection:
    def test_a_section_opens_with_every_seat_free_and_the_slots_sorted(self):
        section = open_section()
        assert section.seats_available == 30 and section.capacity == 30 and not section.is_full
        assert [s.day.value for s in section.slots] == ["MON", "WED"]

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"term": "2026-C"}, "term"),
            ({"group_number": 0}, "groupNumber"),
            ({"professor_name": "C"}, "professorName"),
            ({"capacity": 501}, "capacity"),
            ({"slots": []}, "slots"),
        ],
    )
    def test_an_invalid_field_is_named(self, overrides, field):
        with pytest.raises(ValidationError) as caught:
            open_section(**overrides)
        assert [d.field for d in caught.value.details] == [field]

    def test_overlapping_slots_are_inv_sec_002(self):
        with pytest.raises(BusinessRuleViolation) as caught:
            open_section(slots=[MON_8_10, Slot.parse("MON", "09:00", "11:00")])
        assert str(caught.value).startswith("INV-SEC-002")

    def test_seats_are_taken_one_at_a_time_and_never_below_zero(self):
        section = open_section(capacity=1)
        taken = section.take_seat()
        assert taken.seats_available == 0 and taken.is_full
        with pytest.raises(BusinessRuleViolation) as caught:
            taken.take_seat()
        assert str(caught.value).startswith("NO_SEATS")
        assert taken.give_seat_back().seats_available == 1
        assert section.give_seat_back().seats_available == 1, "never above the capacity"
