"""HTTP level (Annex C): the checks of norm 5.3.12 against this service's resources, plus the
saga's: reservations for the SERVICE token only, one seat to exactly one reservation, release
and commit idempotent."""

from uuid import uuid4

import pytest

from tests.http_support import (
    ANA,
    ISW_604_G1,
    LAURA,
    OTHER_PRIVATE,
    PATRICIA,
    bearer,
    make_client,
    mint,
)

BASE = "/api/v1/catalog"
KEY = {"Idempotency-Key": "subject-6f1d2c3e-9b7a-4e1f-8c2d-0a1b2c3d4e5f"}
SUBJECT = {"code": "ISW-801", "name": "Compiladores", "credits": 3, "semester": 8, "prerequisites": ["ISW-604"]}
SECTION = {
    "subjectCode": "ISW-604",
    "term": "2026-B",
    "groupNumber": 4,
    "professorId": ANA,
    "professorName": "Ana Torres",
    "capacity": 25,
    "slots": [{"day": "TUE", "start": "10:00", "end": "12:00"}],
}


@pytest.fixture
def client():
    client, _ = make_client()
    return client


def admin():
    return bearer(PATRICIA, "ADMIN", "Patricia Mora")


def laura():
    return bearer(LAURA, "STUDENT", "Laura Gómez")


def saga():
    return bearer("qone-workflow", "SERVICE", "qone-workflow")


def reservation_of(client, section=ISW_604_G1, key="saga-1:reserve-seat", student=LAURA):
    return client.post(
        f"{BASE}/sections/{section}/reservations",
        json={"studentId": student, "sagaId": str(uuid4())},
        headers={**saga(), "Idempotency-Key": key},
    )


class TestAuthenticationAndEnvelope:
    def test_health_without_a_token_and_401_variants(self, client):
        assert client.get(f"{BASE}/health").json()["service"] == "qone-catalog-api"
        assert client.get(f"{BASE}/subjects").status_code == 401
        for kw in ({"alg": "HS256"}, {"minutes": -5}, {"key": OTHER_PRIVATE}, {"exp": None}):
            token = mint(LAURA, "STUDENT", **kw)
            assert client.get(f"{BASE}/subjects", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_the_envelope_the_correlation_and_the_404s(self, client):
        r = client.get(f"{BASE}/subjects/not_a_code", headers={**laura(), "X-Correlation-Id": "e2e-saga-1"})
        assert r.status_code == 400 and r.json()["details"] == [
            {"field": "code", "message": "must match ^[A-Z0-9-]{3,10}$"}
        ]
        assert r.json()["traceId"] == "e2e-saga-1" and r.headers["X-Correlation-Id"] == "e2e-saga-1"
        assert len(client.get(f"{BASE}/health").headers["X-Correlation-Id"]) == 36
        assert client.get(f"{BASE}/subjects/NOP-999", headers=laura()).json()["error"] == "NOT_FOUND"
        assert client.get(f"{BASE}/nothing", headers=laura()).status_code == 404


class TestSubjects:
    def test_list_get_and_filters(self, client):
        body = client.get(f"{BASE}/subjects", headers=laura()).json()
        assert set(body) == {"data", "meta"} and body["meta"]["total"] == 3
        assert [s["code"] for s in client.get(f"{BASE}/subjects?semester=6", headers=laura()).json()["data"]] == [
            "ISW-604"
        ]
        assert client.get(f"{BASE}/subjects/ISW-604", headers=laura()).json()["prerequisites"] == ["RED-501"]
        for query in ("limit=101", "page=0", "semester=13", "semester=x"):
            assert client.get(f"{BASE}/subjects?{query}", headers=laura()).status_code == 400

    def test_create_201_with_location_replay_200_and_the_rules(self, client):
        r = client.post(f"{BASE}/subjects", json=SUBJECT, headers={**admin(), **KEY})
        assert r.status_code == 201 and r.headers["Location"] == f"{BASE}/subjects/ISW-801"
        assert client.post(f"{BASE}/subjects", json=SUBJECT, headers={**admin(), **KEY}).status_code == 200
        dup = client.post(
            f"{BASE}/subjects",
            json={**SUBJECT, "code": "ISW-604", "prerequisites": []},
            headers={**admin(), "Idempotency-Key": "subject-key-00002"},
        )
        assert dup.status_code == 422 and dup.json()["message"].startswith("INV-SUB-001")
        assert client.post(f"{BASE}/subjects", json=SUBJECT, headers={**laura(), **KEY}).status_code == 403
        bad = client.post(f"{BASE}/subjects", json={**SUBJECT, "credits": 1.5, "code": "x"}, headers=admin())
        # A body of the wrong shape is rejected by the framework before the header is read.
        assert bad.status_code == 400 and "credits" in {d["field"] for d in bad.json()["details"]}
        assert (
            client.post(
                f"{BASE}/subjects", content="{nope", headers={**admin(), **KEY, "Content-Type": "application/json"}
            ).status_code
            == 400
        )
