#!/usr/bin/env python3
"""Local rehearsal — practise the ceremony without touching anything real.

Serves the site from the repository with the real API in front of it, so the ceremony
can be walked end to end: sign in at /admin/, open the ceremony, cut the ribbon, watch
the site move to its permanent routing. Everything it writes goes into
``.rehearsal/`` beside this repository.

Every run starts clean, discarding the previous one: the usual reason to run this is to
watch the ceremony from the beginning, and a site left in its inaugurated state shows
"this ceremony has already been held" on the very page the harness exists to show.
Pass ``--resume`` to keep the previous run's state instead.

Three things it deliberately cannot do:

* change production configuration — the Nginx controller is stubbed out and never
  shells out, so nothing outside ``.rehearsal/`` is read or written;
* mark the real inauguration complete — the state document it uses lives in
  ``.rehearsal/``, not in ``/opt``;
* execute the cleanup — the decommission step is refused outright, whatever the
  portal asks for.

This script is for local use and is never deployed to the VM. The installation
allowlist in ``usr/local/sbin/nss-decommission`` does not mention it, and neither does
``install.sh``.

    python inauguration/dev/rehearse.py
    python inauguration/dev/rehearse.py --port 9000
    python inauguration/dev/rehearse.py --resume     # keep the previous run's state
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "inauguration" / "backend"
WEB = REPO / "inauguration" / "web"

sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

REHEARSAL_USER = "rehearsal"
REHEARSAL_PASSWORD = "rehearsal"


class RehearsalNginx:
    """Stands in for the nginx binary.

    Reports every test and reload as successful and records what was asked for, so
    ``NginxController`` writes its include into ``.rehearsal/`` and reports success
    without a reload ever reaching the real server.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="rehearsal: ok", stderr="")


def refuse_cleanup(settings) -> tuple[bool, str]:
    """Rehearsal never decommissions anything."""
    return False, (
        "Rehearsal mode: the cleanup is disabled. Nothing outside .rehearsal/ was touched."
    )


def build_settings(root: Path, host: str, port: int) -> Settings:
    # The allowed origins have to name the port the rehearsal is actually served
    # on. Production listens on 443, where the browser's Origin is the bare
    # scheme and host; here it is http://127.0.0.1:8787, and a browser always
    # sends the port when it is not the default. Matching only the bare host
    # would pass every read and then refuse the cut — a 403 that looks like a
    # broken page rather than a misconfigured rehearsal.
    origins = []
    for name in (host, "127.0.0.1", "localhost"):
        for scheme in ("http", "https"):
            for suffix in ("", f":{port}"):
                origin = f"{scheme}://{name}{suffix}"
                if origin not in origins:
                    origins.append(origin)

    return Settings(
        base_dir=root / "opt" / "nss-inauguration",
        web_root=REPO,                      # the real site, read-only in practice
        backup_dir=root / "var" / "backups",
        log_dir=root / "var" / "log",
        nginx_mode_inc=root / "etc" / "nginx" / "nss-mode.inc",
        nginx_sites_available=root / "etc" / "nginx" / "sites-available" / "nss",
        decommission_script=root / "usr" / "local" / "sbin" / "nss-decommission",
        allowed_origins=tuple(origins),
        secure_cookies=False,               # exercised over plain http on purpose
    )


