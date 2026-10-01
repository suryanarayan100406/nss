#!/usr/bin/env bash
#
# Put the site back the way a snapshot recorded it.
#
# This is the documented way back for the two failures that can leave the site
# serving the wrong thing: a bad Nginx mode transition, and a deploy that replaced a
# working server block with one that does not work. Both snapshots are written before
# the thing they protect is replaced, so there is always one to roll back to.
#
# It restores configuration by default and leaves the web root alone — the site's
# content is not what breaks, and rewriting it during an incident would undo whatever
# the ceremony had legitimately done. Pass --web to restore the files as well.
#
#     deploy/rollback.sh                      # newest snapshot, configuration only
#     deploy/rollback.sh --list
#     deploy/rollback.sh --backup cleanup-20261002-101500
#     deploy/rollback.sh --backup deploy-20261001T090000Z --web
#
# It never deletes a snapshot, never removes the installation, and is safe to re-run.

set -euo pipefail

readonly PROGRAM=nss-rollback

readonly INSTALL_DIR=/opt/nss-inauguration
readonly WEB_ROOT=/var/www/nss
readonly BACKUP_ROOT=/var/backups/nss-inauguration
readonly NGINX_INC=/etc/nginx/nss-mode.inc
readonly NGINX_SITE=/etc/nginx/sites-available/nss
readonly NGINX_BIN=/usr/sbin/nginx
readonly UNIT=/etc/systemd/system/nss-inauguration.service
readonly SUDOERS=/etc/sudoers.d/nss-inauguration
readonly SERVICE=nss-inauguration

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*" >&2; }
die()  { printf '\n\033[31m%s: %s\033[0m\n' "$PROGRAM" "$*" >&2; exit 1; }

BUILD="$(date -u +%Y%m%dT%H%M%SZ)"
#: The snapshot named by --backup. Empty means "the newest one", which is the
#: ordinary case. Kept separate from BUILD, which only ever names the stash written
#: by this run — conflating the two makes the no-argument form look for a snapshot
#: named after the current second and fail.
REQUESTED=""
STASH=""
WAS_RUNNING=0

require_root() {
    [ "$(id -u)" -eq 0 ] || die "must run as root (try: sudo $0)"
}

