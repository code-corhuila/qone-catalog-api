# qone-catalog-api

> catalog bounded context: service API

Part of the **Qampus (Quorum One)** distributed system — team `quorum-one`, Grupo 1.
Governance and documentation live in [`qone-docs`](https://github.com/code-corhuila/qone-docs).

## Purpose

The service of the **catalog** domain (course norm 5.3, Annex C): subjects with prerequisites,
sections with schedule and seats, and the seat reservations the enrollment saga takes, releases
and commits (ADR-008, steps `reserve-seat` and `commit-seat`). Python 3.12 + FastAPI, hexagonal
(ADR-007): the domain and the use cases import no framework and no driver; a test enforces it.

- Contract: `07-api/contracts/openapi/qone-catalog-api.yaml` (CA-01..CA-12) in `qone-docs`.
- Data: `qone-catalog-db` owns the schema `catalog`; this service never versions it.
- Piece page: `09-microservices/services/03-catalog-api/README.md`. Stories: `code-corhuila/qone-docs#37`, `#38`, `#39` (HU-CAT-001..003).

## Layout (Annex C, Python)

```
src/catalog/
├── domain/model/            subject.py (acyclic prerequisites), section.py (slots, seats), reservation.py (saga states), slot.py, events.py, errors.py
├── application/port/        inbound/ (use cases offered) · outbound/ (stores, clock, ids)
├── application/usecase/     the operations of CA-02..CA-12 and the authorization by role
└── adapter/
    ├── inbound/http/        app.py, auth.py (RS256), errors.py (envelope), router.py
    └── outbound/            persistence/ (memory, postgres with the row lock of INV-SEC-001)
apps/api/__main__.py         composition root
tests/                       domain, use cases, HTTP, contract, architecture, postgres integration
```

## Rules it keeps

- The subject code is unique (INV-SUB-001); the prerequisite graph stays acyclic (INV-SUB-002).
- The slots of a section do not overlap (INV-SEC-002); `seatsAvailable` never goes below zero
  (INV-SEC-001): the reservation decrements it under `SELECT ... FOR UPDATE`.
- A reservation is RESERVED for 15 minutes, then COMMITTED by the saga, RELEASED by its
  compensation or EXPIRED by `qone-worker`; commit and release are idempotent (D-04).
- Every creation is idempotent by `Idempotency-Key`; every change writes its event to the outbox.

## How to run it

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check .
python -m pytest                                   # domain, use cases, HTTP, contract, architecture
cp .env.example .env                               # JWT_PUBLIC_KEY (or _FILE), CATALOG_DATABASE_URL
python -m apps.api                                 # http://localhost:8000/api/v1/catalog/health
```

Without `CATALOG_DATABASE_URL` the service starts over an empty in-memory store (development
only; it says so in the log). With it, the store is `qone-catalog-db`: start that repository's
compose, run its Flyway runner, and point the URL at it.

Integration tests against the real schema and seeds (Annex C) run only when `TEST_DATABASE_URL`
is set; `ci.yml` clones `qone-catalog-db`, migrates a fresh PostgreSQL and runs them, including
two concurrent reservations of the last seat (exactly one succeeds):

```bash
TEST_DATABASE_URL=postgresql://catalog:password@localhost:5432/catalog python -m pytest tests/test_postgres_integration.py
```

Container image and compose (`deploy/`, Annex G): `docker build -f deploy/Dockerfile -t
qone-catalog-api:dev .`, then `deploy/smoke.sh` against the running container; `ci.yml` builds
and smoke-checks the image. `deploy/compose.yml` is what `qone-infra` includes: `expose` only,
after `catalog-db` is healthy.

Explicit limits (norm 5.3.10), declared in `apps/api/factory.py`: pool 10 connections with a
5 s wait, statement timeout 5 s, keep-alive 60 s, graceful shutdown 20 s; the header read
timeout is imposed by the gateway. A reservation lives `RESERVATION_TTL_MINUTES` (15).
Every log line is JSON with the `traceId`. `scripts/sync-contracts.sh [ref]` refreshes the
contract copies in `contracts/openapi/`.

## Branching

Three permanent branches. **None of them accepts a direct commit** — you enter through a child
branch and leave through a Pull Request.

```
develop  <--PR--  feat/... fix/... chore/...
qa       <--PR--  qa/...
main     <--PR--  release/...  hotfix/...
```

Promotion happens **by re-application** (`git cherry-pick -x`), never by merging one permanent
branch into another: `merge develop -> qa` and `merge qa -> main` do not exist in this model.

`main` requires **1 approval from `ariel5253`**. On `develop` and `qa` the team sets its own review
rule.

Full policy: `00-governance/branching-policy.md` in `qone-docs`.
