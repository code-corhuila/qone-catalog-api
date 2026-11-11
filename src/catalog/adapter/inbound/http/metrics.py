"""The RED metrics of the service (HU-OBS-001, ADR-010): a middleware that counts and times every
request under its route template, and GET /metrics at the root of the app for Prometheus, which
scrapes the container on the platform network (the gateway never routes it). Each app owns its
registry, so two apps in one process (the tests) never share a series."""

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

METRICS_PATH = "/metrics"
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10)
# The client chooses the method freely: anything else is folded into OTHER to bound the label.
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
OTHER_METHOD = "OTHER"


def install(app: FastAPI, service: str) -> CollectorRegistry:
    registry = CollectorRegistry()
    requests = Counter(
        "http_server_requests",
        "HTTP requests served, by route template and status class",
        ["service", "method", "route", "status_class"],
        registry=registry,
    )
    duration = Histogram(
        "http_server_request_duration_seconds",
        "Time to serve an HTTP request, by route template",
        ["service", "method", "route"],
        buckets=BUCKETS,
        registry=registry,
    )

    @app.middleware("http")
    async def measure(request: Request, call_next):
        if request.url.path == METRICS_PATH:
            return await call_next(request)
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"  # the template, never the raw path
        method = request.method if request.method in METHODS else OTHER_METHOD
        requests.labels(service, method, template, f"{response.status_code // 100}xx").inc()
        duration.labels(service, method, template).observe(time.perf_counter() - started)
        return response

    def scrape() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    app.add_api_route(METRICS_PATH, scrape, methods=["GET"], include_in_schema=False)
    app.state.metrics = registry
    return registry
