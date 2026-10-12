"""The stores over PostgreSQL (schema `catalog` of qone-catalog-db) with psycopg 3 and a pool.

- Idempotent creation (norm 5.3.8): the row, its key (ON CONFLICT DO NOTHING) and its event in
  ONE transaction; a used key rolls back and returns the row the key created first.
- INV-SEC-001: a reservation locks the section row with SELECT ... FOR UPDATE, applies the
  domain rule and writes the new seat count, the reservation, the key and the event in the
  same transaction, so two concurrent reservations of the last seat give exactly one success.
"""

from datetime import UTC, time
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from catalog.application.port.outbound.ports import DuplicateCode, DuplicateGroup, ReserveFn, TransitionFn
from catalog.domain.model.events import DomainEvent
from catalog.domain.model.reservation import Reservation, ReservationStatus
from catalog.domain.model.section import Section
from catalog.domain.model.slot import Day, Slot
from catalog.domain.model.subject import Subject

SECTION_SELECT = """
SELECT s.id, s.subject_id, sub.code AS subject_code, sub.name AS subject_name, s.term, s.group_number,
       s.professor_id, s.professor_name, s.capacity, s.seats_available, s.created_at
FROM catalog.section s JOIN catalog.subject sub ON sub.id = s.subject_id
"""
RESERVATION_COLUMNS = "id, section_id, student_id, saga_id, status, expires_at, created_at, updated_at"


def connect(
    url: str, max_size: int = 10, wait_seconds: float = 5.0, statement_timeout_ms: int = 5000
) -> ConnectionPool:
    return ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=max_size,
        timeout=wait_seconds,
        kwargs={"options": f"-c statement_timeout={statement_timeout_ms}", "row_factory": dict_row},
        open=True,
    )


class _KeyAlreadyUsed(Exception):
    """Raised inside a transaction to roll it back when the Idempotency-Key existed."""


def _claim_key(conn: psycopg.Connection, key: str, resource_type: str, resource_id: UUID) -> None:
    taken = conn.execute(
        "INSERT INTO catalog.idempotency_key (key, resource_type, resource_id) VALUES (%s, %s, %s) "
        "ON CONFLICT (key) DO NOTHING RETURNING key",
        (key, resource_type, resource_id),
    ).fetchone()
    if taken is None:
        raise _KeyAlreadyUsed()


def _resource_of(conn: psycopg.Connection, key: str, resource_type: str) -> UUID | None:
    row = conn.execute(
        "SELECT resource_id FROM catalog.idempotency_key WHERE key = %s AND resource_type = %s", (key, resource_type)
    ).fetchone()
    return row["resource_id"] if row else None


def _write_event(conn: psycopg.Connection, event: DomainEvent) -> None:
    conn.execute(
        "INSERT INTO catalog.outbox (aggregate_type, aggregate_id, event_type, payload, occurred_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (event.aggregate_type, event.aggregate_id, event.event_type, Jsonb(event.payload), event.occurred_at),
    )


class PostgresSubjectStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find_by_code(self, code: str) -> Subject | None:
        with self._pool.connection() as conn:
            return _one_subject(conn, "WHERE code = %s", (code,))

    def find_by_key(self, idempotency_key: str) -> Subject | None:
        with self._pool.connection() as conn:
            subject_id = _resource_of(conn, idempotency_key, "subject")
            return _one_subject(conn, "WHERE id = %s", (subject_id,)) if subject_id else None

    def graph(self) -> dict[str, tuple[str, ...]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT s.code, coalesce(array_agg(p.code ORDER BY p.code) FILTER (WHERE p.code IS "
                "NOT NULL), '{}') AS prereqs "
                "FROM catalog.subject s LEFT JOIN catalog.subject_prerequisite sp ON sp.subject_id = s.id "
                "LEFT JOIN catalog.subject p ON p.id = sp.prerequisite_id GROUP BY s.code"
            ).fetchall()
        return {r["code"]: tuple(r["prereqs"]) for r in rows}

    def create(self, subject: Subject, idempotency_key: str, event: DomainEvent) -> tuple[Subject, bool]:
        try:
            with self._pool.connection() as conn, conn.transaction():
                try:
                    conn.execute(
                        "INSERT INTO catalog.subject (id, code, name, credits, semester, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (subject.id, subject.code, subject.name, subject.credits, subject.semester, subject.created_at),
                    )
                except psycopg.errors.UniqueViolation as cause:
                    raise DuplicateCode() from cause
                for code in subject.prerequisites:
                    conn.execute(
                        "INSERT INTO catalog.subject_prerequisite (subject_id, prerequisite_id) "
                        "SELECT %s, id FROM catalog.subject WHERE code = %s",
                        (subject.id, code),
                    )
                _claim_key(conn, idempotency_key, "subject", subject.id)
                _write_event(conn, event)
        except _KeyAlreadyUsed:
            earlier = self.find_by_key(idempotency_key)
            assert earlier is not None
            return earlier, False
        return subject, True

    def page(self, semester: int | None, page: int, limit: int) -> tuple[list[Subject], int]:
        where, params = ("WHERE semester = %s", (semester,)) if semester is not None else ("", ())
        with self._pool.connection() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM catalog.subject {where}", params).fetchone()["n"]
            ids = conn.execute(
                f"SELECT id FROM catalog.subject {where} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                (*params, limit, (page - 1) * limit),
            ).fetchall()
            items = [_one_subject(conn, "WHERE id = %s", (r["id"],)) for r in ids]
        return [s for s in items if s is not None], int(total)


def _one_subject(conn: psycopg.Connection, where: str, params: tuple) -> Subject | None:
    row = conn.execute(
        f"SELECT id, code, name, credits, semester, created_at FROM catalog.subject {where}", params
    ).fetchone()
    if row is None:
        return None
    prereqs = conn.execute(
        "SELECT p.code FROM catalog.subject_prerequisite sp JOIN catalog.subject p ON p.id = sp.prerequisite_id "
        "WHERE sp.subject_id = %s ORDER BY p.code",
        (row["id"],),
    ).fetchall()
    return Subject(
        row["id"],
        row["code"],
        row["name"],
        row["credits"],
        row["semester"],
        tuple(p["code"] for p in prereqs),
        row["created_at"].astimezone(UTC),
    )


class PostgresSectionStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find(self, section_id: UUID) -> Section | None:
        with self._pool.connection() as conn:
            return _one_section(conn, section_id)

    def find_by_key(self, idempotency_key: str) -> Section | None:
        with self._pool.connection() as conn:
            section_id = _resource_of(conn, idempotency_key, "section")
            return _one_section(conn, section_id) if section_id else None

    def create(self, section: Section, idempotency_key: str, event: DomainEvent) -> tuple[Section, bool]:
        try:
            with self._pool.connection() as conn, conn.transaction():
                try:
                    conn.execute(
                        "INSERT INTO catalog.section (id, subject_id, term, group_number, "
                        "professor_id, professor_name, capacity, "
                        "seats_available, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            section.id,
                            section.subject_id,
                            section.term,
                            section.group_number,
                            section.professor_id,
                            section.professor_name,
                            section.capacity,
                            section.seats_available,
                            section.created_at,
                        ),
                    )
                except psycopg.errors.UniqueViolation as cause:
                    raise DuplicateGroup() from cause
                for slot in section.slots:
                    conn.execute(
                        "INSERT INTO catalog.section_slot (section_id, day, start_time, end_time) "
                        "VALUES (%s, %s, %s, %s)",
                        (section.id, slot.day.value, slot.start, slot.end),
                    )
                _claim_key(conn, idempotency_key, "section", section.id)
                _write_event(conn, event)
        except _KeyAlreadyUsed:
            earlier = self.find_by_key(idempotency_key)
            assert earlier is not None
            return earlier, False
        return section, True

    def page(
        self, subject_id: UUID | None, term: str | None, professor_id: UUID | None, page: int, limit: int
    ) -> tuple[list[Section], int]:
        clauses, params = ["TRUE"], []
        for column, value in (("s.subject_id", subject_id), ("s.term", term), ("s.professor_id", professor_id)):
            if value is not None:
                clauses.append(f"{column} = %s")
                params.append(value)
        where = " AND ".join(clauses)
        with self._pool.connection() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM catalog.section s WHERE {where}", params).fetchone()["n"]
            rows = conn.execute(
                f"{SECTION_SELECT} WHERE {where} ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s",
                (*params, limit, (page - 1) * limit),
            ).fetchall()
            items = [_section(conn, r) for r in rows]
        return items, int(total)


def _one_section(conn: psycopg.Connection, section_id: UUID, lock: bool = False) -> Section | None:
    sql = f"{SECTION_SELECT} WHERE s.id = %s" + (" FOR UPDATE OF s" if lock else "")
    row = conn.execute(sql, (section_id,)).fetchone()
    return _section(conn, row) if row else None


def _section(conn: psycopg.Connection, row: dict) -> Section:
    slots = conn.execute(
        "SELECT day, start_time, end_time FROM catalog.section_slot WHERE section_id = %s", (row["id"],)
    ).fetchall()
    return Section(
        id=row["id"],
        subject_id=row["subject_id"],
        subject_code=row["subject_code"],
        subject_name=row["subject_name"],
        term=row["term"],
        group_number=row["group_number"],
        professor_id=row["professor_id"],
        professor_name=row["professor_name"],
        capacity=row["capacity"],
        seats_available=row["seats_available"],
        slots=tuple(sorted(Slot(Day(s["day"]), _time(s["start_time"]), _time(s["end_time"])) for s in slots)),
        created_at=row["created_at"].astimezone(UTC),
    )


def _time(value) -> time:
    return value if isinstance(value, time) else time.fromisoformat(str(value))


class PostgresReservationStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find_by_key(self, idempotency_key: str) -> Reservation | None:
        with self._pool.connection() as conn:
            reservation_id = _resource_of(conn, idempotency_key, "seat_reservation")
            if reservation_id is None:
                return None
            row = conn.execute(
                f"SELECT {RESERVATION_COLUMNS} FROM catalog.seat_reservation WHERE id = %s", (reservation_id,)
            ).fetchone()
        return _reservation(row) if row else None

    def find(self, section_id: UUID, reservation_id: UUID) -> Reservation | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {RESERVATION_COLUMNS} FROM catalog.seat_reservation WHERE id = %s AND section_id = %s",
                (reservation_id, section_id),
            ).fetchone()
        return _reservation(row) if row else None

    def reserve(self, section_id: UUID, idempotency_key: str, build: ReserveFn) -> tuple[Reservation, bool] | None:
        earlier = self.find_by_key(idempotency_key)
        if earlier is not None:
            return earlier, False
        try:
            with self._pool.connection() as conn, conn.transaction():
                section = _one_section(conn, section_id, lock=True)  # INV-SEC-001: the row is ours until commit
                if section is None:
                    return None
                section, reservation, event = build(section)
                conn.execute(
                    "UPDATE catalog.section SET seats_available = %s WHERE id = %s",
                    (section.seats_available, section.id),
                )
                conn.execute(
                    f"INSERT INTO catalog.seat_reservation ({RESERVATION_COLUMNS}) VALUES (%s, %s, "
                    "%s, %s, %s, %s, %s, %s)",
                    _reservation_values(reservation),
                )
                _claim_key(conn, idempotency_key, "seat_reservation", reservation.id)
                _write_event(conn, event)
        except _KeyAlreadyUsed:
            first = self.find_by_key(idempotency_key)
            assert first is not None
            return first, False
        return reservation, True

    def transition(self, section_id: UUID, reservation_id: UUID, apply: TransitionFn) -> Reservation | None:
        with self._pool.connection() as conn, conn.transaction():
            section = _one_section(conn, section_id, lock=True)
            if section is None:
                return None
            row = conn.execute(
                f"SELECT {RESERVATION_COLUMNS} FROM catalog.seat_reservation WHERE id = %s AND "
                "section_id = %s FOR UPDATE",
                (reservation_id, section_id),
            ).fetchone()
            if row is None:
                return None
            section, reservation, event = apply(section, _reservation(row))
            conn.execute(
                "UPDATE catalog.section SET seats_available = %s WHERE id = %s", (section.seats_available, section.id)
            )
            conn.execute(
                "UPDATE catalog.seat_reservation SET status = %s, updated_at = %s WHERE id = %s",
                (reservation.status.value, reservation.updated_at, reservation.id),
            )
            if event is not None:
                _write_event(conn, event)
        return reservation


def _reservation_values(r: Reservation) -> tuple:
    return (r.id, r.section_id, r.student_id, r.saga_id, r.status.value, r.expires_at, r.created_at, r.updated_at)


def _reservation(row: dict) -> Reservation:
    return Reservation(
        id=row["id"],
        section_id=row["section_id"],
        student_id=row["student_id"],
        saga_id=row["saga_id"],
        status=ReservationStatus(row["status"]),
        expires_at=row["expires_at"].astimezone(UTC),
        created_at=row["created_at"].astimezone(UTC),
        updated_at=row["updated_at"].astimezone(UTC),
    )
