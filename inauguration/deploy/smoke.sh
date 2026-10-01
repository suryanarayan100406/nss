#!/usr/bin/env bash
#
# The checks that can only be run against a live VM.
#
# pytest proves the application's behaviour; it cannot prove that Nginx is wired to
# it correctly, and every rule that has ever broken this deployment lives in that gap.
# This script walks the real site over real HTTP and asserts the things that produce a
# blank page rather than an error.
#
#     deploy/smoke.sh routing      # Pass A — routing, safe to run at any time
#     deploy/smoke.sh lifecycle    # Pass B — the full ceremony, on a real site
#     deploy/smoke.sh all
#
# Pass A reads the site mode first and asserts the set that matches it, so it is worth
# running again after the cut: it then checks the mirror image — the pages that were
# held back are served, and the inauguration system is gone.
#
# Pass C is Pass B run once more on the real VM with the real date, before the event.
# There is no separate mode for it because there is nothing extra to assert — the
# point of Pass C is that it is the second time.
#
# Pass B signs in, flips the site mode, and completes the ceremony. It does NOT run
# the cleanup unless you ask for it by name, because that one is irreversible.
#
# Environment:
#   NSS_BASE_URL          default https://nss.iiitnr.ac.in
#   NSS_ADMIN_USER        admin account, required by `lifecycle`
#   NSS_ADMIN_PASSWORD    required by `lifecycle`
#   NSS_CONFIRM_CLEANUP=YES   required by `lifecycle --with-cleanup`

set -uo pipefail

readonly PROGRAM=nss-smoke
readonly BASE="${NSS_BASE_URL:-https://nss.iiitnr.ac.in}"
readonly ADMIN_USER="${NSS_ADMIN_USER:-}"
readonly ADMIN_PASSWORD="${NSS_ADMIN_PASSWORD:-}"
readonly CLEANUP_PHRASE="DELETE INAUGURATION SYSTEM"

readonly JAR="$(mktemp)"
readonly TMPBODY="$(mktemp)"
trap 'rm -f "$JAR" "$TMPBODY"' EXIT

PASSED=0
FAILED=0
SKIPPED=0

