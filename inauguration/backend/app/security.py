"""Authorization, CSRF and origin checks.

Every control here is enforced on the server. The front end hides nothing and is
trusted with nothing: a request that does not carry a valid admin session and a
matching CSRF token is rejected no matter what the browser was told to display.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, Response, status

from .auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    AdminStore,
    NotAuthenticated,
    SessionManager,
)

STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class AdminSession:
    username: str
    csrf: str
    token: str


def _ctx(request: Request) -> Any:
    return request.app.state.ctx


def client_ip(request: Request) -> str:
    """The caller's address, trusting the proxy header only from the proxy itself.

    Anything that reaches the app directly must not be able to pick its own address
    and sidestep the login lockout, so X-Forwarded-For is honoured only when the
    immediate peer is loopback — which is exactly how Nginx proxies to us.
    """
    peer = request.client.host if request.client else ""
    if peer in {"127.0.0.1", "::1", "localhost"}:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def check_origin(request: Request) -> None:
    """Reject cross-site state-changing requests.

    A browser always sends Origin on a cross-origin POST, so a mismatch is decisive.
    A *missing* Origin is tolerated because same-origin form posts and non-browser
    clients omit it — and those cases cannot be forged by a third-party page anyway,
    which is the threat this guards.
    """
    if request.method not in STATE_CHANGING:
        return
    allowed = set(_ctx(request).settings.allowed_origins)
    origin = request.headers.get("origin")
    if origin and origin not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "origin not allowed")
    if not origin:
        referer = request.headers.get("referer")
        if referer and not any(referer.startswith(a) for a in allowed):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "referer not allowed")


def check_csrf(request: Request, session_payload: dict[str, Any]) -> None:
    """Double-submit: the header must match the signed session *and* the cookie.

    The signed copy is what actually stops an attacker — it is unreadable from a
    third-party page. The cookie comparison is the conventional second half of
    double-submit and catches a stale header after a re-login.
    """
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing {CSRF_HEADER} header")

    expected = session_payload.get("csrf", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")

    cookie = request.cookies.get(CSRF_COOKIE)
    if cookie is not None and not hmac.compare_digest(supplied, cookie):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token does not match cookie")


def load_session(request: Request) -> tuple[AdminSession, dict[str, Any]]:
    """Verify the session cookie, or raise 401. Does not check CSRF."""
    ctx = _ctx(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")

    store: AdminStore = ctx.admin_store
    record = store.load()
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no admin account configured")

    manager: SessionManager = ctx.sessions
    try:
        payload = manager.load(token, current_epoch=record.session_epoch)
    except NotAuthenticated as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    return AdminSession(username=payload["sub"], csrf=payload["csrf"], token=token), payload


def require_admin(request: Request) -> AdminSession:
    """FastAPI dependency for every authenticated route.

    Also slides the session window: because the cookie is re-issued on each call, the
    expiry behaves as an idle timeout rather than a hard deadline from login.
    """
    session, _ = load_session(request)
    refresh_session_cookie(request, session)
    return session


def require_admin_with_csrf(request: Request) -> AdminSession:
    """For state-changing admin routes: a session *and* a valid CSRF token."""
    session, payload = load_session(request)
    check_origin(request)
    check_csrf(request, payload)
    refresh_session_cookie(request, session)
    return session


def set_session_cookies(
    response: Response, settings: Any, manager: SessionManager,
    username: str, epoch: int, csrf: str,
) -> str:
    token = manager.issue(username, epoch, csrf)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    # Readable by the admin page's own script so it can echo it back in the header;
    # holding no authority on its own, it does not need to be HttpOnly.
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=settings.session_max_age,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return token


def clear_session_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


def refresh_session_cookie(request: Request, session: AdminSession) -> None:
    """Re-issue the cookie on an authenticated request, sliding the idle window."""
    ctx = _ctx(request)
    raw = getattr(request.state, "refresh_cookies", None)
    if raw is None:
        return
    raw.append((session.username, session.csrf))


_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

# The admin portal and the API. The portal is the one page that carries a session
# cookie and renders server-held data, so it is the surface worth being strict about —
# and it costs nothing here, because `admin/index.html` runs no inline script at all.
# Everything it executes is `admin.js`.
PORTAL_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-src 'none'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)

# The public site. Every page in it is deliberately self-contained: the original pages
# each carry inline <script> blocks, `team.html` has an inline onclick, and the ceremony
# page keeps its animation inline by design. A `script-src 'self'` here does not harden
# anything — it silently disables the countdown, the ceremony, and a third of the
# permanent site, with no error a visitor would understand. The pages take no input and
# render no user-supplied text, so the relaxation buys back a working site at no real
# cost. `frame-src` names Instagram because the homepage's post preview is a
# cross-origin iframe, which `default-src 'self'` would otherwise block outright.
SITE_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; connect-src 'self'; font-src 'self'; "
    "frame-src 'self' https://www.instagram.com; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)

#: Paths served by the application itself rather than by the public site.
STRICT_PREFIXES = ("/api/", "/admin/")


def content_security_policy(path: str) -> str:
    """The policy for one response, chosen by which surface is answering.

    Keeping the two policies apart is the whole point: it is what lets the portal stay
    strict without switching off the public pages, and it is why a single global policy
    would have been wrong either way round.
    """
    return PORTAL_CSP if path.startswith(STRICT_PREFIXES) else SITE_CSP


def security_headers_for(path: str) -> dict[str, str]:
    return {**_BASE_HEADERS, "Content-Security-Policy": content_security_policy(path)}
