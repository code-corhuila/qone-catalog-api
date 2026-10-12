"""Core level: the reservation states of the saga."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from catalog.domain.model.errors import InvalidStatusTransition
from catalog.domain.model.reservation import Reservation, ReservationStatus

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)


def take():
    return Reservation.take(uuid4(), uuid4(), uuid4(), uuid4(), NOW)


def test_a_reservation_holds_a_seat_for_fifteen_minutes():
    r = take()
    assert r.status is ReservationStatus.RESERVED and r.holds_a_seat
    assert r.expires_at == NOW + timedelta(minutes=15)


def test_commit_once_then_unchanged():
    committed, changed = take().commit(LATER)
    assert changed and committed.status is ReservationStatus.COMMITTED and committed.updated_at == LATER
    again, changed_again = committed.commit(LATER + timedelta(minutes=1))
    assert not changed_again and again == committed


def test_release_returns_the_seat_once_and_is_idempotent_afterwards():
    released, seat_back = take().release(LATER)
    assert seat_back and released.status is ReservationStatus.RELEASED and not released.holds_a_seat
    again, seat_back_again = released.release(LATER)
    assert not seat_back_again and again == released


def test_terminal_states_refuse_the_other_transition():
    committed, _ = take().commit(LATER)
    with pytest.raises(InvalidStatusTransition):
        committed.release(LATER)
    released, _ = take().release(LATER)
    with pytest.raises(InvalidStatusTransition):
        released.commit(LATER)


def test_expiry_only_after_expires_at_and_only_when_reserved():
    r = take()
    assert r.expire(NOW + timedelta(minutes=14)) == (r, False)
    expired, changed = r.expire(NOW + timedelta(minutes=16))
    assert changed and expired.status is ReservationStatus.EXPIRED
    assert expired.expire(NOW + timedelta(minutes=20)) == (expired, False)
    with pytest.raises(InvalidStatusTransition):
        expired.commit(NOW + timedelta(minutes=20))