green() { printf '\033[32m  ok  \033[0m %s\n' "$*"; }
red()   { printf '\033[31m fail \033[0m %s\n' "$*"; }
skip()  { printf '\033[33m skip \033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

pass() { PASSED=$((PASSED + 1)); green "$1"; }
fail() { FAILED=$((FAILED + 1)); red "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; return 0; }
skipped() { SKIPPED=$((SKIPPED + 1)); skip "$1"; }

check() { # check <name> <expected> <actual>
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected $2, got $3"; fi
}

# --- primitives ---------------------------------------------------------------

# Status of a single request. No -L: a redirect is a result worth asserting on.
status() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }

# Status following redirects, plus curl's exit code. Exit 47 is "Maximum (50)
# redirects followed" — the loop this deployment is most likely to grow.
follow_status() {
    local out
    out=$(curl -sL -o /dev/null -w '%{http_code}' --max-time 20 "$@" 2>/dev/null)
    local rc=$?
    printf '%s %s' "$rc" "${out:-000}"
}

body() { curl -sL --max-time 20 "$@" 2>/dev/null; }

url() { printf '%s%s' "$BASE" "$1"; }

# A JSON field, without requiring jq on the VM.
field() { sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^,\"}]*\)\"\{0,1\}.*/\1/p" <<<"$1" | head -1; }

# --- Pass A — routing ---------------------------------------------------------

#: Mirrors nginx_mode.REQUIRED_REACHABLE. Kept as one literal list so the smoke test
#: and the pytest that renders the include cannot drift; a change to one is a change
#: to the other, and tests/test_smoke_manifest.py asserts they agree.
readonly FILES=(
    /nss_logo.jpg
    /css/style.css
    /js/main.js
    /vendor/gsap.min.js
    /assets/events/blood/blood-1.jpg
)

#: The pages that must not be reachable while the site is held.
readonly PERMANENT_PAGES=(
    /index.html
    /team.html
)

pass_routing() {
    head_ "Pass A — routing"

    # Which mode is the site in? The same URL means something different in each, so
    # the assertions have to be chosen rather than run unconditionally — otherwise a
    # perfectly working site gets reported as broken, which is how a smoke test stops
    # being trusted and starts being ignored.
    #
    # The status code, not the body, is what identifies the end state. After the
    # cleanup the route is gone and answers 404 — but so is a route behind a dead
    # backend, and reading that second case as `permanent` would run the launched-site
    # rules against a held site. Only a 404 means permanent; a backend that is merely
    # unreachable is its own, clearer failure.
    local config code mode
    config=$(body "$(url /api/public/config)")
    code=$(status "$(url /api/public/config)")
    if [ "$code" = "404" ]; then
        mode=permanent
    elif grep -q '"mode"' <<<"$config"; then
        mode=$(field "$config" mode)
    else
        fail "/api/public/config answers" "got $code: $(head -c 120 <<<"$config")"
        return 1
    fi
    printf '    site mode: %s\n' "$mode"

    # The three modes mean three different things by the same URLs, so the mode picks
    # the set of assertions. `coming_soon` is the one that falls through to the checks
    # below; the other two have their own.
    case "$mode" in
        permanent)    pass_routing_permanent;    return $? ;;
        inauguration) pass_routing_inauguration; return $? ;;
    esac

    # 1. The redirect must terminate. The hold page is a redirect target and a
    #    `.html` catch-all matches it, so a rule ordered wrong loops forever.
    local rc code
    read -r rc code <<<"$(follow_status "$(url /coming-soon.html)")"
    check "/coming-soon.html answers without a redirect loop" "0 200" "$rc $code"
    [ "$rc" = "47" ] && fail "/coming-soon.html redirect loop" "curl gave up after 50 redirects"

    read -r rc code <<<"$(follow_status "$(url /)")"
    check "/ answers without a redirect loop" "0 200" "$rc $code"

    # 2. The hold body is the reference. Every held page has to return exactly it —
    #    not merely "a 200", which an accidentally-served permanent page would also
    #    produce.
    local hold
    hold=$(body "$(url /coming-soon.html)")
    [ -n "$hold" ] || { fail "the hold page has a body"; return 1; }
    pass "the hold page has a body"

    check "/ serves the hold page" "$hold" "$(body "$(url /)")"

    local page
    for page in "${PERMANENT_PAGES[@]}"; do
        check "$page is held back" "$hold" "$(body "$(url "$page")")"
    done

    # event-*.html are enumerated by globbing the deployed web root, so a new one is
    # covered the day it is added rather than the day someone remembers to list it.
    local found=0 file
    for file in /var/www/nss/event-*.html; do
        [ -e "$file" ] || continue
        found=1
        check "/$(basename "$file") is held back" "$hold" "$(body "$(url "/$(basename "$file")")")"
    done
    [ "$found" -eq 1 ] || skipped "no event-*.html found in /var/www/nss to check"

    # 3. The ceremony page must not be servable before the ceremony.
    check "/inauguration.html is held back" "$hold" "$(body "$(url /inauguration.html)")"

    # 4. The assets. This is the trap the plan names: /nss_logo.jpg is not under a
    #    ^~ prefix, so an unqualified `location /` redirect strips the branding off
    #    the hold page and nothing else reports it.
    local path
    for path in "${FILES[@]}"; do
        check "$path returns 200" "200" "$(status "$(url "$path")")"
    done

    # favicon.ico is not in the repository and never has been. There is no file to
    # serve, so 404 is the correct answer — what matters is that the mode's rules do
    # not redirect it (which strips the tab icon of its identity and, in a bad
    # ordering, loops) and do not 5xx it.
    local fav
    fav=$(status "$(url /favicon.ico)")
    if [ "${fav:0:1}" = "3" ]; then
        fail "/favicon.ico is not redirected" "got $fav"
    elif [ "${fav:0:1}" = "5" ]; then
        fail "/favicon.ico is not a server error" "got $fav"
    else
        pass "/favicon.ico answers without a redirect or a 5xx (got $fav)"
    fi

    # 5. The API answers, and answers JSON. A dead backend must not look like a dead
    #    site, but a *reachable* backend is what Pass B needs.
    local config
    config=$(body "$(url /api/public/config)")
    if grep -q '"mode"' <<<"$config"; then
        pass "/api/public/config returns JSON with a mode"
    else
        fail "/api/public/config returns JSON with a mode" "got: $(head -c 120 <<<"$config")"
    fi

    # 6. The admin portal renders. Its API enforces auth; the page itself is public.
    check "/admin/ returns 200" "200" "$(status "$(url /admin/)")"

    # 7. Nothing from the permanent site may leak into a held response.
    if grep -q 'id="coming-soon-css"' <<<"$hold"; then
        pass "the hold page is the coming-soon page"
    else
        fail "the hold page is the coming-soon page" "no coming-soon marker in the body"
    fi
}

