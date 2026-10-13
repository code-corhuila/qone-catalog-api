"""Section routes (CA-05..CA-09) on the catalog router. `/sections/mine` is registered before
`/sections/{section_id}` so the literal path wins."""

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from catalog.adapter.inbound.http.router import (
    DEFAULT_LIMIT,
    PREFIX,
    Current,
    UseCases,
    created,
    idempotency_key_of,
    page_of,
    parse_uuid,
    rfc3339,
    router,
)
from catalog.application.port.inbound.catalog import OpenSection
from catalog.domain.model.errors import FieldError, ValidationError
from catalog.domain.model.section import Section
from catalog.domain.model.slot import Slot


class SlotBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day: StrictStr
    start: StrictStr
    end: StrictStr


class SectionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subjectCode: StrictStr  # noqa: N815 - the contract's camelCase
    term: StrictStr
    groupNumber: StrictInt  # noqa: N815
    professorId: StrictStr  # noqa: N815
    professorName: StrictStr  # noqa: N815
    capacity: StrictInt
    slots: list[SlotBody]


@router.get("/subjects/{code}/sections")
def sections_of(
    code: str,
    who: Current,
    service: UseCases,
    page: int = Query(1),
    limit: int = Query(DEFAULT_LIMIT),
    term: str | None = Query(None),
):
    return page_of(service.sections_of(who, code, term, page, limit), represent_section)


@router.get("/sections")
def list_sections(
    who: Current,
    service: UseCases,
    page: int = Query(1),
    limit: int = Query(DEFAULT_LIMIT),
    term: str | None = Query(None),
    professorId: str | None = Query(None),  # noqa: N803 - the contract's camelCase
):
    professor = parse_uuid(professorId, "professorId") if professorId is not None else None
    return page_of(service.list_sections(who, term, professor, page, limit), represent_section)


@router.post("/sections")
def open_section(body: SectionBody, who: Current, service: UseCases, request: Request):
    key = idempotency_key_of(request)
    details: list[FieldError] = []
    slots: list[Slot] = []
    for i, slot in enumerate(body.slots):
        try:
            slots.append(Slot.parse(slot.day, slot.start, slot.end, field=f"slots.{i}"))
        except ValidationError as error:
            details.extend(error.details)
    professor = None
    try:
        professor = parse_uuid(body.professorId, "professorId")
    except ValidationError as error:
        details.extend(error.details)
    if details:
        raise ValidationError(details)
    assert professor is not None
    command = OpenSection(
        body.subjectCode, body.term, body.groupNumber, professor, body.professorName, body.capacity, tuple(slots)
    )
    result = service.open_section(who, command, key)
    return created(represent_section(result.item), result.created, f"{PREFIX}/sections/{result.item.id}")


@router.get("/sections/mine")
def my_sections(
    who: Current,
    service: UseCases,
    page: int = Query(1),
    limit: int = Query(DEFAULT_LIMIT),
    term: str | None = Query(None),
):
    return page_of(service.my_sections(who, term, page, limit), represent_section)


@router.get("/sections/{section_id}")
def get_section(section_id: str, who: Current, service: UseCases):
    return represent_section(service.get_section(who, parse_uuid(section_id, "id")))


def represent_section(section: Section) -> dict:
    return {
        "id": str(section.id),
        "subjectCode": section.subject_code,
        "subjectName": section.subject_name,
        "term": section.term,
        "groupNumber": section.group_number,
        "professorId": str(section.professor_id),
        "professorName": section.professor_name,
        "capacity": section.capacity,
        "seatsAvailable": section.seats_available,
        "slots": [
            {"day": s.day.value, "start": s.start.strftime("%H:%M"), "end": s.end.strftime("%H:%M")}
            for s in section.slots
        ],
        "createdAt": rfc3339(section.created_at),
    }
