"""`bookctl web` restarts its own server, and refuses to end anyone else's.

The failure this covers is mundane — "address already in use" from a server
the user forgot was running — but the fix is a process kill, so the tests here
spend most of their weight on the refusals rather than on the happy path.
"""

from __future__ import annotations

import shutil
import signal
import socket
from pathlib import Path

import pytest

from bookkit.web import portguard
from bookkit.web.portguard import Holder, PortHeld

# --- what counts as ours -----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "/Users/g/Developer/bookkit/.venv/bin/bookctl web",
        "/opt/bookkit/.venv/bin/bookctl web --port 8931",
        "/usr/bin/python3.13 -m bookkit.cli web",
        "/usr/bin/python3.13 -m bookkit web",
    ],
)
def test_a_bookkit_server_is_recognised(command: str):
    assert portguard.is_ours(command)


@pytest.mark.parametrize(
    "command",
    [
        # THE TRAP. A substring test for "bookkit" says yes to this, and the
        # thing bookctl would then kill is the test suite.
        "/Users/g/Developer/bookkit/.venv/bin/python -m pytest -q",
        "/Users/g/Developer/bookkit/node_modules/.bin/vite --port 8931",
        "/usr/local/bin/node /Users/g/bookkit-docs/server.js",
        "/usr/bin/python3 -m http.server 8931",
        "",
    ],
)
def test_anything_else_is_not_ours(command: str):
    assert not portguard.is_ours(command)


# --- the refusals ------------------------------------------------------------


def test_a_foreign_holder_is_refused_and_never_signalled(monkeypatch):
    """The whole point of the feature is the kill; the whole point of this test
    is that the kill does not happen here."""
    signalled: list[int] = []
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: False)
    monkeypatch.setattr(
        portguard,
        "holders",
        lambda host, port: [Holder(4711, "/usr/local/bin/node vite --port 8931")],
    )
    monkeypatch.setattr(portguard.os, "kill", lambda pid, sig: signalled.append(pid))

    with pytest.raises(PortHeld) as refusal:
        portguard.reclaim("127.0.0.1", 8931)

    assert signalled == []
    said = str(refusal.value)
    assert "4711" in said and "vite" in said, "the refusal must name what is holding the port"
    assert "--port 8932" in said, "and say what to do instead"


def test_an_unidentifiable_holder_is_refused(monkeypatch):
    """No lsof, no permission, no answer — an unidentified process is never a
    kill candidate. This degrades to uvicorn's own error, which is the floor."""
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: False)
    monkeypatch.setattr(portguard, "holders", lambda host, port: [])

    with pytest.raises(PortHeld, match="could not be identified"):
        portguard.reclaim("127.0.0.1", 8931)


def test_another_users_process_is_refused_not_retried(monkeypatch):
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: False)
    ours = [Holder(4711, "/opt/bookkit/.venv/bin/bookctl web")]
    monkeypatch.setattr(portguard, "holders", lambda host, port: ours)

    def denied(pid: int, sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(portguard.os, "kill", denied)

    with pytest.raises(PortHeld, match="another user"):
        portguard.reclaim("127.0.0.1", 8931, announce=lambda line: None)


def test_a_port_that_never_frees_is_refused(monkeypatch):
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: False)
    ours = [Holder(4711, "/opt/bookkit/.venv/bin/bookctl web")]
    monkeypatch.setattr(portguard, "holders", lambda host, port: ours)
    monkeypatch.setattr(portguard.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(portguard, "GRACE_SECONDS", 0.0)
    monkeypatch.setattr(portguard, "KILL_SECONDS", 0.0)

    with pytest.raises(PortHeld, match="still held"):
        portguard.reclaim("127.0.0.1", 8931, announce=lambda line: None)


# --- the restart -------------------------------------------------------------


def test_a_free_port_asks_nobody(monkeypatch):
    """The common case runs no subprocesses: startup must not pay for lsof."""

    def refuse(host: str, port: int):
        raise AssertionError("holders() must not be consulted for a free port")

    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: True)
    monkeypatch.setattr(portguard, "holders", refuse)

    portguard.reclaim("127.0.0.1", 8931)