list_snapshots() {
    say "Snapshots in $BACKUP_ROOT"
    [ -d "$BACKUP_ROOT" ] || { note "(none — nothing has been backed up yet)"; return 0; }
    local found=0 dir
    for dir in "$BACKUP_ROOT"/*/; do
        [ -d "$dir" ] || continue
        found=1
        printf '    %-32s %s\n' "$(basename "$dir")" \
            "$(stat -c '%y' "$dir" 2>/dev/null | cut -d. -f1)"
    done
    [ "$found" -eq 1 ] || note "(none)"
}

# --- snapshot access ----------------------------------------------------------

snapshot_dir() {
    if [ -n "$REQUESTED" ]; then
        [ -d "$BACKUP_ROOT/$REQUESTED" ] || die "no snapshot named $REQUESTED in $BACKUP_ROOT"
        printf '%s' "$BACKUP_ROOT/$REQUESTED"
        return
    fi
    # Newest by modification time. `ls -1dt` on directories only, so a stray file in
    # the backup root cannot be selected.
    local newest
    newest=$(ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null | head -1 || true)
    [ -n "$newest" ] || die "there are no snapshots in $BACKUP_ROOT to roll back to"
    printf '%s' "${newest%/}"
}

verify_snapshot() {
    local dir=$1
    [ -f "$dir/MANIFEST.sha256" ] \
        || die "$dir has no MANIFEST.sha256; refusing to restore from it"
    ( cd "$dir" && sha256sum --check --quiet MANIFEST.sha256 ) \
        || die "$dir does not match its own checksums; refusing to restore from it"
    note "snapshot verifies against its manifest"
}

# Locate one file in either snapshot layout. install.sh writes nginx/ with the
# original names; nss-decommission writes them flat with prefixed names.
find_in_snapshot() {
    local dir=$1 name=$2 candidate
    for candidate in "$dir/nginx/$name" "$dir/$name"; do
        [ -e "$candidate" ] && { printf '%s' "$candidate"; return 0; }
    done
    return 1
}

# --- restore steps ------------------------------------------------------------

backup_current() {
    # Before anything is overwritten, the *current* files are stashed too. If the
    # rollback turns out to be the wrong move, the state it replaced is still on disk.
    local stash="$BACKUP_ROOT/pre-rollback-$BUILD"
    mkdir -p "$stash/nginx"
    [ -e "$NGINX_INC" ] && cp -a "$NGINX_INC" "$stash/nginx/nss-mode.inc"
    [ -e "$NGINX_SITE" ] && cp -a "$NGINX_SITE" "$stash/nginx/nss"
    [ -e "$UNIT" ] && cp -a "$UNIT" "$stash/nginx/nss-inauguration.service"
    [ -e "$SUDOERS" ] && cp -a "$SUDOERS" "$stash/nginx/nss-inauguration.sudoers"
    STASH="$stash"
    note "current configuration stashed at $stash"
}

restore_nginx() {
    local dir=$1 src

    if src=$(find_in_snapshot "$dir" nss-mode.inc); then
        cp -a "$src" "$NGINX_INC"
        note "restored $NGINX_INC"
    else
        warn "the snapshot has no nss-mode.inc; left the current routing in place"
    fi

    if src=$(find_in_snapshot "$dir" nss); then
        cp -a "$src" "$NGINX_SITE.new"
        mv "$NGINX_SITE.new" "$NGINX_SITE"
        note "restored $NGINX_SITE"
    fi

    if src=$(find_in_snapshot "$dir" nss-inauguration.service); then
        cp -a "$src" "$UNIT"
        note "restored $UNIT"
    fi

    if src=$(find_in_snapshot "$dir" nss-inauguration.sudoers); then
        if visudo -cf "$src" >/dev/null 2>&1; then
            cp -a "$src" "$SUDOERS"
            chown root:root "$SUDOERS"
            chmod 0440 "$SUDOERS"
            note "restored $SUDOERS"
        else
            warn "the snapshot's sudoers rule does not validate; left the current one in place"
        fi
    fi

    # The include is written by the service account and Nginx reads it as root; make
    # sure a restore has not left it unreadable or root-only.
    if [ -f "$NGINX_INC" ]; then
        chown "nss-inaug:nss-inaug" "$NGINX_INC" 2>/dev/null || true
        chmod 0644 "$NGINX_INC"
    fi
}

restore_web() {
    local dir=$1 src

    if [ -d "$dir/site" ]; then
        say "Restoring the web root from $dir/site"
        # Contents only; the directory itself, and anything the snapshot did not
        # contain, stay where they are.
        cp -a "$dir/site/." "$WEB_ROOT/"
        note "web root restored"
    elif [ -d "$dir/web" ]; then
        say "Restoring the temporary web files from $dir/web"
        cp -a "$dir/web/." "$WEB_ROOT/"
        note "ceremony and hold page restored"
    else
        warn "the snapshot contains no web files; nothing to restore there"
    fi
}

reload_nginx() {
    say "Testing and reloading Nginx"
    local output
    if ! output=$("$NGINX_BIN" -t 2>&1); then
        printf '%s\n' "$output" >&2
        if [ -n "$STASH" ] && [ -f "$STASH/nginx/nss-mode.inc" ]; then
            warn "the snapshot's routing was rejected; putting the stashed routing back"
            cp -a "$STASH/nginx/nss-mode.inc" "$NGINX_INC"
            chown "nss-inaug:nss-inaug" "$NGINX_INC" 2>/dev/null || true
            if "$NGINX_BIN" -t >/dev/null 2>&1; then
                "$NGINX_BIN" -s reload || true
                warn "the machine is back on the configuration it had before this rollback"
            fi
        fi
        die "nginx rejected the restored configuration; see the output above"
    fi
    note "nginx -t accepted the restored configuration"

    "$NGINX_BIN" -s reload || die "nginx -t passed but the reload failed; check journalctl -u nginx"
    note "nginx reloaded"
}

restart_service() {
    systemctl daemon-reload

    if [ ! -f "$UNIT" ] || [ ! -d "$INSTALL_DIR" ]; then
        if [ "$WAS_RUNNING" -eq 1 ]; then
            warn "the service was running but its unit or installation is gone; leaving it stopped"
        else
            note "no inauguration service to start (this snapshot is from after a cleanup)"
        fi
        return
    fi

    say "Restarting $SERVICE"
    systemctl restart "$SERVICE" || die "$SERVICE failed to start; journalctl -u $SERVICE"
    local tries=0
    until curl -fsS --max-time 2 "http://127.0.0.1:8001/api/health" >/dev/null 2>&1; do
        tries=$((tries + 1))
        [ "$tries" -ge 15 ] && die "$SERVICE did not answer within 15s after the rollback"
        sleep 1
    done
    note "backend is up"
}

# --- main ---------------------------------------------------------------------

main() {
    local want_web=0
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --list|-l)   require_root; list_snapshots; return 0 ;;
            --backup)    [ "$#" -ge 2 ] || die "--backup needs a snapshot name"; REQUESTED="$2"; shift 2 ;;
            --web)       want_web=1; shift ;;
            -h|--help)   sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
            *)           die "unknown argument: $1 (try --help)" ;;
        esac
    done

    require_root

    local dir
    dir=$(snapshot_dir)
    say "Rolling back from $dir"
    verify_snapshot "$dir"

    if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
        WAS_RUNNING=1
    fi

    backup_current
    restore_nginx "$dir"
    [ "$want_web" -eq 1 ] && restore_web "$dir"
    reload_nginx
    restart_service

    cat <<EOF

$(printf '\033[1m')Rolled back.$(printf '\033[0m')

  Snapshot used       $dir
  What it replaced    $STASH

Check the site before walking away:

    curl -sSI https://nss.iiitnr.ac.in/ | head -1
    $0 --list
EOF
}

main "$@"
