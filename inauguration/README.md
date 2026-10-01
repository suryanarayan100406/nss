# NSS inauguration system

The launch system for **https://nss.iiitnr.ac.in**: a hold page with a countdown, the
ribbon-cutting ceremony, an admin portal, and a way to remove all of it afterwards.

**Read this before the ceremony.** The runbook is [Pass B](#pass-b--the-lifecycle) and
the three recovery procedures are at [Recovery](#recovery). Everything else is here so
that those two sections make sense.

---

## The one idea

The permanent website and the inauguration system never mix.

| | |
|---|---|
| **Permanent** | `index.html`, `team.html`, `event-*.html`, `css/`, `js/`, `vendor/`, `assets/`, `nss_logo.jpg` |
| **Temporary** | `inauguration/` (this tree), `inauguration.html`, `coming-soon.html`, `admin/`, and the backend behind them |

The permanent site is static files served by Nginx. It needs no application, no
database and no process to be running. The temporary layer is everything else, and
the whole design exists to make removing it a single, verified, reversible operation.

That is why `permanent` mode's routing is one line — `location / { try_files $uri $uri/ =404; }`
— with no reference to the backend at all. When the ceremony is over, the site is
exactly the site it is today, and the inauguration system being stopped, broken or
deleted changes nothing about it.

---

## Layout

```
/var/www/nss/                      the public web root
  index.html team.html event-*.html      PERMANENT
  css/ js/ vendor/ assets/ nss_logo.jpg  PERMANENT
  inauguration.html                      temporary · the ceremony
  coming-soon.html                       temporary · the hold page
  admin/                                 temporary · the admin portal

/opt/nss-inauguration/             application + configuration; never web-served
  app/                    the FastAPI application
  config/site.json        the state document
  config/admin.json       the admin account (argon2)
  config/secret.key       the session signing key (0600, generated on first boot)
  config/nss.env          paths, origins, cookie policy
  venv/

/etc/nginx/nss-mode.inc            generated routing, rewritten on every mode change
/etc/nginx/sites-available/nss     the server block that includes it
/usr/local/sbin/nss-decommission   root:root 0750, hardcoded allowlist
/etc/systemd/system/nss-inauguration.service
/etc/sudoers.d/nss-inauguration    one command, no arguments

/var/backups/nss-inauguration/     OUTSIDE the install dir, on no allowlist
/var/log/nss-inauguration/         audit.log · cleanup.log
```

**The backups and logs live outside `/opt/nss-inauguration/` on purpose.** The cleanup
ends by removing that whole directory, so anything stored inside it would be destroyed
at the exact moment it is most needed. A failed cleanup is precisely when the
pre-cleanup snapshot has to still exist.

### The service account

The backend runs as `nss-inaug` — a system account with no shell and no home, not in
`sudo`, `adm` or `www-data`. It can write exactly three things:

* `/opt/nss-inauguration/config/`
* `/var/backups/nss-inauguration/`
* `/etc/nginx/nss-mode.inc` — that one file, not `/etc/nginx`

It can read the web root and not write it. The service's **only** privilege escalation
is one command, and the sudoers rule restricts it to an empty argument vector, so the
script can never be invoked with arguments even if the backend one day tried to:

```
nss-inaug ALL=(root) NOPASSWD: /usr/local/sbin/nss-decommission ""
```

---

## The three modes

`site_mode` is derived and validated server-side; the client never sets it. Transitions
are `coming_soon → inauguration → permanent`, plus a terminal `decommissioned` flag.

| Mode | `/` serves | Permanent pages | `/admin/` | `/api/` |
|---|---|---|---|---|
| `coming_soon` | the hold page | redirected to the hold page | served, login enforced | proxied |
| `inauguration` | the ceremony | redirected to the ceremony | served, login enforced | proxied |
| `permanent` | `index.html` | served normally | **404** | **404** |

The routing lives in `/etc/nginx/nss-mode.inc` and is regenerated on every change. The
backend writes a candidate, runs `nginx -t`, and **only reloads if the test passed** —
and if the reload itself fails, it restores the previous include and reloads again. The
site is never left in a half-applied mode.

Two traps the generated include exists to avoid, both of which produce a **blank page**
rather than an error:

* a `location ~* \.html$` catch-all also matches the page it redirects to, so the
  target needs an exact-match block (`location = /coming-soon.html { }`) that wins over
  the regex — otherwise the hold page redirects to itself forever;
* `/nss_logo.jpg` and `/favicon.ico` are not under any `^~` prefix, so an unqualified
  `location /` would redirect them and strip the branding off the hold page.

`deploy/smoke.sh` checks both by following redirects and failing on
`curl: (47) Maximum (50) redirects followed`.

---

## Install

Do this **on the VM**, as root, from a clone of this repository. Nothing here runs on
the presentation laptop.

```bash
sudo inauguration/deploy/install.sh
```

No arguments. It is re-runnable: a second run re-deploys the code and leaves the state,
the admin account and the session key untouched.

What it does, in this order — the order is the point:

1. **Snapshots the live site first.** The whole web root plus the Nginx configuration
   goes to `/var/backups/nss-inauguration/deploy-<ts>/`, and the copy is verified
   against a `sha256sum` manifest before anything is written. If the verification
   fails, the install stops with the live site untouched.
2. Creates the `nss-inaug` account and the directories.
3. Copies the application in and builds the venv. `config/` is never overwritten, so a
   re-run cannot clobber a live ceremony.
4. Publishes `coming-soon.html`, `admin/` and the ceremony page. The ceremony page is
   copied from `inauguration.html` at the repository root on every run — that file is its
   only source, and the preflight refuses to install without it. The page it displaces is
   in the snapshot from step 1.
5. Installs the cleanup script at `0750`, root-owned.
6. Writes the server block to `sites-available/nss.new`, **runs `nginx -t`**, and only
   swaps it in and reloads if the test passed. A failed test restores the previous file
   and aborts with Nginx's own output.
7. Installs the systemd unit and enables it.
8. Validates the sudoers rule with `visudo -c` in a temporary file, then moves it into
   place. A typo aborts the install rather than silently widening the grant.
9. Starts the service and waits for `/api/health`.

**Then create the admin account.** It is never created by the installer, and there is
no public registration route:

```bash
sudo /opt/nss-inauguration/venv/bin/python /opt/nss-inauguration/deploy/init-admin.py
```

It prompts for a password twice with no echo (or reads `NSS_ADMIN_PASSWORD` for
non-interactive provisioning), requires at least 12 characters, and refuses to
overwrite an existing account without `--force`.

The site is now in `coming_soon` mode. Nothing is public yet.

### If the VM has no outbound network

`pip install` needs the wheels. Either give the VM network for the install, or build
the venv elsewhere and copy `/opt/nss-inauguration/venv` across — it is a plain
virtualenv and is relocatable if the prefix is identical.

---

## Rehearsal

### Locally, on any machine

```bash
python inauguration/dev/rehearse.py           # http://127.0.0.1:8787
python inauguration/dev/rehearse.py --port 9000
python inauguration/dev/rehearse.py --resume  # keep the previous run's state
```

Sign in with `rehearsal` / `rehearsal`. This serves the real site with the real backend
in front of it, so the ceremony can be walked end to end: sign in at `/admin/`, enable
the ceremony, open the preview, cut the ribbon, watch the site move to its permanent
routing.

**Every run starts clean** and discards the previous one. The usual reason to run this
is to watch the ceremony from the beginning, and a run left in its inaugurated state
answers the ceremony page with *"this ceremony has already been held"* — the very page
the harness exists to demonstrate, reading as a broken rehearsal rather than a stale
one. `--resume` is how you go back and inspect what a cut left behind. `--reset` still
works; it now names the default.

Three things it deliberately **cannot** do, and will refuse if asked:

* change production configuration — the Nginx controller is stubbed and never shells out;
* mark the real inauguration complete — its state document lives in `.rehearsal/`;
* execute the cleanup — refused outright, whatever the portal asks for.

That list is about what it refuses to *do*. There is also one thing it cannot *show*.
Here the whole site is served by the application rather than by Nginx, so the hold rules
do not exist: `/` is mimicked, but `index.html`, `team.html` and the event pages answer
200 locally when on the VM they are held back. Do not read a 200 for those here as a
routing bug. The routing is proved by the include tests in `tests/test_nginx_mode.py`
and by `deploy/smoke.sh` on the VM; the rehearsal is for walking the ceremony.

Everything it writes stays in `.rehearsal/` beside the repository. It is never deployed;
neither `install.sh` nor the cleanup allowlist mentions it.

### On the VM

```bash
deploy/smoke.sh routing      # Pass A — safe to run at any time
deploy/smoke.sh lifecycle    # Pass B — walks the whole ceremony
```

---

## Pass A — routing

Read-only. Run it after every install, every config change, and any time the site looks
wrong. It asserts, over real HTTPS:

* `/` and `/coming-soon.html` answer 200 with **no redirect loop**;
* `index.html`, `team.html` and every `event-*.html` return the **hold page body** —
  not merely a 200, which an accidentally-served permanent page would also give;
* `/nss_logo.jpg`, `/css/style.css`, `/js/main.js`, `/vendor/gsap.min.js` and a real
  file under `/assets/` all return 200 — the asset-stripping regression;
* `/favicon.ico` is **not** redirected and **not** a 5xx. It is not asserted as a 200,
  because there is no favicon in this repository and never has been; a 404 is correct;
* `/api/public/config` returns JSON;
* `/admin/` returns 200;
* the hold page body actually contains the hold page.

It reads the site mode first and asserts the set that matches it, because the same
paths mean different things either side of the ceremony. There are three sets, one per
mode:

* **`coming_soon`** — the list above.
* **`inauguration`** — between the enable step and the cut, `/` is the ceremony, the
  hold page leads to it, and the permanent pages are still held back but by the
  ceremony rather than the countdown. Without this pass, "safe to run at any time"
  would be untrue for the half hour before the event.
* **`permanent`** — after the cut, the mirror image: the held-back pages are now served
  as their own pages, the assets still answer, and `/admin/`, `/api/public/config` and
  `/inauguration.html` are all gone. That last group is what catches a cleanup which
  stopped half way.

So **run it again after the cut** — it switches over by itself.

The mode is read from the **status code** of `/api/public/config`, not its body: only a
404 means the system is gone. A backend that is merely unreachable is reported as its
own failure rather than being mistaken for a finished cleanup.

## Pass B — the lifecycle

Needs credentials, and moves the site. **Run it before the event.**

```bash
export NSS_ADMIN_USER=nss-admin
export NSS_ADMIN_PASSWORD='...'
deploy/smoke.sh lifecycle
```

It walks, asserting at every step:

1. **an unauthenticated cut returns 401/403 and leaves `site_mode` unchanged** — the
   authorization test, over real HTTP rather than only in pytest;
2. admin login succeeds;
3. **a signed-in cut without a CSRF token is refused with 403** — a session alone is
   not enough;
4. enabling the ceremony moves the site to `inauguration` mode and `/` serves the
   ceremony page;
5. **cutting commits it** — `site_mode` flips to `permanent`, `/` serves `index.html`,
   `/admin/` 404s.

The cleanup is **not** run unless you ask for it by name, because it is irreversible:

```bash
deploy/smoke.sh lifecycle --with-cleanup     # also needs NSS_CONFIRM_CLEANUP=YES
```

## Pass C — the dress rehearsal

Pass B, once more, on the real VM, with the real countdown date, a few days before the
event. There is no separate mode for it, because there is nothing extra to assert — the
point of Pass C is that it is the second time. This is the step that has to happen
*before* the ceremony.

### Recovery, mid-Pass-B

These are the checks that prove the recovery procedures actually work, so run them
during Pass B rather than discovering they do not during the event:

```bash
sudo systemctl restart nss-inauguration    # the mode must survive
sudo systemctl stop nss-inauguration       # the public site must still serve
sudo deploy/rollback.sh                    # from a broken include, restores a working site
```

---

## Operating the ceremony

1. Sign in at `https://nss.iiitnr.ac.in/admin/`.
2. **Enable the ceremony** — the site moves to `inauguration` mode and `/` becomes the
   ceremony page. The hold page starts redirecting here, so anyone with it bookmarked
   lands in the right place. Confirm it with `deploy/smoke.sh routing`, which switches to
   its inauguration assertions: `/` serves the ceremony, the hold page leads to it, and
   the permanent pages are still held back.
3. **Open the preview** (`/preview`) and walk the animation on the projector. Nothing
   is recorded and nothing changes.
4. On the day: the Director cuts the ribbon on the ceremony page, signed in as admin.
   The cut **is** the completion event — it records the timestamp and the name, and
   moves the site to its permanent routing in the same action.
5. Verify: `deploy/smoke.sh routing` again — it now reports the permanent pages serving,
   and that `/admin/`, `/api/public/config` and `/inauguration.html` are gone.

**Visitors see the same animation.** Every visitor can play it; what differs is the
label. Without a session, the ceremony page carries a persistent banner reading
*"Preview — this ceremony is not official"*, the reveal is relabelled *"...is still
held"*, and the "enter the website" link is hidden. On a confirmed `200` the banner is
replaced by *"Inauguration completed at \<time\>, by \<name\>"*, and the reveal frames
the real homepage. The real detail only ever appears after the server has committed it.

The cut button is **not** hidden from visitors. Hiding it would be theatre — the
animation is public either way — and a hidden control reads as a broken page to a
projector audience. Authorization is enforced server-side, and the label is honest
about it.

## The cleanup

Locked until the inauguration is complete. When you are ready — and not before —
sign in, scroll to the red decommissioning section, type `DELETE INAUGURATION SYSTEM`,
and re-enter the password. A live session alone is not enough to destroy the system.

The root-owned script then runs in six phases and **never advances past a phase that
failed**:

| Phase | What it does |
|---|---|
| 0 · preconditions | read-only: the ceremony completed, every permanent file present, the site serves on its own |
| 1 · backup | snapshot config + Nginx + the unit + the sudoers rule to `cleanup-<ts>/`, outside the install dir, checksummed |
| 2 · routing | render the permanent include → `nginx -t` → swap → `nginx -t` → reload → **verify over HTTP**. The backend is still running, so a failure here restores the previous include and leaves the system exactly as it was |
| 3 · web files | remove `inauguration.html`, `coming-soon.html`, `admin/` — then re-verify over HTTP |
| 4 · backend | stop and disable the service, remove `/opt/nss-inauguration/` — **last of all**, because it is the recovery mechanism |
| 5 · record | append to `cleanup.log`, print a report |
| 6 · self-removal | a detached helper waits for this process to exit, then removes the script, the sudoers rule and the unit |

Nothing is removed until the permanent configuration has been validated, reloaded and
verified over HTTP. The ordering is asserted by a pytest case that instruments a
sandboxed run and checks the backend directory is untouched until after the
post-reload HTTP verification has passed.

Re-running it afterwards is a clean no-op.

**Never touched, by any phase:** `index.html`, `team.html`, `event-*.html`, `css/`,
`js/`, `vendor/`, `assets/`, `nss_logo.jpg`, `sites-available/nss` (rewritten, never
deleted), everything under `/var/backups/nss-inauguration/`, and the audit and cleanup
logs. The allowlist is a literal list of paths in the script — no glob, no prefix
match, and no user-supplied path anywhere in it.

---

## Recovery

Three procedures. Each one has a copy-pasteable command and a written statement of what
state the machine is in afterwards.

### 1. The backend won't start

```bash
sudo systemctl status nss-inauguration
sudo journalctl -u nss-inauguration -n 100 --no-pager
```

**The public site still serves.** Nginx is static for every path except `/api/`,
`/admin/` and `/preview`, so a dead backend degrades to "the countdown is frozen", not
"the site is down". If the ceremony is imminent and the backend will not come up, the
site is still launchable: enable the ceremony another way is not possible, but the
permanent site is unaffected and Nginx can be pointed at it directly by putting
`permanent`'s one-line routing into `/etc/nginx/nss-mode.inc` and reloading.

### 2. A bad Nginx transition

The site is serving the wrong thing, or the hold page is looping.

```bash
sudo deploy/rollback.sh --list                 # what is available
sudo deploy/rollback.sh                        # restore the newest snapshot's configuration
sudo deploy/rollback.sh --backup cleanup-20261002-101500
```

It verifies the snapshot against its `sha256sum` manifest before restoring anything,
stashes the configuration it is replacing under `pre-rollback-<ts>/` so the rollback is
itself reversible, then runs `nginx -t` and reloads. If the snapshot's own routing is
rejected by Nginx, it puts the previous routing back — so you cannot end up worse off
than you started.

It restores configuration only. Pass `--web` to restore the files as well, which you
generally do not want: the site's content is not what breaks, and rewriting it during
an incident would undo whatever the ceremony had legitimately done.

### 3. The cleanup failed

The script aborts at the first phase that fails and changes nothing further, so
afterwards the system is in one of exactly two states:

* **nothing was removed** — the failure was in phase 0–1;
* **routing is permanent and the backend is still running** — the failure was in
  phase 2–4 after the include was swapped. The site is serving correctly; only the
  tidying up is incomplete.

Find out which:

```bash
sudo tail -50 /var/log/nss-inauguration/cleanup.log    # names the failed phase
ls -d /var/backups/nss-inauguration/cleanup-*/         # the restore point
ls -d /opt/nss-inauguration/                           # is the backend still there?
curl -sSI https://nss.iiitnr.ac.in/ | head -1          # is the site serving?
```

To restore the inauguration system:

```bash
sudo deploy/rollback.sh --backup cleanup-<ts>
```

To try the cleanup again — it re-checks every phase and is idempotent:

```bash
sudo /usr/local/sbin/nss-decommission
```

The snapshot and the logs survive either way. They are on no allowlist and are never
deleted by any phase.

---

## Tests

```bash
cd inauguration && python -m pytest tests/ -q
```

Tests run locally on Windows and need no VM. What they cover:

* **the application** — route guards (unauthenticated requests to every state-changing
  endpoint), login rate limiting and lockout, CSRF rejection, state transitions
  including atomic writes and concurrent updates, the cut requiring admin and being
  idempotent, and the cleanup refusing when not completed / wrong phrase /
  unauthenticated;
* **Nginx include rendering** — every mode rendered in Python and asserted
  structurally: the hold page's exact-match block precedes the `.html` catch-all, root
  assets and every `^~` prefix are present, and no `location /` precedes a `^~`;
* **the cleanup** — the allowlist equals the expected literal set, contains no permanent
  file and nothing under `/var/backups/` or `/var/log/`; a sandboxed run against a temp
  tree in which the permanent site survives and the temporary files do not; and a
  failure injected at each phase asserting that everything not yet removed survives and
  the recovery path is intact;
* **the deploy scripts** — `install.sh` and `rollback.sh` driven against a temp tree,
  asserting that a failed `nginx -t` leaves the live config byte-identical, that the
  snapshot is written before the first thing is replaced, and that a rollback restores
  a working config from the newest snapshot;
* **the ceremony page** — which page the reveal frames and when, and that only the
  committed path claims the site is live.

### What could not be tested from the development machine

This was written on Windows. The application, the state machine, the auth, the CSRF,
the allowlist logic, the Nginx include *text*, the deploy scripts and the pages are all
covered above. The systemd unit, the sudoers rule, `visudo`, and the real HTTPS
lifecycle **have not been executed** — their first true test is on the VM. Passes A–C
are yours to run there.

Two consequences worth knowing before you start:

* **The systemd unit deliberately omits `NoNewPrivileges=yes` and
  `SystemCallFilter=@system-service`**, which the original plan called for. Both break
  `sudo`: `NoNewPrivileges` sets `PR_SET_NO_NEW_PRIVS`, after which execve of the
  setuid-root `sudo` binary grants no privilege, and `@system-service` excludes the
  `setuid`/`setresuid` syscalls sudo needs. Either one would leave the system looking
  hardened and silently unable to run the cleanup — the one operation that has to work.
  The privilege boundary is the account, the file modes and `ProtectSystem=strict`,
  which are all present. The unit carries this reasoning inline so it does not get
  "fixed" back.
* **The venv needs outbound network on first install.** See above.

---

## Security

* argon2 password hashing; the password is never written to a log or passed on a
  command line.
* Sessions: signed `itsdangerous` cookie, `HttpOnly`, `Secure`, `SameSite=Lax`,
  30-minute idle expiry, server-side epoch for revocation.
* CSRF double-submit on every state-changing route. Origin checking on top: a browser
  always sends `Origin` on a cross-origin POST, so a mismatch is decisive.
* Per-IP login rate limiting with lockout. `X-Forwarded-For` is trusted only when the
  immediate peer is loopback — which is exactly how Nginx arrives, and means a direct
  caller cannot pick its own address and sidestep the lockout.
* `require_admin` enforced server-side on every admin and cleanup route. Hiding a
  control in JavaScript is presentation, never the control.
* CSP scoped by surface: the admin portal is strict (`script-src 'self'`, no inline),
  because it runs no inline script at all; the public site is relaxed for
  `script-src`, because every page in it is deliberately self-contained with inline
  script, and a strict policy there would silently disable the countdown, the ceremony
  and a third of the permanent site with no error anyone would understand.
* JSON config outside the web root at `0640`; `secret.key` at `0600`, generated on
  first boot. No secret is in the frontend or in this repository.
* No database, no CMS, no content editing, no member management, and **no public
  registration endpoint**. Anything beyond the inauguration lifecycle is out of scope.
