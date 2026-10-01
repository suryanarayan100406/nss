#!/usr/bin/env bash
#
# Install the NSS inauguration system onto the institute VM.
#
# Takes no arguments. Re-runnable: running it again over a working installation
# re-deploys the code and leaves the state, the admin account and the session key
# exactly as they were.
#
# The order below is the point of the script. Nothing on the live system is replaced
# until a copy of what it is replacing has been taken and verified, and no
# configuration file is swapped in until `nginx -t` has accepted it — Nginx reads its
# configuration on reload, not on write, so a candidate can be put in place, tested,
# and rolled back without ever having been live.
#
# It does NOT: start the ceremony, change the mode, or remove anything. The mode is
# the backend's business, and removal is /usr/local/sbin/nss-decommission's.

set -euo pipefail

readonly PROGRAM=nss-inauguration-install
readonly SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR="$(cd "$SOURCE_DIR/.." && pwd)"

# --- fixed locations ---------------------------------------------------------
readonly INSTALL_DIR=/opt/nss-inauguration
readonly CONFIG_DIR=/opt/nss-inauguration/config
readonly VENV_DIR=/opt/nss-inauguration/venv
readonly ENV_FILE=/opt/nss-inauguration/config/nss.env
readonly WEB_ROOT=/var/www/nss
readonly BACKUP_ROOT=/var/backups/nss-inauguration
readonly LOG_DIR=/var/log/nss-inauguration
readonly NGINX_INC=/etc/nginx/nss-mode.inc
readonly NGINX_SITE=/etc/nginx/sites-available/nss
readonly NGINX_ENABLED=/etc/nginx/sites-enabled/nss
readonly NGINX_BIN=/usr/sbin/nginx
readonly UNIT=/etc/systemd/system/nss-inauguration.service
readonly SUDOERS=/etc/sudoers.d/nss-inauguration
readonly DECOMMISSION=/usr/local/sbin/nss-decommission

readonly SERVICE=nss-inauguration
readonly SERVICE_USER=nss-inaug
readonly SERVICE_GROUP=nss-inaug
readonly SITE_HOST=nss.iiitnr.ac.in

