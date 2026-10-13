"""A seat reservation of the enrollment saga (CA-10..CA-12, ADR-008 steps reserve-seat and
commit-seat). RESERVED is the only non-terminal state: commit makes it COMMITTED, release
makes it RELEASED (the seat returns), the worker makes it EXPIRED after `expires_at`.
Committing a committed reservation or releasing a released or expired one is a no-op (D-04);
every other transition out of a terminal state is InvalidStatusTransition."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from catalog.domain.model.errors import InvalidStatusTransition

DEFAULT_TTL = timedelta(minutes=15)


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class Reservation:
    id: UUID
    section_id: UUID
    student_id: UUID
    saga_id: UUID
    status: ReservationStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def take(
        reservation_id: UUID,
        section_id: UUID,
        student_id: UUID,
        saga_id: UUID,
        now: datetime,
        ttl: timedelta = DEFAULT_TTL,
    ) -> "Reservation":
        moment = _utc(now)
        return Reservation(
            reservation_id, section_id, student_id, saga_id, ReservationStatus.RESERVED, moment + ttl, moment, moment
        )

    @property
    def holds_a_seat(self) -> bool:
        return self.status in (ReservationStatus.RESERVED, ReservationStatus.COMMITTED)

    def commit(self, now: datetime) -> tuple["Reservation", bool]:
        if self.status is ReservationStatus.COMMITTED:
            return self, False
        if self.status is not ReservationStatus.RESERVED:
            raise InvalidStatusTransition(f"a {self.status.value} reservation cannot be committed")
        return replace(self, status=ReservationStatus.COMMITTED, updated_at=_utc(now)), True

    def release(self, now: datetime) -> tuple["Reservation", bool]:
        """(reservation, seat_returned): the seat returns only when it was RESERVED."""
        if self.status in (ReservationStatus.RELEASED, ReservationStatus.EXPIRED):
            return self, False
        if self.status is ReservationStatus.COMMITTED:
            raise InvalidStatusTransition("a COMMITTED reservation cannot be released")
        return replace(self, status=ReservationStatus.RELEASED, updated_at=_utc(now)), True

    def expire(self, now: datetime) -> tuple["Reservation", bool]:
        """The worker's sweep (CA-11): a RESERVED reservation past its expiry gives its seat back."""
        moment = _utc(now)
        if self.status is not ReservationStatus.RESERVED or moment < self.expires_at:
            return self, False
        return replace(self, status=ReservationStatus.EXPIRED, updated_at=moment), True


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
