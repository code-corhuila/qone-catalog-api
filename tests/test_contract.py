"""Contract level: the responses validate against the synced copy of qone-catalog-api.yaml and
every path the contract declares is served."""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from tests.http_support import ANA, ISW_604_G1, LAURA, PATRICIA, bearer, make_client

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "openapi"
CATALOG = yaml.safe_load((CONTRACTS / "qone-catalog-api.yaml").read_text(encoding="utf-8"))
SHARED = yaml.safe_load((CONTRACTS / "_shared.yaml").read_text(encoding="utf-8"))
BASE = "/api/v1/catalog"


def inline(node, doc):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            source = SHARED if ref.startswith("./_shared.yaml#") else doc
            target = source
            for part in ref.split("#/")[1].split("/"):
                target = target[part]
            return inline(target, source)
        out = {k: inline(v, doc) for k, v in node.items() if k != "nullable"}
        return {"anyOf": [out, {"type": "null"}]} if node.get("nullable") else out
    if isinstance(node, list):
        return [inline(v, doc) for v in node]
    return node


def assert_valid(body, name: str, doc=CATALOG):
    schema = inline(doc["components"]["schemas"][name], doc)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(body))
    assert not errors, [e.message for e in errors]


@pytest.fixture
def client():
    client, _ = make_client()
    return client


def test_every_path_of_the_contract_is_served(client):
    served = set(client.app.openapi()["paths"])
    for path in CATALOG["paths"]:
        pattern = "/api/v1" + path.replace("{id}", "{section_id}").replace("{reservationId}", "{reservation_id}")
        assert pattern in served, f"{path} is in the contract but not served"


def test_subjects_sections_and_reservations_match_their_schemas(client):
    student, admin, saga = bearer(LAURA, "STUDENT"), bearer(PATRICIA, "ADMIN"), bearer("qone-workflow", "SERVICE")
    assert_valid(client.get(f"{BASE}/health").json(), "Health", SHARED)
    assert_valid(client.get(f"{BASE}/subjects", headers=student).json(), "SubjectPage")
    assert_valid(client.get(f"{BASE}/subjects/ISW-604", headers=student).json(), "Subject")
    body = {"code": "ISW-801", "name": "Compiladores", "credits": 3, "semester": 8, "prerequisites": ["ISW-604"]}
    assert_valid(body, "SubjectRequest")
    assert_valid(
        client.post(f"{BASE}/subjects", json=body, headers={**admin, "Idempotency-Key": "contract-subject-1"}).json(),
        "Subject",
    )
    assert_valid(client.get(f"{BASE}/subjects/ISW-604/sections", headers=student).json(), "SectionPage")
    assert_valid(client.get(f"{BASE}/sections/{ISW_604_G1}", headers=student).json(), "Section")
    section = {
        "subjectCode": "ISW-604",
        "term": "2026-B",
        "groupNumber": 4,
        "professorId": ANA,
        "professorName": "Ana Torres",
        "capacity": 25,
        "slots": [{"day": "TUE", "start": "10:00", "end": "12:00"}],
    }
    assert_valid(section, "SectionRequest")
    assert_valid(
        client.post(
            f"{BASE}/sections", json=section, headers={**admin, "Idempotency-Key": "contract-section-1"}
        ).json(),
        "Section",
    )
    r = client.post(
        f"{BASE}/sections/{ISW_604_G1}/reservations",
        json={"studentId": LAURA, "sagaId": str(uuid4())},
        headers={**saga, "Idempotency-Key": "contract-reserve-1"},
    )
    assert r.status_code == 201
    assert_valid(r.json(), "Reservation")
    assert_valid(
        client.post(f"{BASE}/sections/{ISW_604_G1}/reservations/{r.json()['id']}/commit", headers=saga).json(),
        "Reservation",
    )


@pytest.mark.parametrize(
    ("method", "path", "auth", "status", "code"),
    [
        ("GET", "/subjects?limit=101", ("STUDENT", LAURA), 400, "VALIDATION_ERROR"),
        ("GET", "/subjects", None, 401, "UNAUTHORIZED"),
        ("GET", "/sections", ("STUDENT", LAURA), 403, "FORBIDDEN"),
        ("GET", "/subjects/NOP-999", ("STUDENT", LAURA), 404, "NOT_FOUND"),
        ("GET", "/no-such-route", None, 404, "NOT_FOUND"),
    ],
)
def test_every_error_matches_the_shared_error_schema(client, method, path, auth, status, code):
    headers = bearer(auth[1], auth[0]) if auth else {}
    r = client.request(method, f"{BASE}{path}", headers=headers)
    assert r.status_code == status and r.json()["error"] == code
    assert_valid(r.json(), "Error", SHARED)
