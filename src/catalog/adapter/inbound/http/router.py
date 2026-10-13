"""The router of the catalog service, the shared border helpers and the subject routes
(CA-01..CA-04); sections and reservations register their routes on the same router from
`routes_sections` and `routes_reservations`. Routes, validation at the border and response objects of CA-01..CA-12
(07-api/contracts/openapi/qone-catalog-api.yaml). Domain entities are never serialized
directly (Annex C)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from catalog.application.port.inbound.caller import Caller
from catalog.application.port.inbound.catalog import (
    DEFAULT_LIMIT,
    CatalogUseCases,
    CreateSubject,
)
from catalog.application.usecase.catalog import KEY_MAX, KEY_MIN
from catalog.domain.model.errors import FieldError, ValidationError
from catalog.domain.model.subject import Subject

PREFIX = "/api/v1/catalog"
router = APIRouter(prefix=PREFIX)


class SubjectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: StrictStr
    name: StrictStr
    credits: StrictInt
    semester: StrictInt
    prerequisites: list[StrictStr] = []


def use_cases(request: Request) -> CatalogUseCases:
    return request.app.state.use_cases


def caller(request: Request) -> Caller:
    return request.app.state.verifier.verify(request.headers.get("Authorization"))


UseCases = Annotated[CatalogUseCases, Depends(use_cases)]
Current = Annotated[Caller, Depends(caller)]
PageQ = Annotated[int, Query()]


@router.get("/health")
def health(request: Request):
    return {"status": "ok", "service": request.app.state.service, "version": request.app.state.version}


@router.get("/subjects")
def list_subjects(
    who: Current,
    service: UseCases,
    page: int = Query(1),
    limit: int = Query(DEFAULT_LIMIT),
    semester: int | None = Query(None),
):
    return page_of(service.list_subjects(who, semester, page, limit), represent_subject)


@router.post("/subjects")
def create_subject(body: SubjectBody, who: Current, service: UseCases, request: Request):
    key = idempotency_key_of(request)
    result = service.create_subject(
        who, CreateSubject(body.code, body.name, body.credits, body.semester, tuple(body.prerequisites)), key
    )
    return created(represent_subject(result.item), result.created, f"{PREFIX}/subjects/{result.item.code}")


@router.get("/subjects/{code}")
def get_subject(code: str, who: Current, service: UseCases):
    return represent_subject(service.get_subject(who, code))


def idempotency_key_of(request: Request) -> str:
    key = request.headers.get("Idempotency-Key", "")
    if not KEY_MIN <= len(key) <= KEY_MAX:
        raise ValidationError([FieldError("Idempotency-Key", "header required, 8 to 128 characters")])
    return key


def parse_uuid(value: str | None, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError([FieldError(field, "must be a UUID")]) from None


def created(payload: dict, is_new: bool, location: str) -> JSONResponse:
    if not is_new:
        return JSONResponse(payload, status_code=200)
    return JSONResponse(payload, status_code=201, headers={"Location": location})


def page_of(page, represent) -> dict:
    return {
        "data": [represent(i) for i in page.items],
        "meta": {"page": page.page, "limit": page.limit, "total": page.total, "totalPages": page.total_pages},
    }


def rfc3339(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def represent_subject(subject: Subject) -> dict:
    return {
        "id": str(subject.id),
        "code": subject.code,
        "name": subject.name,
        "credits": subject.credits,
        "semester": subject.semester,
        "prerequisites": list(subject.prerequisites),
        "createdAt": rfc3339(subject.created_at),
    }