# --- Pass A, while the ceremony is live ---------------------------------------

#: Between the enable step and the cut the site is in inauguration mode, and every URL
#: means something different again: `/` is the ceremony, the hold page is gone, and the
#: permanent pages are held back by the ceremony rather than by the countdown.
#: Without this branch "safe to run at any time" would be untrue for the half hour
#: before the event — which is exactly when somebody would run it.
pass_routing_inauguration() {
    local rc code
    read -r rc code <<<"$(follow_status "$(url /)")"
    check "/ answers without a redirect loop" "0 200" "$rc $code"

    local ceremony
    ceremony=$(body "$(url /)")
    if grep -q 'class="live-head"\|The Unveiling' <<<"$ceremony"; then
        pass "/ serves the ceremony page"
    else
        fail "/ serves the ceremony page" "no ceremony marker in the body"
    fi

    # The countdown has done its job: a bookmarked hold page is sent to the ceremony.
    check "/coming-soon.html leads to the ceremony" "$ceremony" "$(body "$(url /coming-soon.html)")"

    # The permanent pages are still held back — but held by the ceremony now. A
    # leftover coming-soon rule would be the bug, and it is what the first branch
    # below is looking for.
    local page served
    for page in "${PERMANENT_PAGES[@]}"; do
        served=$(body "$(url "$page")")
        if grep -q 'id="coming-soon-css"' <<<"$served"; then
            fail "$page is held by the ceremony, not the countdown" "the hold page came back"
        elif [ "$served" = "$ceremony" ]; then
            pass "$page is held back by the ceremony"
        else
            fail "$page is held back" "the page itself was served"
        fi
    done

    local path
    for path in "${FILES[@]}"; do
        check "$path returns 200" "200" "$(status "$(url "$path")")"
    done

    local fav
    fav=$(status "$(url /favicon.ico)")
    if [ "${fav:0:1}" = "3" ] || [ "${fav:0:1}" = "5" ]; then
        fail "/favicon.ico answers without a redirect or a 5xx" "got $fav"
    else
        pass "/favicon.ico answers without a redirect or a 5xx (got $fav)"
    fi

    check "/api/public/config reports inauguration mode" "inauguration" \
        "$(field "$(body "$(url /api/public/config)")" mode)"
    check "/admin/ returns 200" "200" "$(status "$(url /admin/)")"
}

# --- Pass A, after the ceremony -----------------------------------------------

#: What Pass A asserts once the site is permanent: the mirror image of the held
#: checks. The same paths are requested, and every one of them that used to be
#: redirected now has to be served.
pass_routing_permanent() {
    local rc code
    read -r rc code <<<"$(follow_status "$(url /)")"
    check "/ answers without a redirect loop" "0 200" "$rc $code"

    local home
    home=$(body "$(url /)")
    if grep -q 'id="coming-soon-css"' <<<"$home"; then
        fail "/ serves the permanent home page" "the hold page is still being served"
    elif grep -qi "<title>NSS" <<<"$home"; then
        pass "/ serves the permanent home page"
    else
        fail "/ serves the permanent home page" "no NSS title in the body"
    fi

    # The pages that were held back are now served. What this is really asking is
    # whether the catch-all that used to hold them is still there — a leftover
    # `location / { return 302 ... }` serves the hold page with a 200, so a status
    # check alone would call that a pass.
    #
    # It is deliberately not "each page differs from /": in permanent mode / *is*
    # index.html, so that check would fail on a site that is working perfectly.
    local page served
    for page in "${PERMANENT_PAGES[@]}"; do
        check "$page returns 200" "200" "$(status "$(url "$page")")"
        served=$(body "$(url "$page")")
        if grep -q 'id="coming-soon-css"' <<<"$served"; then
            fail "$page is served, not held" "the hold page came back"
        else
            pass "$page is served, not held"
        fi
    done

    local path
    for path in "${FILES[@]}"; do
        check "$path returns 200" "200" "$(status "$(url "$path")")"
    done

    local fav
    fav=$(status "$(url /favicon.ico)")
    if [ "${fav:0:1}" = "3" ] || [ "${fav:0:1}" = "5" ]; then
        fail "/favicon.ico answers without a redirect or a 5xx" "got $fav"
    else
        pass "/favicon.ico answers without a redirect or a 5xx (got $fav)"
    fi

    # The inauguration system is gone. If the backend is still running this catches
    # a cleanup that stopped half way — the one failure mode that leaves the site
    # serving correctly while the system it was meant to remove is still there.
    check "/admin/ is gone" "404" "$(status "$(url /admin/)")"
    check "/api/public/config is gone" "404" "$(status "$(url /api/public/config)")"
    check "/inauguration.html is gone" "404" "$(status "$(url /inauguration.html)")"
}

