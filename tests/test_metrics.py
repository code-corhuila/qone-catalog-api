"""HTTP level (HU-OBS-001): GET /metrics at the root of the app in the Prometheus text format,
every request counted under its route template (never the raw path) and timed."""

from tests.http_support import ISW_604_G1, LAURA, bearer, make_client

BASE = "/api/v1/catalog"
SERVICE = 'service="qone-catalog-api"'


def scrape(client) -> str:
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    return r.text


def line_of(text: str, name: str, *labels: str) -> str:
    for line in text.splitlines():
        if line.startswith(name + "{") and all(label in line for label in labels):
            return line
    raise AssertionError(f"no {name} sample with {labels}")


def test_metrics_answers_the_prometheus_text_with_the_common_names():
    client, _ = make_client()
    text = scrape(client)
    assert "# TYPE http_server_requests_total counter" in text
    assert "# TYPE http_server_request_duration_seconds histogram" in text


def test_a_templated_route_is_counted_under_its_template_with_its_status_class():
    client, _ = make_client()
    student = bearer(LAURA, "STUDENT")
    for _ in range(2):
        assert client.get(f"{BASE}/sections/{ISW_604_G1}", headers=student).status_code == 200
    assert client.get(f"{BASE}/subjects").status_code == 401
    text = scrape(client)
    route = f'route="{BASE}/sections/{{section_id}}"'
    assert line_of(text, "http_server_requests_total", SERVICE, 'method="GET"', route, 'status_class="2xx"').endswith(
        " 2.0"
    )
    assert line_of(text, "http_server_requests_total", f'route="{BASE}/subjects"', 'status_class="4xx"').endswith(
        " 1.0"
    )
    assert line_of(text, "http_server_request_duration_seconds_bucket", route, 'le="0.05"')
    assert line_of(text, "http_server_request_duration_seconds_count", route).endswith(" 2.0")
    assert ISW_604_G1 not in text


def test_an_unknown_path_is_unmatched_and_the_scrape_itself_is_not_counted():
    client, _ = make_client()
    client.get(f"{BASE}/no-such-route/123")
    scrape(client)
    text = scrape(client)
    assert line_of(text, "http_server_requests_total", 'route="unmatched"', 'status_class="4xx"').endswith(" 1.0")
    assert 'route="/metrics"' not in text and "no-such-route" not in text


def test_every_app_has_its_own_registry():
    first, _ = make_client()
    second, _ = make_client()
    first.get(f"{BASE}/health")
    assert f'route="{BASE}/health"' in scrape(first)
    assert f'route="{BASE}/health"' not in scrape(second)