def test_our_own_server_is_stopped_and_the_port_reclaimed(monkeypatch):
    sent: list[tuple[int, int]] = []
    said: list[str] = []
    frees = iter([False, True])
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: next(frees))
    ours = [Holder(4711, "/opt/bookkit/.venv/bin/bookctl web")]
    monkeypatch.setattr(portguard, "holders", lambda host, port: ours)
    monkeypatch.setattr(portguard.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    portguard.reclaim("127.0.0.1", 8931, announce=said.append)

    assert sent == [(4711, signal.SIGTERM)], "a live server is asked to stop, not killed outright"
    assert any("4711" in line for line in said), "a killed process is never killed silently"


def test_a_server_that_ignores_sigterm_is_killed(monkeypatch):
    sent: list[tuple[int, int]] = []
    frees = iter([False, False, True])
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: next(frees))
    ours = [Holder(4711, "/opt/bookkit/.venv/bin/bookctl web")]
    monkeypatch.setattr(portguard, "holders", lambda host, port: ours)
    monkeypatch.setattr(portguard.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(portguard, "GRACE_SECONDS", 0.0)

    portguard.reclaim("127.0.0.1", 8931, announce=lambda line: None)

    assert sent == [(4711, signal.SIGTERM), (4711, signal.SIGKILL)]


def test_a_process_that_exits_between_listing_and_signal_is_not_an_error(monkeypatch):
    frees = iter([False, True])
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: next(frees))
    ours = [Holder(4711, "/opt/bookkit/.venv/bin/bookctl web")]
    monkeypatch.setattr(portguard, "holders", lambda host, port: ours)

    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(portguard.os, "kill", gone)

    portguard.reclaim("127.0.0.1", 8931, announce=lambda line: None)


# --- the plumbing, unmocked --------------------------------------------------


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof is how a holder is identified")
def test_a_real_listener_is_found_and_refused():
    """Everything above mocks lsof and ps. This one holds a real socket in this
    process and asks the real questions — because a `holders()` that silently
    returns [] on every machine would pass every test above and reclaim
    nothing.

    It also proves the trap test end to end: this listener IS running from the
    bookkit checkout, and is refused anyway."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]

        assert not portguard.free_to_bind("127.0.0.1", port)

        found = portguard.holders("127.0.0.1", port)
        assert [h.pid for h in found] == [portguard.os.getpid()]
        assert found[0].command, "a holder without its command line cannot be judged"

        with pytest.raises(PortHeld, match="not ours to stop"):
            portguard.reclaim("127.0.0.1", port)


def test_free_to_bind_says_yes_to_a_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert portguard.free_to_bind("127.0.0.1", port)


# --- and what serve() does with a refusal ------------------------------------


def test_serve_refuses_rather_than_starting_a_server_it_cannot_bind(
    db_path: Path, monkeypatch, capsys
):
    import uvicorn

    from bookkit.web import serve as serve_mod

    started: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.append(kw))

    def refuse(host: str, port: int) -> None:
        raise PortHeld("held by pid 4711 (vite)")

    monkeypatch.setattr(portguard, "reclaim", refuse)

    assert serve_mod.serve(db_path, 8931, open_browser=False) == 1
    assert started == [], "a refused port must not reach uvicorn"
    assert "4711" in capsys.readouterr().out


def test_serve_reclaims_the_port_before_binding_it(db_path: Path, monkeypatch):
    """The order is the whole feature: asking uvicorn first is what produces
    the error this exists to remove."""
    import uvicorn

    order: list[str] = []
    from bookkit.web import serve as serve_mod

    monkeypatch.setattr(portguard, "reclaim", lambda host, port, **kw: order.append("reclaim"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: order.append("run"))

    serve_mod.serve(db_path, 8931, open_browser=False)

    assert order == ["reclaim", "run"]


def test_the_announcement_is_flushed(monkeypatch, capsys):
    """A killed process is a thing you want in the log afterwards, and stdout
    block-buffers when it is not a terminal: `bookctl web > log` swallowed the
    "stopping pid N" line entirely until this was fixed."""
    import builtins

    flushed: list[bool] = []
    real = builtins.print

    def watched(*args, **kwargs):
        flushed.append(bool(kwargs.get("flush")))
        real(*args, **kwargs)

    monkeypatch.setattr("builtins.print", watched)

    portguard.say("stopping pid 4711")

    assert flushed == [True]
    assert "4711" in capsys.readouterr().out


def test_reclaim_announces_through_say_by_default(monkeypatch):
    """The default must be the flushing one, not bare print."""
    frees = iter([False, True])
    said: list[str] = []
    monkeypatch.setattr(portguard, "free_to_bind", lambda host, port: next(frees))
    ours = [Holder(4711, "/opt/bookkit/.venv/bin/bookctl web")]
    monkeypatch.setattr(portguard, "holders", lambda host, port: ours)
    monkeypatch.setattr(portguard.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(portguard, "say", said.append)

    portguard.reclaim("127.0.0.1", 8931)

    assert any("4711" in line for line in said)
