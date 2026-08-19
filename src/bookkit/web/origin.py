"""Who is allowed to talk to this server, as opposed to where it listens.

serve.py binds 127.0.0.1 and there is a test pinning it. That is a NETWORK
control, and it was doing an ORIGIN control's job: the browser is already on
the loopback, so a page on any website could drive this app through it. A
reviewer did exactly that — a cross-origin POST created a contact and then
reverted a real edit, and `services.batches.revert` writes its restoration
WITHOUT a batch_id, so that one is not undoable by `R` or `u`. A forged
`Host: evil.example` served /book in full.

TWO CHEAP CHECKS, NOT A CSRF TOKEN FLOW. What was rejected and why:

* Per-session CSRF tokens. There is no session, no cookie and no signing key
  anywhere in this app, so this means inventing all three, then threading a
  hidden field through macros/form.html AND every htmx POST that is not a
  form (the cell editors, `done`, `received`, `revert`). Tokens earn their
  cost where Origin cannot be trusted — browsers too old to send it, or a
  multi-user service where the origin is shared and the SESSION is the thing
  being proven. Neither holds here: this is one person, one machine, and
  every browser in use has sent Origin on non-GET for years.
* SameSite cookies — there are no cookies to mark.
* Requiring a custom header (X-Requested-With) on every request. Identical
  protection to the Origin check, since only CORS-preflighted requests can
  set it cross-origin, but it needs a change in every template and breaks
  `curl` against a local tool for no gain.

HOST, ON EVERY REQUEST. A DNS-rebinding page resolves its own name to
127.0.0.1 and then IS same-origin — an Origin check alone would let it read
/book, because that is a GET and no request header is out of place. What is
out of place is the name: this server answers to loopback names only, so a
request arriving under any other one is refused before it reaches a route.
This is the check that makes the loopback binding mean what it says.

ORIGIN, ON WRITES. The classic same-origin test and it needs no
configuration: a browser's `Origin` (or, if absent, the origin part of
`Referer`) must equal the `Host` it is talking to, which is what "same
origin" means and is also why the port is included — another local app on
another port is a different origin and has no business writing here.

NEITHER HEADER PRESENT IS ALLOWED, deliberately. Browsers send Origin on
every non-GET; a request with neither header is a script or a `curl`, which
is a person at this machine's own keyboard, i.e. exactly the party the
binding already trusts. Refusing those would break local scripting to
protect against nothing a browser can do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from .serve import HOST

# `serve.HOST` is one of these BY TEST (tests/test_web_shell.py), so the
# network control and the origin control cannot drift apart: whatever the
# server binds, this is the set of names it answers to.
LOOPBACK_HOSTS = frozenset({HOST, "127.0.0.1", "localhost", "::1", "[::1]"})

# GET/HEAD/OPTIONS are left to the Host check alone: they write nothing, and a
# cross-origin page cannot READ the response without CORS headers, which this
# app never sends.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[Any]]
Send = Callable[[Any], Awaitable[None]]


def bare_host(authority: str) -> str:
    """`host:port` -> `host`, with an IPv6 literal's brackets kept.

    Splitting on ":" is only safe once the IPv6 forms are out of the way. RFC
    7230 requires the brackets in a Host header, but a bare "::1" reaches this
    from a hand-written client and splitting it on the first colon leaves the
    empty string — a name that matches nothing, which is a refusal for the
    wrong reason."""
    value = authority.strip().lower()
    if value.startswith("["):
        closed = value.find("]")
        return value if closed == -1 else value[: closed + 1]
    if value.count(":") > 1:  # bare IPv6 literal: every colon is part of it
        return value
    return value.split(":", 1)[0]


def is_loopback(authority: str) -> bool:
    host = bare_host(authority)
    return host in LOOPBACK_HOSTS or host.strip("[]") in LOOPBACK_HOSTS


def _header(scope: Scope, name: bytes) -> str:
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for key, value in headers:
        if key == name:
            return value.decode("latin-1")
    return ""


def refusal(scope: Scope) -> tuple[int, str] | None:
    """(status, reason) when this request must not reach a route, else None.

    Both refusals are terse on purpose: the party on the other end is either
    the operator, who has the server's own log and knows what it binds, or a
    page that should learn nothing about what is here."""
    host = _header(scope, b"host")
    if not host or not is_loopback(host):
        # 400, not 403: the request is malformed for this server — it names a
        # host this server does not serve — rather than merely unauthorised.
        return 400, f"this server answers on loopback only, not {host or '(no host)'!r}"
    if scope.get("method", "").upper() not in UNSAFE_METHODS:
        return None
    origin = _header(scope, b"origin")
    if not origin:
        referer = _header(scope, b"referer")
        if not referer:
            return None  # not a browser; see this module's docstring
        origin = referer
    # "null" is what a sandboxed iframe or a file:// page sends, and urlsplit
    # gives it an empty netloc — which would otherwise compare equal to
    # nothing rather than being refused outright.
    netloc = urlsplit(origin).netloc.lower()
    if not netloc or netloc != host.strip().lower():
        return 403, "a write must come from this page, not from another site"
    return None


class OriginGuard:
    """Plain ASGI, not BaseHTTPMiddleware.

    BaseHTTPMiddleware runs the downstream app inside a task group of its own,
    and this app's connection model is written around which task and which
    THREAD a route runs on (see app.ThreadConnections). A short-circuiting
    wrapper that never calls into the app on the refusal path introduces
    neither."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        refused = refusal(scope)
        if refused is None:
            await self.app(scope, receive, send)
            return
        status, reason = refused
        body = reason.encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
