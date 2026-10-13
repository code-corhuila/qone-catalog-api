#!/usr/bin/env sh
# Smoke checks of the catalog image by HTTP (norm 5.3.12, without a token): health, the
# envelope on 401 and on an unknown route, the correlation header. Usage:
#   deploy/smoke.sh http://localhost:8010
set -eu
BASE="${1:-http://localhost:8010}"
fail() { echo "FAIL: $1" >&2; exit 1; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

[ "$(status "$BASE/api/v1/catalog/health")" = "200" ] || fail "/health is not 200"
curl -s "$BASE/api/v1/catalog/health" | grep -q '"service":"qone-catalog-api"' || fail "health does not name the service"
[ "$(status "$BASE/api/v1/catalog/subjects")" = "401" ] || fail "a request without a token is not 401"
curl -s "$BASE/api/v1/catalog/subjects" | grep -q '"error":"UNAUTHORIZED"' || fail "401 has no envelope"
[ "$(status "$BASE/api/v1/catalog/nothing")" = "404" ] || fail "an unknown route is not 404"
curl -s "$BASE/api/v1/catalog/nothing" | grep -q '"error":"NOT_FOUND"' || fail "404 has no envelope"
corr=$(curl -s -D - -o /dev/null -H 'X-Correlation-Id: smoke-1' "$BASE/api/v1/catalog/health" | tr -d '\r' | grep -i '^x-correlation-id:' | cut -d' ' -f2)
[ "$corr" = "smoke-1" ] || fail "X-Correlation-Id is not returned"
echo "OK: catalog service smoke checks passed against $BASE"