# --- Pass B — the lifecycle ---------------------------------------------------

api() { # api <method> <path> [json]
    local method=$1 path=$2 data=${3:-} csrf
    csrf=$(awk '$6 == "nss_admin_csrf" { print $7 }' "$JAR" 2>/dev/null | tail -1)
    if [ -n "$data" ]; then
        curl -s -b "$JAR" -c "$JAR" -X "$method" --max-time 20 \
            -H 'Content-Type: application/json' \
            -H "Origin: $BASE" \
            -H "X-CSRF-Token: ${csrf:-}" \
            -d "$data" "$(url "$path")" 2>/dev/null
    else
        curl -s -b "$JAR" -c "$JAR" -X "$method" --max-time 20 \
            -H "Origin: $BASE" -H "X-CSRF-Token: ${csrf:-}" "$(url "$path")" 2>/dev/null
    fi
}

api_status() { # like api, but the status code
    local method=$1 path=$2 data=${3:-} csrf
    csrf=$(awk '$6 == "nss_admin_csrf" { print $7 }' "$JAR" 2>/dev/null | tail -1)
    if [ -n "$data" ]; then
        curl -s -o "$TMPBODY" -w '%{http_code}' -b "$JAR" -c "$JAR" -X "$method" --max-time 20 \
            -H 'Content-Type: application/json' -H "Origin: $BASE" \
            -H "X-CSRF-Token: ${csrf:-}" -d "$data" "$(url "$path")" 2>/dev/null
    else
        curl -s -o "$TMPBODY" -w '%{http_code}' -b "$JAR" -c "$JAR" -X "$method" --max-time 20 \
            -H "Origin: $BASE" -H "X-CSRF-Token: ${csrf:-}" "$(url "$path")" 2>/dev/null
    fi
}

mode() { field "$(body "$(url /api/public/config)")" mode; }

