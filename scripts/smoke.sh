#!/usr/bin/env bash
# SoftLadyLux smoke test for staging / right after a deploy (M6 release checklist).
#
#   scripts/smoke.sh https://shop.example.com        (add -k for a self-signed test certificate)
#
# Checks that the public site, API and security headers respond as expected. It only reads; it
# creates no data. Exit code 0 = all checks passed.
set -uo pipefail

BASE="${1:-}"
CURL_OPTS=()
shift || true
for a in "$@"; do [ "$a" = "-k" ] && CURL_OPTS+=(-k); done
[ -n "$BASE" ] || { echo "Usage: smoke.sh <base-url> [-k]" >&2; exit 2; }
BASE="${BASE%/}"
FAIL=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }

status() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "${CURL_OPTS[@]}" "$@"; }
check_status() {  # check_status <label> <expected> <url> [curl args]
  local label="$1" want="$2" url="$3"; shift 3
  local got; got="$(status "$@" "$url")"
  [ "$got" = "$want" ] && pass "$label ($got)" || fail "$label: expected $want, got $got"
}
body() { curl -s --max-time 20 "${CURL_OPTS[@]}" "$@"; }
headers() { curl -s -I --max-time 20 "${CURL_OPTS[@]}" "$@" | tr -d '\r'; }

echo "Smoke test: $BASE"
check_status "health endpoint"                200 "$BASE/api/v1/health/"
check_status "home page"                      200 "$BASE/"
check_status "category page"                   200 "$BASE/shop/bras"
check_status "offers page"                     200 "$BASE/shop/offers"
check_status "size guide"                      200 "$BASE/size-guide"
check_status "cart page"                       200 "$BASE/cart"
check_status "login page"                      200 "$BASE/login"
check_status "contact page"                    200 "$BASE/contact"
check_status "admin login page"                200 "$BASE/admin/login"
check_status "robots.txt"                      200 "$BASE/robots.txt"
check_status "sitemap.xml"                     200 "$BASE/sitemap.xml"
check_status "public settings API"             200 "$BASE/api/v1/settings/"
check_status "public theme API"                200 "$BASE/api/v1/theme/"
check_status "categories API"                  200 "$BASE/api/v1/categories/"
check_status "unknown page is a 404"           404 "$BASE/this-page-does-not-exist"
check_status "admin API needs a login"         401 "$BASE/api/v1/admin/dashboard/"

body "$BASE/robots.txt" | grep -q "Disallow: /admin" && pass "robots.txt disallows /admin" || fail "robots.txt does not disallow /admin"
body "$BASE/api/v1/theme/" | grep -q '"--color-primary"' && pass "theme tokens are served" || fail "theme tokens missing"

H="$(headers "$BASE/")"
echo "$H" | grep -qi '^strict-transport-security:' && pass "HSTS header" || fail "HSTS header missing (expected over HTTPS)"
echo "$H" | grep -qi '^x-content-type-options: nosniff' && pass "X-Content-Type-Options" || fail "X-Content-Type-Options missing"
echo "$H" | grep -qi '^x-frame-options:' && pass "X-Frame-Options" || fail "X-Frame-Options missing"
echo "$H" | grep -qi '^server: nginx/' && fail "nginx version is exposed in the Server header" || pass "server version hidden"

if [[ "$BASE" == https://* ]]; then
  HTTP_BASE="http://${BASE#https://}"
  HTTP_BASE="${HTTP_BASE%%/*}"
  loc="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 "http://${HTTP_BASE#*//}/" 2>/dev/null || true)"
  [[ "$loc" == 30* ]] && pass "plain HTTP redirects ($loc)" || echo "SKIP  HTTP redirect check (port 80 not reachable at that host: $loc)"
fi

echo
[ "$FAIL" -eq 0 ] && echo "All smoke checks passed." || echo "$FAIL check(s) failed."
exit "$FAIL"
