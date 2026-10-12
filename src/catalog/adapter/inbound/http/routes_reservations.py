"""Reservation routes of the enrollment saga (CA-10..CA-12) on the catalog router: SERVICE
token only, enforced by the use case."""

from uuid import UUID

from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr

from catalog.adapter.inbound.http.router import (
    PREFIX,
    Current,
    UseCases,
    created,
    idempotency_key_of,
    parse_uuid,
    rfc3339,
    router,
)
from catalog.application.port.inbound.catalog import ReserveSeat
from catalog.domain.model.errors import FieldError, ValidationError
from catalog.domain.model.reservation import Reservation


class ReservationBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    studentId: StrictStr  # noqa: N815
    sagaId: StrictStr  # noqa: N815


@router.post("/sections/{section_id}/reservations")
def reserve(section_id: str, body: ReservationBody, who: Current, service: UseCases, request: Request):
    # The border names every invalid input it can see at once: the header and the ids together.
    details: list[FieldError] = []
    key = ""
    try:
        key = idempotency_key_of(request)
    except ValidationError as error:
        details.extend(error.details)
    ids: dict[str, UUID] = {}
    for field, value in (("id", section_id), ("studentId", body.studentId), ("sagaId", body.sagaId)):
        try:
            ids[field] = parse_uuid(value, field)
        except ValidationError as error:
            details.extend(error.details)
    if details:
        raise ValidationError(details)
    result = service.reserve(who, ReserveSeat(ids["id"], ids["studentId"], ids["sagaId"]), key)
    location = f"{PREFIX}/sections/{section_id}/reservations/{result.item.id}"
    return created(represent_reservation(result.item), result.created, location)


@router.delete("/sections/{section_id}/reservations/{reservation_id}")
def release(section_id: str, reservation_id: str, who: Current, service: UseCases):
    service.release(who, parse_uuid(section_id, "id"), parse_uuid(reservation_id, "reservationId"))
    return Response(status_code=204)


@router.post("/sections/{section_id}/reservations/{reservation_id}/commit")
def commit(section_id: str, reservation_id: str, who: Current, service: UseCases):
    return represent_reservation(
        service.commit(who, parse_uuid(section_id, "id"), parse_uuid(reservation_id, "reservationId"))
    )


def represent_reservation(reservation: Reservation) -> dict:
    return {
        "id": str(reservation.id),
        "sectionId": str(reservation.section_id),
        "studentId": str(reservation.student_id),
        "sagaId": str(reservation.saga_id),
        "status": reservation.status.value,
        "expiresAt": rfc3339(reservation.expires_at),
        "createdAt": rfc3339(reservation.created_at),
    }