pass_lifecycle() {
    local with_cleanup=${1:-0}
    head_ "Pass B — the full lifecycle"

    if [ -z "$ADMIN_USER" ] || [ -z "$ADMIN_PASSWORD" ]; then
        skipped "lifecycle needs NSS_ADMIN_USER and NSS_ADMIN_PASSWORD"
        return 0
    fi

    local before
    before=$(mode)
    printf '    starting mode: %s\n' "$before"

    # --- the authorization check, over real HTTP ------------------------------
    # This is the one the whole design rests on. Done before signing in, so the
    # session jar is empty and the request looks exactly like a visitor's.
    local anon_code
    anon_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -X POST \
        -H 'Content-Type: application/json' -H "Origin: $BASE" \
        -d '{"by":"smoke test"}' "$(url /api/ceremony/cut)" 2>/dev/null)
    if [ "$anon_code" = "401" ] || [ "$anon_code" = "403" ]; then
        pass "an unauthenticated cut is refused ($anon_code)"
    else
        fail "an unauthenticated cut is refused" "expected 401 or 403, got $anon_code"
    fi
    check "...and it changed nothing" "$before" "$(mode)"

    # --- sign in ---------------------------------------------------------------
    local code
    code=$(api_status POST /api/admin/login \
        "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASSWORD\"}")
    if [ "$code" != "200" ]; then
        fail "admin login succeeds" "got $code: $(head -c 200 <"$TMPBODY")"
        return 1
    fi
    pass "admin login succeeds"

    # --- the cut is still refused without the CSRF header ----------------------
    # A session alone must not be enough; check_csrf is the second half.
    local no_csrf
    no_csrf=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X POST --max-time 20 \
        -H 'Content-Type: application/json' -H "Origin: $BASE" \
        -d '{"by":"smoke test"}' "$(url /api/ceremony/cut)" 2>/dev/null)
    if [ "$no_csrf" = "403" ]; then
        pass "a signed-in cut without a CSRF token is refused (403)"
    else
        fail "a signed-in cut without a CSRF token is refused" "expected 403, got $no_csrf"
    fi

    # --- move to the ceremony --------------------------------------------------
    if [ "$before" != "inauguration" ]; then
        code=$(api_status POST /api/admin/ceremony/enable)
        if [ "$code" = "200" ]; then
            pass "the ceremony can be enabled"
        else
            fail "the ceremony can be enabled" "got $code: $(head -c 200 <"$TMPBODY")"
            return 1
        fi
    fi

    check "the site is in inauguration mode" "inauguration" "$(mode)"

    local ceremony
    ceremony=$(body "$(url /)")
    if grep -q 'id="liveBody"\|class="live-head"\|The Unveiling' <<<"$ceremony"; then
        pass "/ serves the ceremony page"
    else
        fail "/ serves the ceremony page" "no ceremony marker in the body"
    fi

    # --- commit ----------------------------------------------------------------
    code=$(api_status POST /api/ceremony/cut '{"by":"smoke test (Pass B)"}')
    if [ "$code" = "200" ]; then
        pass "the ceremony can be committed by an admin"
    else
        fail "the ceremony can be committed by an admin" "got $code: $(head -c 300 <"$TMPBODY")"
        return 1
    fi

    check "the site is now permanent" "permanent" "$(mode)"

    local home
    home=$(body "$(url /)")
    if grep -q 'id="coming-soon-css"' <<<"$home"; then
        fail "/ serves index.html" "the hold page is still being served"
    elif grep -qi "<title>NSS" <<<"$home"; then
        pass "/ serves the permanent home page"
    else
        fail "/ serves the permanent home page" "no NSS title in the body"
    fi

    local gone
    gone=$(status "$(url /admin/)")
    if [ "$gone" = "404" ]; then
        pass "/admin/ is gone (404)"
    else
        fail "/admin/ is gone" "expected 404, got $gone"
    fi

    # --- the cleanup, only when explicitly asked -------------------------------
    if [ "$with_cleanup" -ne 1 ]; then
        skipped "the cleanup (pass --with-cleanup to run it; it is irreversible)"
        return 0
    fi

    head_ "Pass B — the cleanup"
    if [ "${NSS_CONFIRM_CLEANUP:-}" != "YES" ]; then
        skipped "the cleanup (set NSS_CONFIRM_CLEANUP=YES as well)"
        return 0
    fi

    code=$(api_status POST /api/cleanup/run \
        "{\"phrase\":\"$CLEANUP_PHRASE\",\"password\":\"$ADMIN_PASSWORD\"}")
    if [ "$code" = "200" ]; then
        pass "the cleanup accepted the phrase and password"
    else
        fail "the cleanup accepted the phrase and password" "got $code: $(head -c 300 <"$TMPBODY")"
        return 1
    fi

    sleep 2
    check "/ still serves the permanent home page after the cleanup" "$home" "$(body "$(url /)")"
    check "/admin/ is still gone after the cleanup" "404" "$(status "$(url /admin/)")"

    if systemctl is-active --quiet nss-inauguration 2>/dev/null; then
        fail "the backend is stopped after the cleanup"
    else
        pass "the backend is stopped after the cleanup"
    fi

    # The script is idempotent. This is the cheapest possible proof of it, and the
    # one that matters if somebody runs it twice by mistake.
    if sudo -n /usr/local/sbin/nss-decommission >/dev/null 2>&1; then
        pass "re-running the cleanup is a clean no-op"
    else
        fail "re-running the cleanup is a clean no-op" "it exited non-zero the second time"
    fi
}

# --- main ---------------------------------------------------------------------

main() {
    local what=${1:-all} with_cleanup=0
    shift || true
    for arg in "$@"; do
        case "$arg" in
            --with-cleanup) with_cleanup=1 ;;
            *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
        esac
    done

    printf '\n\033[1mNSS smoke test\033[0m — %s\n' "$BASE"
    printf '%s\n' "------------------------------------------------------------"

    case "$what" in
        routing)   pass_routing ;;
        lifecycle) pass_lifecycle "$with_cleanup" ;;
        all)       pass_routing; pass_lifecycle "$with_cleanup" ;;
        *)         printf 'usage: %s {routing|lifecycle|all} [--with-cleanup]\n' "$PROGRAM" >&2; exit 2 ;;
    esac

    printf '\n%s\n' "------------------------------------------------------------"
    printf '\033[1m%d passed, %d failed, %d skipped\033[0m\n\n' "$PASSED" "$FAILED" "$SKIPPED"

    [ "$FAILED" -eq 0 ] || exit 1
    return 0
}

main "$@"
