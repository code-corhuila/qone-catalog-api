"""HU-PLT-004: the ready probe. Core level (the use case over fake probes), HTTP level (200 ready,
503 with the common envelope, public like /health) and the PostgreSQL probe against a database
that does not answer (no TEST_DATABASE_URL needed: nothing listens on that port)."""

import time

import pytest
from fastapi.testclient import TestClient

from catalog.adapter.inbound.http.app import create_app
from catalog.adapter.inbound.http.auth import TokenVerifier
from catalog.adapter.outbound.persistence.memory import InMemoryCatalog, InMemoryProbe
from catalog.application.errors import Unavailable
from catalog.application.usecase.catalog import CatalogService
from catalog.application.usecase.readiness import Readiness
from tests.fakes import FixedClock, SequenceIds
from tests.http_support import PUBLIC_PEM

READY = "/api/v1/catalog/health/ready"


class Probe:
    def __init__(self, name: str, up: bool) -> None:
        self.name, self.up, self.calls = name, up, 0

    def reachable(self) -> bool:
        self.calls += 1
        return self.up


def client_with(*probes) -> TestClient:
    store = InMemoryCatalog()
    service = CatalogService(store, store, store, FixedClock(), SequenceIds())
    app = create_app(service, TokenVerifier(PUBLIC_PEM), Readiness(list(probes)))
    return TestClient(app, raise_server_exceptions=False)


def test_ready_names_every_dependency_it_checked():
    assert Readiness([Probe("database", True)]).check() == {"database": "ok"}
    assert InMemoryProbe().name == "database" and InMemoryProbe().reachable()


def test_a_dependency_down_is_unavailable_with_its_name():
    with pytest.raises(Unavailable) as caught:
        Readiness([Probe("database", False)]).check()
    assert caught.value.message == "database is not reachable"


def test_ready_is_200_without_a_token():
    r = client_with(Probe("database", True)).get(READY)
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "service": "qone-catalog-api", "checks": {"database": "ok"}}


def test_ready_is_503_with_the_envelope_when_the_database_is_down_and_liveness_stays_200():
    client = client_with(Probe("database", False))
    r = client.get(READY, headers={"X-Correlation-Id": "ready-1"})
    assert r.status_code == 503
    assert r.json() == {
        "error": "SERVICE_UNAVAILABLE",
        "message": "database is not reachable",
        "traceId": "ready-1",
    }
    assert client.get("/api/v1/catalog/health").status_code == 200


def test_the_postgres_probe_answers_false_within_its_timeout_when_nothing_listens():
    from catalog.adapter.outbound.persistence.postgres import PostgresProbe, connect

    pool = connect("postgresql://nobody:nothing@127.0.0.1:1/none", max_size=1, wait_seconds=0.5)
    try:
        started = time.monotonic()
        assert PostgresProbe(pool, timeout_seconds=0.5).reachable() is False
        assert time.monotonic() - started < 3
    finally:
        pool.close(timeout=1)
