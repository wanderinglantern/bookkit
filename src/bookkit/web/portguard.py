"""Who holds the port, and whether it is ours to end.

`bookctl web` used to die on uvicorn's bare "address already in use", and the
thing holding the port was almost always a server this same command started
and nobody noticed. Restarting is the right default.

Killing whatever answers on the port is NOT. 8931 is a port on Grant's
machine, not a port we own: a dev server, another app's admin panel, anything
at all may be sitting there, and ending someone else's process to take its
socket is the kind of help nobody asks for twice. So ownership is PROVEN
before anything is signalled, and a holder we cannot prove is ours is refused
with its own command line quoted back.

The proof is the process's argv, read from `ps`, matched by TOKEN and never as
a substring. A substring test looks fine and is a trap: pytest running out of
`~/Developer/bookkit` has "bookkit" in argv[0], and a green test suite would
have been the thing we killed.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

GRACE_SECONDS = 5.0
"""How long a SIGTERM'd server gets to put the socket down."""

KILL_SECONDS = 2.0
"""And how long SIGKILL gets after that."""

POLL_SECONDS = 0.1


class PortHeld(Exception):
    """The port is taken by something that is not ours to stop."""


@dataclass(frozen=True)
class Holder:
    """A process listening on the port, and the argv that proves what it is."""

    pid: int
    command: str


def free_to_bind(host: str, port: int) -> bool:
    """Can a server take host:port right now?

    Asked by binding it, because that is the same question uvicorn asks a
    moment later — anything else (a connect probe, a process list) answers a
    near-miss version of it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def is_ours(command: str) -> bool:
    """Is this argv a bookkit server?

    Only two shapes count: the installed `bookctl` launcher, and `-m bookkit`
    / `-m bookkit.<anything>`. Both name the program being RUN. A path that
    merely passes through the checkout proves nothing about what is running.
    """
    tokens = command.split()
    if not tokens:
        return False
    if os.path.basename(tokens[0]) == "bookctl":
        return True
    return any(
        flag == "-m" and (name == "bookkit" or name.startswith("bookkit."))
        for flag, name in zip(tokens, tokens[1:], strict=False)
    )


def holders(host: str, port: int) -> list[Holder]:
    """Every process listening on host:port, with its full command line.

    Returns [] when the machine cannot tell us — no `lsof`, no permission.
    That degrades to exactly today's behaviour (uvicorn's own error), which is
    the right floor: an unidentified holder is never a kill candidate.
    """
    return [Holder(pid, _command_of(pid)) for pid in _listening_pids(host, port)]


def reclaim(host: str, port: int, *, announce: Callable[[str], None] | None = None) -> None:
    """Return once host:port is free for this process to bind.

    Raises PortHeld — carrying the message a person needs to act on — when the
    holder is not ours, cannot be identified, or will not let go.
    """
    announce = say if announce is None else announce
    if free_to_bind(host, port):
        return

    held = holders(host, port)
    if not held:
        raise PortHeld(
            f"{host}:{port} is already in use, and the process holding it could not "
            f"be identified. Stop it yourself, or start on another port: "
            f"bookctl web --port {port + 1}"
        )

    foreign = [holder for holder in held if not is_ours(holder.command)]
    if foreign:
        listed = "\n".join(f"    pid {h.pid}  {h.command}" for h in foreign)
        raise PortHeld(
            f"refusing to start: {host}:{port} is held by a process that is not "
            f"bookkit's, so it is not ours to stop.\n{listed}\n"
            f"Stop it yourself, or start on another port: bookctl web --port {port + 1}"
        )

    for holder in held:
        announce(f"a bookkit server is already on {host}:{port} (pid {holder.pid}) — stopping it")
        _signal(holder.pid, signal.SIGTERM, host, port)
    if _becomes_free(host, port, GRACE_SECONDS):
        return

    for holder in held:
        announce(f"pid {holder.pid} did not stop in {GRACE_SECONDS:g}s — killing it")
        _signal(holder.pid, signal.SIGKILL, host, port)
    if _becomes_free(host, port, KILL_SECONDS):
        return

    raise PortHeld(
        f"{host}:{port} is still held after stopping "
        f"{', '.join(str(h.pid) for h in held)}. Start on another port: "
        f"bookctl web --port {port + 1}"
    )


def say(line: str) -> None:
    """Print, and FLUSH.

    stdout block-buffers when it is not a terminal, so `bookctl web >log 2>&1`
    held "stopping pid N" in a buffer that uvicorn then never flushed — the one
    record of a process being killed, lost in exactly the run where you go
    looking for it afterwards. Observed, not theorised.
    """
    print(line, flush=True)


def _becomes_free(host: str, port: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while True:
        if free_to_bind(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def _signal(pid: int, sig: int, host: str, port: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass  # it let go between the listing and the signal; that is the outcome we wanted
    except PermissionError as denied:
        raise PortHeld(
            f"refusing to start: {host}:{port} is held by pid {pid}, which belongs to "
            f"another user. Start on another port: bookctl web --port {port + 1}"
        ) from denied


def _listening_pids(host: str, port: int) -> list[int]:
    listed = _run(["lsof", "-nP", f"-iTCP@{host}:{port}", "-sTCP:LISTEN", "-t"])
    return [int(line) for line in listed.split() if line.isdigit()]


def _command_of(pid: int) -> str:
    return _run(["ps", "-o", "command=", "-p", str(pid)]).strip()


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout
