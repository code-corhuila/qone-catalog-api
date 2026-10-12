"""Core level: the Subject aggregate and the acyclic rule, no database."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from catalog.domain.model.errors import BusinessRuleViolation, ValidationError
from catalog.domain.model.subject import Subject, ensure_acyclic

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


def create(**overrides):
    args = dict(
        subject_id=uuid4(),
        code="ISW-801",
        name="Compiladores",
        credits=3,
        semester=8,
        prerequisites=["ISW-602"],
        now=NOW,
    )
    args.update(overrides)
    return Subject.create(**args)


def test_a_subject_is_created_with_its_prerequisite_codes():
    subject = create(code=" ISW-801 ", name=" Compiladores ")
    assert subject.code == "ISW-801" and subject.name == "Compiladores"
    assert subject.prerequisites == ("ISW-602",) and subject.created_at == NOW


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"code": "isw-801"}, "code"),
        ({"code": "AB"}, "code"),
        ({"name": "C"}, "name"),
        ({"credits": 0}, "credits"),
        ({"credits": 11}, "credits"),
        ({"semester": 13}, "semester"),
        ({"prerequisites": ["bad code"]}, "prerequisites"),
        ({"prerequisites": ["ISW-602", "ISW-602"]}, "prerequisites"),
        ({"prerequisites": ["ISW-801"]}, "prerequisites"),
    ],
)
def test_an_invalid_field_is_named(overrides, field):
    with pytest.raises(ValidationError) as caught:
        create(**overrides)
    assert [d.field for d in caught.value.details] == [field]


GRAPH = {"ISW-501": (), "ISW-601": ("ISW-501",), "RED-501": (), "ISW-604": ("RED-501",), "ISW-701": ("ISW-604",)}


def test_a_chain_without_a_cycle_is_accepted():
    ensure_acyclic("ISW-801", ["ISW-701", "ISW-601"], GRAPH)


def test_a_cycle_through_the_prerequisites_is_inv_sub_002():
    graph = dict(
        GRAPH, **{"RED-501": ("ISW-701",)}
    )  # RED-501 now needs ISW-701, which needs ISW-604, which needs RED-501
    with pytest.raises(BusinessRuleViolation) as caught:
        ensure_acyclic("RED-501", ["ISW-701"], graph)
    assert str(caught.value).startswith("INV-SUB-002")


def test_a_new_code_cannot_close_a_cycle_but_an_existing_one_can():
    ensure_acyclic("NEW-001", ["ISW-701"], GRAPH)
    with pytest.raises(BusinessRuleViolation):
        ensure_acyclic("ISW-501", ["ISW-601"], GRAPH)