readonly TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31m%s: %s\033[0m\n' "$PROGRAM" "$*" >&2; exit 1; }

CURRENT_PHASE="startup"
on_error() {
    local line=$1
    printf '\n\033[31m%s: failed in phase "%s" (line %s).\033[0m\n' \
        "$PROGRAM" "$CURRENT_PHASE" "$line" >&2
    printf 'Nothing was removed. Re-run after fixing the cause; the script is re-runnable.\n' >&2
    exit 1
}
trap 'on_error $LINENO' ERR

# --- phase 0: preconditions --------------------------------------------------

phase_0_preconditions() {
    CURRENT_PHASE="preconditions"

    [ "$(id -u)" -eq 0 ] || die "must run as root (try: sudo $0)"

    for tool in python3 "$NGINX_BIN" systemctl visudo curl; do
        command -v "$tool" >/dev/null 2>&1 || [ -x "$tool" ] \
            || die "$tool is required but was not found"
    done

    [ -d "$WEB_ROOT" ] || die "$WEB_ROOT does not exist — is this the right machine?"
    [ -f "$WEB_ROOT/index.html" ] || die "$WEB_ROOT/index.html is missing; refusing to install over a site that is not there"
    [ -f "$REPO_DIR/inauguration.html" ] || die "$REPO_DIR/inauguration.html is missing"

    for required in web/coming-soon.html web/admin/index.html backend/app/main.py \
                    backend/requirements.txt deploy/sbin/nss-decommission \
                    deploy/nginx/nss deploy/systemd/nss-inauguration.service \
                    deploy/sudoers/nss-inauguration; do
        [ -e "$SOURCE_DIR/$required" ] || die "deploy tree is incomplete: $SOURCE_DIR/$required is missing"
    done

    note "source        $SOURCE_DIR"
    note "web root      $WEB_ROOT"
    note "service user  $SERVICE_USER"
}

# --- phase 1: the backup, before anything is touched -------------------------

phase_1_backup() {
    CURRENT_PHASE="backup"

    BACKUP_DIR="$BACKUP_ROOT/deploy-$TIMESTAMP"
    mkdir -p "$BACKUP_DIR"

    say "Snapshotting the live site to $BACKUP_DIR"
    # The whole web root, not just the files this script writes. If the install is
    # wrong, the way back has to be the site as it actually was, including whatever
    # was already there that nobody remembered.
    cp -a "$WEB_ROOT/." "$BACKUP_DIR/site/"

    mkdir -p "$BACKUP_DIR/nginx"
    # Each of these may legitimately not exist on a first install.
    for candidate in "$NGINX_SITE" "$NGINX_ENABLED" "$NGINX_INC" "$UNIT"; do
        if [ -e "$candidate" ]; then
            cp -a "$candidate" "$BACKUP_DIR/nginx/$(basename "$candidate")"
        fi
    done

    local expected actual
    expected=$(find "$WEB_ROOT" -type f | wc -l)
    actual=$(find "$BACKUP_DIR/site" -type f | wc -l)
    [ "$expected" -eq "$actual" ] \
        || die "backup is incomplete: $expected files in the web root, $actual in the copy"

    # -r so an empty tree does not leave sha256sum reading the terminal, and a single
    # `cd` so the manifest holds paths relative to the snapshot and verifies in place.
    ( cd "$BACKUP_DIR" && find . -type f ! -name MANIFEST.sha256 -print0 \
        | sort -z | xargs -0 -r sha256sum > MANIFEST.sha256 )
    ( cd "$BACKUP_DIR" && sha256sum -c --quiet MANIFEST.sha256 ) \
        || die "the snapshot does not verify against its own manifest"

    note "$actual files, checksummed and verified"
    note "restore with: deploy/rollback.sh --backup deploy-$TIMESTAMP"
}

# --- phase 2: the service account --------------------------------------------

phase_2_account() {
    CURRENT_PHASE="service account"

    if id -u "$SERVICE_USER" >/dev/null 2>&1; then
        note "$SERVICE_USER already exists; leaving it alone"
    else
        say "Creating the $SERVICE_USER system account"
        # No shell, no home, not in any privileged group. This account is the
        # boundary that keeps the backend away from the permanent site.
        useradd --system --no-create-home --home-dir "$INSTALL_DIR" \
                --shell /usr/sbin/nologin "$SERVICE_USER"
    fi

    for group in sudo adm www-data; do
        if id -nG "$SERVICE_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$group"; then
            die "$SERVICE_USER is in the $group group; that is a wider grant than this system needs"
        fi
    done
}

# --- phase 3: directories and permissions ------------------------------------

phase_3_directories() {
    CURRENT_PHASE="directories"

    say "Creating directories"
    mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$BACKUP_ROOT" "$LOG_DIR" \
             "$(dirname "$DECOMMISSION")" \
             "$(dirname "$NGINX_INC")" "$(dirname "$NGINX_SITE")"

    # The install tree is root-owned and read-only to the service; only config/ is
    # the service's to write.
    chown -R root:root "$INSTALL_DIR"
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$CONFIG_DIR" "$BACKUP_ROOT" "$LOG_DIR"
    chmod 0750 "$INSTALL_DIR" "$CONFIG_DIR" "$BACKUP_ROOT" "$LOG_DIR"

    # The include is regenerated on every mode change, so the service owns the file
    # itself. It does not own /etc/nginx, and it does not own sites-available/nss.
    if [ ! -e "$NGINX_INC" ]; then
        install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0644 /dev/null "$NGINX_INC"
    fi
}

# --- phase 4: the application and its virtualenv -----------------------------

phase_4_application() {
    CURRENT_PHASE="application"

    say "Installing the backend into $INSTALL_DIR"
    # config/ holds the state, the admin account and the session key. It is never
    # part of what gets copied, so a re-run cannot clobber a live ceremony.
    rm -rf "$INSTALL_DIR/app" "$INSTALL_DIR/README.md"
    cp -a "$SOURCE_DIR/backend/app" "$INSTALL_DIR/app"
    cp -a "$SOURCE_DIR/backend/requirements.txt" "$INSTALL_DIR/requirements.txt"
    chown -R root:root "$INSTALL_DIR/app" "$INSTALL_DIR/requirements.txt"

    # The operator's copy of the runbook. Not required for the system to work, so a
    # missing README degrades to a note rather than failing an install on the morning
    # of the ceremony.
    if [ -f "$SOURCE_DIR/README.md" ]; then
        cp -a "$SOURCE_DIR/README.md" "$INSTALL_DIR/README.md"
        chown root:root "$INSTALL_DIR/README.md"
    else
        note "no README.md in the deploy source; skipping the documentation copy"
    fi

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        say "Creating the virtualenv (this is the slow step)"
        python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
    "$VENV_DIR/bin/python" -m pip install --quiet -r "$INSTALL_DIR/requirements.txt"
    "$VENV_DIR/bin/python" -c 'import fastapi, uvicorn, argon2, itsdangerous' \
        || die "the virtualenv is missing a runtime dependency"

    # Written only if absent, so a re-run does not change cookie or origin policy
    # underneath a running ceremony.
    if [ ! -f "$ENV_FILE" ]; then
        say "Writing $ENV_FILE"
        cat > "$ENV_FILE" <<EOF
# Read by systemd. See inauguration/README.md before editing.
NSS_BASE_DIR=$INSTALL_DIR
NSS_WEB_ROOT=$WEB_ROOT
NSS_BACKUP_DIR=$BACKUP_ROOT
NSS_LOG_DIR=$LOG_DIR
NSS_NGINX_MODE_INC=$NGINX_INC
NSS_NGINX_SITES_AVAILABLE=$NGINX_SITE
NSS_DECOMMISSION=$DECOMMISSION
NSS_NGINX_BINARY=$NGINX_BIN
NSS_SERVICE_NAME=$SERVICE
NSS_ALLOWED_ORIGINS=https://$SITE_HOST
NSS_SECURE_COOKIES=1
EOF
        chown "$SERVICE_USER:$SERVICE_GROUP" "$ENV_FILE"
        chmod 0640 "$ENV_FILE"
    else
        note "$ENV_FILE already exists; left unchanged"
    fi
}

# --- phase 5: the temporary web files ----------------------------------------

phase_5_web_files() {
    CURRENT_PHASE="web files"

    say "Publishing the hold page and the admin portal"
    install -o root -g root -m 0644 "$SOURCE_DIR/web/coming-soon.html" \
        "$WEB_ROOT/coming-soon.html"

    rm -rf "$WEB_ROOT/admin"
    cp -a "$SOURCE_DIR/web/admin" "$WEB_ROOT/admin"
    chown -R root:root "$WEB_ROOT/admin"
    chmod -R a+rX "$WEB_ROOT/admin"

    # The ceremony page is deployed like everything else, from the only copy that is
    # its source — $REPO_DIR/inauguration.html, which the preflight above already
    # requires to exist. It used to be left alone here, on the reasoning that a deploy
    # should never change what the ceremony looks like. The effect was the opposite:
    # the page could not be deployed at all, so the ceremony ran whatever the web root
    # happened to be holding and a fix to it went nowhere. Nothing is lost by writing
    # it now — phase 1 snapshots the whole web root before this phase runs, and the
    # note below names the file it left behind.
    install -o root -g root -m 0644 "$REPO_DIR/inauguration.html" \
        "$WEB_ROOT/inauguration.html"
    note "ceremony page $WEB_ROOT/inauguration.html"
    note "  the copy it replaced is in $BACKUP_DIR/site/inauguration.html"
}

# --- phase 6: the cleanup script ---------------------------------------------

phase_6_decommission() {
    CURRENT_PHASE="cleanup script"

    say "Installing the cleanup script"
    # Root-owned, 0750: readable and executable by root, and by nobody else. The
    # service reaches it only through the sudoers rule below.
    install -o root -g root -m 0750 "$SOURCE_DIR/deploy/sbin/nss-decommission" "$DECOMMISSION"
    bash -n "$DECOMMISSION" || die "$DECOMMISSION does not parse"
    note "$DECOMMISSION (root:root 0750)"
}

# --- phase 7: Nginx -----------------------------------------------------------

phase_7_nginx() {
    CURRENT_PHASE="nginx"

    say "Installing the server block"

    # The include has to exist before `nginx -t` runs, because the server block
    # includes it unconditionally: an include of a missing file is a fatal error, and
    # that is the behaviour we want — a missing include should stop the install
    # rather than serve something unintended.
    if [ ! -s "$NGINX_INC" ]; then
        ( cd "$INSTALL_DIR" && "$VENV_DIR/bin/python" -c \
            'from app.nginx_mode import render_include; print(render_include("coming_soon"), end="")' \
            > "$NGINX_INC" )
        chown "$SERVICE_USER:$SERVICE_GROUP" "$NGINX_INC"
        chmod 0644 "$NGINX_INC"
        note "wrote the initial include for coming_soon mode"
    fi

    # Test-then-swap, the same shape as NginxController.apply(): the candidate goes
    # in, `nginx -t` reads it, and the reload only happens if the test passed. A
    # failed test restores the previous file and aborts with nginx's own output.
    install -o root -g root -m 0644 "$SOURCE_DIR/deploy/nginx/nss" "$NGINX_SITE.new"

    local had_previous=0
    if [ -e "$NGINX_SITE" ]; then
        mv "$NGINX_SITE" "$NGINX_SITE.bak-$TIMESTAMP"
        had_previous=1
    fi
    mv "$NGINX_SITE.new" "$NGINX_SITE"

    rollback_site() {
        if [ "$had_previous" -eq 1 ]; then
            mv "$NGINX_SITE.bak-$TIMESTAMP" "$NGINX_SITE"
        else
            rm -f "$NGINX_SITE"
        fi
    }

    if ! test_output=$("$NGINX_BIN" -t 2>&1); then
        rollback_site
        printf '%s\n' "$test_output" >&2
        die "nginx rejected the new server block; the previous configuration is back in place"
    fi
    note "nginx -t accepted the new server block"

    # sites-enabled is a symlink on Debian. Create it only if nothing is there.
    if [ ! -e "$NGINX_ENABLED" ]; then
        ln -s "$NGINX_SITE" "$NGINX_ENABLED"
        note "enabled the site"
    fi

    if ! reload_output=$("$NGINX_BIN" -s reload 2>&1); then
        rollback_site
        if test_again=$("$NGINX_BIN" -t 2>&1); then
            "$NGINX_BIN" -s reload || true
        fi
        printf '%s\n' "$reload_output" >&2
        printf '%s\n' "$test_again" >&2
        die "nginx failed to reload; the previous configuration was restored and reloaded"
    fi
    note "nginx reloaded"
}

# --- phase 8: systemd ---------------------------------------------------------

phase_8_systemd() {
    CURRENT_PHASE="systemd"

    say "Installing the service unit"
    install -o root -g root -m 0644 "$SOURCE_DIR/deploy/systemd/nss-inauguration.service" "$UNIT"
    systemctl daemon-reload
    systemctl enable "$SERVICE" >/dev/null
    note "$UNIT (enabled)"
}

# --- phase 9: sudoers ---------------------------------------------------------

phase_9_sudoers() {
    CURRENT_PHASE="sudoers"

    say "Granting the one privileged command"
    # Validated in a temp file first: a broken sudoers file in /etc/sudoers.d takes
    # sudo away from every user on the machine, including whoever is trying to fix it.
    local candidate="$SUDOERS.candidate"
    install -o root -g root -m 0440 "$SOURCE_DIR/deploy/sudoers/nss-inauguration" "$candidate"

    if ! visudo -cf "$candidate" >/dev/null 2>&1; then
        visudo -cf "$candidate" >&2 || true
        rm -f "$candidate"
        die "the sudoers rule is invalid; nothing was installed"
    fi

    mv "$candidate" "$SUDOERS"
    chown root:root "$SUDOERS"
    chmod 0440 "$SUDOERS"
    note "$SUDOERS (validated with visudo -c)"

    # The grant is worthless if the script is not where the rule says it is, or if
    # the service account cannot reach it. Check the rule resolves before the service
    # that depends on it is ever started.
    if ! sudo -n -l -U "$SERVICE_USER" "$DECOMMISSION" >/dev/null 2>&1; then
        note "note: could not confirm the grant with \`sudo -l -U $SERVICE_USER\`"
        note "      verify by hand before the ceremony: sudo -l -U $SERVICE_USER"
    fi
}

# --- phase 10: start and verify ----------------------------------------------

phase_10_start() {
    CURRENT_PHASE="start"

    say "Starting $SERVICE"
    systemctl restart "$SERVICE"

    local tries=0
    until curl -fsS --max-time 2 "http://127.0.0.1:8001/api/health" >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -ge 15 ]; then
            systemctl status "$SERVICE" --no-pager >&2 || true
            die "$SERVICE did not answer on 127.0.0.1:8001 within 15s"
        fi
        sleep 1
    done

    note "backend is up"
    curl -fsS --max-time 5 "http://127.0.0.1:8001/api/health"
    printf '\n'
}

# --- main ---------------------------------------------------------------------

main() {
    [ "$#" -eq 0 ] || die "this script takes no arguments"

    printf '\n\033[1mNSS inauguration system — install\033[0m\n'
    printf '%s\n' "------------------------------------------------------------"

    phase_0_preconditions
    phase_1_backup
    phase_2_account
    phase_3_directories
    phase_4_application
    phase_5_web_files
    phase_6_decommission
    phase_7_nginx
    phase_8_systemd
    phase_9_sudoers
    phase_10_start

    cat <<EOF

$(printf '\033[1m')Installed.$(printf '\033[0m')

  Snapshot      $BACKUP_DIR
  Service       systemctl status $SERVICE
  Hold page     https://$SITE_HOST/
  Admin portal  https://$SITE_HOST/admin/

Two things are still to do, and neither is automatic on purpose:

  1. Create the admin account — it is never created by the installer, and there is
     no public registration route:

         sudo $SOURCE_DIR/deploy/init-admin.py

  2. Walk the rehearsal. deploy/smoke.sh runs the routing and lifecycle checks; the
     README's Pass B/Runbook is the end-to-end walk. Do it before the ceremony.

The site is in coming_soon mode. Nothing is public yet.
EOF
}

main "$@"