def build_app(root: Path, host: str, port: int):
    from fastapi.responses import FileResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    settings = build_settings(root, host, port)
    app = create_app(
        settings,
        nginx_run=RehearsalNginx(),
        secret=b"rehearsal-secret-not-used-in-production",
        decommission_runner=refuse_cleanup,
    )

    #: The paths the backend actually owns in production. Everything else is a
    #: static file that Nginx serves straight off disk.
    _BACKEND_OWNED = ("/api/", "/admin/", "/preview")

    @app.middleware("http")
    async def serve_static_like_nginx(request, call_next):
        """Stop the app claiming framing rights it will not have on the VM.

        In production the backend never serves the site: Nginx hands back
        ``index.html`` and the rest off disk and sets no framing header at all.
        Here the whole site goes through the app, so every page picks up the
        portal's ``X-Frame-Options: DENY`` and ``frame-ancestors 'none'`` — and
        the ceremony's reveal, which frames ``index.html`` inside its own page,
        is blocked by a header the VM will never send. That is the climax of the
        ceremony, so the rehearsal has to be able to show it.

        Only the framing rules are dropped. The rest of the policy stays: it is a
        real check on the shipped pages, and removing it would hide exactly the
        class of bug — an inline script silently blocked — that this harness
        exists to catch.
        """
        response = await call_next(request)
        if not request.url.path.startswith(_BACKEND_OWNED):
            if response.headers.get("x-frame-options"):
                del response.headers["x-frame-options"]
            policy = response.headers.get("content-security-policy")
            if policy and "frame-ancestors 'none'" in policy:
                response.headers["content-security-policy"] = policy.replace(
                    "frame-ancestors 'none'", "frame-ancestors 'self'"
                )
        return response

    store = app.state.ctx.admin_store
    if not store.exists():
        store.create(REHEARSAL_USER, REHEARSAL_PASSWORD)

    from app.state import read_state

    @app.get("/", include_in_schema=False)
    def index():
        """Mimics the production routing so the rehearsal shows the real behaviour."""
        mode = read_state(settings)["site_mode"]
        if mode == "permanent":
            return RedirectResponse("/index.html", status_code=302)
        page = "inauguration.html" if mode == "inauguration" else "coming-soon.html"
        return RedirectResponse("/" + page, status_code=302)

    # In production Nginx maps this bare path onto the backend's admin preview:
    #     location = /preview { proxy_pass http://127.0.0.1:8001/api/admin/preview; }
    # Registering the same handler here means the rehearsal exercises the real gate
    # rather than a second, weaker one — and the portal's "open preview" button works.
    from app.routes_admin import preview as admin_preview

    app.add_api_route("/preview", admin_preview, methods=["GET"], include_in_schema=False)

    # In production install.sh copies these two into the web root beside index.html.
    # Serving them from inauguration/web/ here keeps the rehearsal honest about where
    # they come from without writing anything into the repository.
    @app.get("/coming-soon.html", include_in_schema=False)
    def holding_page():
        return FileResponse(WEB / "coming-soon.html", media_type="text/html; charset=utf-8")

    app.mount("/admin", StaticFiles(directory=str(WEB / "admin"), html=True), name="admin")

    # The static site is mounted last so it cannot shadow the API routes above it.
    app.mount("/", StaticFiles(directory=str(REPO), html=True), name="site")
    return app, settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse the inauguration ceremony locally.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--resume", action="store_true",
        help="continue the previous rehearsal instead of starting clean",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="start from a clean coming-soon state (this is the default)",
    )
    args = parser.parse_args(argv)

    # Every run starts clean unless asked otherwise. A rehearsal is a rehearsal: the
    # common case is running it again to watch the ceremony from the beginning, and
    # resuming a site that was already inaugurated shows "this ceremony has already
    # been held" on the page you opened the harness to see. Opting in to continuity
    # is the rarer intent, so it is the one that costs a flag.
    root = REPO / ".rehearsal"
    if not args.resume:
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    app, settings = build_app(root, args.host, args.port)

    base = f"http://{args.host}:{args.port}"
    print()
    print("  NSS inauguration — local rehearsal")
    print("  " + "-" * 46)
    print(f"  Holding page   {base}/")
    print(f"  Admin portal   {base}/admin/")
    print(f"  Ceremony       {base}/inauguration.html")
    print(f"  Sign in as     {REHEARSAL_USER} / {REHEARSAL_PASSWORD}")
    print()
    if args.resume:
        print(f"  Resumed the previous rehearsal from {root}")
    else:
        print("  Started clean — the site is back at its coming-soon state.")
        print(f"  Use --resume to keep {root} from the last run instead.")
    print("  Nothing outside that directory is modified; the cleanup is disabled.")
    print()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
