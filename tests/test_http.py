"""HTTP level (Annex C): the checks of norm 5.3.12 against this service's resources, plus the
saga's: reservations for the SERVICE token only, one seat to exactly one reservation, release
and commit idempotent."""

from uuid import uuid4

import pytest

from tests.http_support import (
    ANA,
    CARLOS,
    ISW_604_G1,
    ISW_604_G3,
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
        r = client.get(f"{BASE}/sections/not-a-uuid", headers={**laura(), "X-Correlation-Id": "e2e-saga-1"})
        assert r.status_code == 400 and r.json()["details"] == [{"field": "id", "message": "must be a UUID"}]
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


class TestSections:
    def test_sections_of_a_subject_mine_and_the_admin_list(self, client):
        page = client.get(f"{BASE}/subjects/ISW-604/sections?term=2026-B", headers=laura()).json()
        assert page["meta"]["total"] == 2 and page["data"][0]["slots"][0]["day"]
        assert client.get(f"{BASE}/subjects/NOP-999/sections", headers=laura()).status_code == 404
        mine = client.get(f"{BASE}/sections/mine", headers=bearer(CARLOS, "PROFESSOR", "Carlos Ramírez")).json()
        assert {s["subjectCode"] for s in mine["data"]} == {"ISW-604", "RED-501"}
        assert client.get(f"{BASE}/sections/mine", headers=laura()).status_code == 403
        assert client.get(f"{BASE}/sections?professorId={ANA}", headers=admin()).json()["meta"]["total"] == 1
        assert client.get(f"{BASE}/sections", headers=laura()).status_code == 403
        assert client.get(f"{BASE}/sections?professorId=x", headers=admin()).status_code == 400
        assert client.get(f"{BASE}/sections/{ISW_604_G1}", headers=laura()).json()["seatsAvailable"] == 27

    def test_open_a_section_and_its_rules(self, client):
        r = client.post(f"{BASE}/sections", json=SECTION, headers={**admin(), "Idempotency-Key": "section-key-0001"})
        assert (
            r.status_code == 201
            and r.json()["seatsAvailable"] == 25
            and r.json()["subjectName"] == "Sistemas Distribuidos"
        )
        assert r.headers["Location"] == f"{BASE}/sections/{r.json()['id']}"
        assert (
            client.post(
                f"{BASE}/sections", json=SECTION, headers={**admin(), "Idempotency-Key": "section-key-0001"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"{BASE}/sections",
                json={**SECTION, "subjectCode": "NOP-999"},
                headers={**admin(), "Idempotency-Key": "section-key-0002"},
            ).status_code
            == 404
        )
        overlap = {
            **SECTION,
            "groupNumber": 5,
            "slots": [
                {"day": "MON", "start": "08:00", "end": "10:00"},
                {"day": "MON", "start": "09:00", "end": "11:00"},
            ],
        }
        r = client.post(f"{BASE}/sections", json=overlap, headers={**admin(), "Idempotency-Key": "section-key-0003"})
        assert r.status_code == 422 and r.json()["message"].startswith("INV-SEC-002")
        bad = client.post(
            f"{BASE}/sections",
            json={**SECTION, "slots": [{"day": "SUN", "start": "8:00", "end": "10:00"}], "professorId": "x"},
            headers={**admin(), "Idempotency-Key": "section-key-0004"},
        )
        assert bad.status_code == 400 and [d["field"] for d in bad.json()["details"]] == [
            "slots.0.day",
            "slots.0.start",
            "professorId",
        ]


class TestReservations:
    def test_the_service_reserves_once_releases_once_and_commits_once(self, client):
        r = reservation_of(client)
        assert r.status_code == 201 and r.json()["status"] == "RESERVED"
        reservation = r.json()["id"]
        assert r.headers["Location"] == f"{BASE}/sections/{ISW_604_G1}/reservations/{reservation}"
        assert reservation_of(client).status_code == 200
        assert client.get(f"{BASE}/sections/{ISW_604_G1}", headers=laura()).json()["seatsAvailable"] == 26
        assert (
            client.delete(f"{BASE}/sections/{ISW_604_G1}/reservations/{reservation}", headers=saga()).status_code == 204
        )
        assert (
            client.delete(f"{BASE}/sections/{ISW_604_G1}/reservations/{reservation}", headers=saga()).status_code == 204
        )
        assert client.get(f"{BASE}/sections/{ISW_604_G1}", headers=laura()).json()["seatsAvailable"] == 27
        r = client.post(f"{BASE}/sections/{ISW_604_G1}/reservations/{reservation}/commit", headers=saga())
        assert r.status_code == 422 and r.json()["error"] == "INVALID_STATUS_TRANSITION"
        other = reservation_of(client, key="saga-2:reserve-seat").json()["id"]
        committed = client.post(f"{BASE}/sections/{ISW_604_G1}/reservations/{other}/commit", headers=saga())
        assert committed.status_code == 200 and committed.json()["status"] == "COMMITTED"
        assert (
            client.post(f"{BASE}/sections/{ISW_604_G1}/reservations/{other}/commit", headers=saga()).json()
            == committed.json()
        )
        assert client.delete(f"{BASE}/sections/{ISW_604_G1}/reservations/{other}", headers=saga()).status_code == 422

    def test_a_full_section_is_no_seats_and_only_the_service_may_reserve(self, client):
        r = reservation_of(client, ISW_604_G3, "saga-3:reserve-seat")
        assert r.status_code == 422 and r.json()["message"].startswith("NO_SEATS")
        assert (
            client.post(
                f"{BASE}/sections/{ISW_604_G1}/reservations",
                json={"studentId": LAURA, "sagaId": str(uuid4())},
                headers={**admin(), **KEY},
            ).status_code
            == 403
        )
        assert reservation_of(client, str(uuid4()), "saga-4:reserve-seat").status_code == 404
        bad = client.post(
            f"{BASE}/sections/{ISW_604_G1}/reservations", json={"studentId": "x", "sagaId": "y"}, headers=saga()
        )
        assert [d["field"] for d in bad.json()["details"]] == ["Idempotency-Key", "studentId", "sagaId"]
