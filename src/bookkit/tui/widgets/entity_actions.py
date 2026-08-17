"""Entity action FLOWS shared by AccountScreen and NavigatorScreen — the
placement dual-owner edit, renew confirm, and layer edit. One implementation,
two callers; forms commit in place (the platform default). `screen` is any
Screen: it supplies .app (conn, push_screen), .notify, and refresh_data()."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from textual.screen import Screen

    from ...models import Placement, RfiItem, RfiRequest
    from ..app import BookkitApp


def _app(screen: Screen) -> BookkitApp:
    return cast("BookkitApp", screen.app)


def _refresh(screen: Screen) -> None:
    refresh = getattr(screen, "refresh_data", None)
    if refresh is not None:
        refresh()


@contextmanager
def batched_write(
    screen: Screen, *, tool: str, summary: str, org_id: str | None = None
) -> Iterator[None]:
    """One direct (non-form) TUI write as one undo unit.

    Forms get this automatically from FormModal; the keystroke actions —
    d done, D drop, delete interaction, inline cell edits, appetite delete —
    call repos directly and would otherwise write outside any batch, which
    means `u` cannot reach them at all now that undo is batch-granular.

        with batched_write(self, tool="task_done", summary=f"completed {title}"):
            tasks_repo.complete(conn, key)
    """
    from ...services import batches as batches_svc

    with batches_svc.open_batch(
        _app(screen).conn, source="tui", tool=tool, summary=summary, org_id=org_id
    ):
        yield


def push_form(
    screen: Screen, spec: Any, on_save: Any, batch: Any = None, org_id: str | None = None
) -> None:
    """The commit/done wrapper: on_save runs while the form is open; any
    raise keeps the form up with the input intact.

    Every save is ONE undo unit. `batch` defaults to one derived from the
    form's own title, so a caller gets atomicity and a `R`-revertible entry
    without opting in — a save that writes four rows can no longer be half
    applied, and no longer needs `u` to guess which of the four to put back.
    Pass an explicit BatchSpec when the changes list deserves a better
    sentence than the form title."""
    from ...forms.spec import BatchSpec
    from .forms import FormModal

    if batch is None:
        batch = BatchSpec.for_title(spec.title, org_id=org_id)

    def commit(values: dict) -> str | None:
        on_save(values)
        return None

    def done(values: dict | None) -> None:
        if values is not None:
            _refresh(screen)

    _app(screen).push_screen(FormModal(spec, commit=commit, batch=batch), done)



def edit_placement(screen: Screen, placement: Placement) -> None:
    """Dual-owner edit for linked placements (name/dates write through the
    towerkit file), plain form for unlinked ones."""
    from ... import sync
    from ...forms import entities as ef
    from ...forms.spec import Field, FormSpec
    from ...repo import placements
    from .forms import FormModal

    conn = _app(screen).conn
    if not placement.program_path:
        push_form(
            screen,
            ef.placement_form(placement, conn=conn),
            lambda v: ef.apply_placement(conn, v, placement.org_id, placement),
        )
        return

    spec = FormSpec(
        f"edit {placement.ref} (dates/name write to the towerkit file)",
        [
            Field("program_name", "program name", required=True),
            Field("period_from", "effective", "date", required=True),
            Field("period_to", "expiry", "date", required=True),
            Field(
                "status", "status", "select",
                tuple((s, s) for s in
                      ("prospective", "submitted", "quoted", "bound", "lapsed")),
            ),
            Field("commission_bps", "commission (bps)", "int"),
        ],
        initial={
            "program_name": placement.program_name,
            "period_from": placement.period_from,
            "period_to": placement.period_to,
            "status": placement.status,
            "commission_bps": placement.commission_bps,
        },
    )

    def commit(values: dict) -> str | None:
        file_changes = {
            key: values[key]
            for key in ("program_name", "period_from", "period_to")
            if values.get(key) is not None
            and values[key] != getattr(placement, key)
        }
        if file_changes:
            diags = sync.update_program(conn, placement.id, **file_changes)
            if not diags.ok:
                return f"refused: {diags.errors[0]}"
        book_changes = {
            key: values[key]
            for key in ("status", "commission_bps")
            if values.get(key) is not None and values[key] != getattr(placement, key)
        }
        if book_changes:
            placements.update(conn, placement.id, **book_changes)
        return None

    def done(values: dict | None) -> None:
        if values is None:
            return
        screen.notify(f"updated {placement.ref}")
        _refresh(screen)

    _app(screen).push_screen(FormModal(spec, commit=commit), done)


def renew_placement(screen: Screen, placement: Placement) -> None:
    """ConfirmRenew → sync.renew: next period, file cloned + linked at birth."""
    from ..screens.account import ConfirmRenew

    def confirmed(answer: bool | None) -> None:
        if not answer:
            return
        from ... import sync

        new_placement, new_path, diags = sync.renew(_app(screen).conn, placement.id)
        if new_placement is None:
            screen.notify(f"renew refused: {diags.errors[0]}", severity="error")
            return
        note = f" + {new_path.name}" if new_path else ""
        screen.notify(f"created {new_placement.ref} ({new_placement.period_to}){note}")
        _refresh(screen)

    _app(screen).push_screen(ConfirmRenew(placement), confirmed)


def edit_layer(screen: Screen, placement: Placement, layer_id: str | None = None) -> None:
    """Edit one layer of a linked placement through write-through. With no
    layer_id: straight to the form when there's one layer, else the picker."""
    from ... import sync
    from ...forms.spec import Field, FormSpec
    from ...money import format_cents_compact
    from .forms import FormModal
    from .picker import Picker

    conn = _app(screen).conn
    layers = sync.layer_details(conn, placement.id)
    if not layers:
        screen.notify("no layers yet — L adds one", severity="warning")
        return

    def picked(chosen: str | None) -> None:
        if chosen is None:
            return
        layer = next(ly for ly in layers if str(ly["id"]) == chosen)
        spec = FormSpec(
            f"edit layer — {layer['name']}",
            [
                Field("name", "name", required=True),
                Field("policy_number", "policy number"),
                Field("attach", "attach", "money", required=True),
                Field("limit", "limit", "money", required=True),
                Field("premium", "premium", "money"),
                Field("period_from", "policy effective", "date"),
                Field("period_to", "policy expiry", "date"),
            ],
            initial={
                "name": layer["name"],
                "policy_number": layer["policy_number"],
                "attach": layer["attach_cents"],
                "limit": layer["limit_cents"],
                "premium": layer["premium_cents"],
                "period_from": layer["period_from"],
                "period_to": layer["period_to"],
            },
        )

        def commit(values: dict) -> str | None:
            diags = sync.update_layer(
                conn,
                placement.id,
                chosen,
                name=values.get("name"),
                policy_number=values.get("policy_number"),
                attach_cents=values.get("attach"),
                limit_cents=values.get("limit"),
                premium_cents=values.get("premium"),
                period_from=values.get("period_from"),
                period_to=values.get("period_to"),
            )
            return f"refused: {diags.errors[0]}" if not diags.ok else None

        def done(values: dict | None) -> None:
            if values is None:
                return
            screen.notify(f"updated {layer['name']}")
            _refresh(screen)

        _app(screen).push_screen(FormModal(spec, commit=commit), done)

    if layer_id is not None and any(str(ly["id"]) == layer_id for ly in layers):
        picked(layer_id)
        return
    if len(layers) == 1:
        picked(str(layers[0]["id"]))
        return
    options = [
        (
            f"{ly['name']}  {format_cents_compact(ly['limit_cents'])} xs "
            f"{format_cents_compact(ly['attach_cents'])}  ({ly['signed_pct']:g}% placed)",
            str(ly["id"]),
        )
        for ly in layers
    ]
    _app(screen).push_screen(Picker("edit which layer?", options), picked)


def export_open_items_flow(screen: Screen, org_id: str) -> None:
    """The open-items workbook for one client: resolve the org, write the
    file to the CWD, notify. Shared by NavigatorScreen's `x` row export and
    the client Open Items tab — export mutates nothing, so no _refresh."""
    from datetime import date
    from pathlib import Path

    from ...repo import orgs
    from ...services import export_open_items

    conn = _app(screen).conn
    try:
        org = orgs.get(conn, org_id)
    except KeyError:
        screen.notify("account no longer exists", severity="error")
        return
    today = date.today()
    out = Path(f"{org.ref}-open-items-{today.isoformat()}.xlsx")
    try:
        path = export_open_items.write(conn, org_id, out, today)
    except ImportError as exc:
        # the workbook renderer lives in towerkit; an older towerkit on
        # this machine must not take the whole app down
        screen.notify(
            f"export needs a newer towerkit — update it ({exc})",
            severity="error",
        )
        return
    except OSError as exc:
        screen.notify(f"export failed: {exc}", severity="error")
        return
    screen.notify(f"wrote {path}")


def add_request(screen: Screen, org_id: str) -> None:
    from ...forms import entities as ef

    conn = _app(screen).conn
    push_form(
        screen,
        ef.request_form(conn=conn, org_id=org_id),
        lambda v: screen.notify(f"created {ef.apply_request(conn, v, org_id).ref}"),
    )


def edit_request(screen: Screen, request: RfiRequest) -> None:
    from ...forms import entities as ef

    conn = _app(screen).conn
    push_form(
        screen,
        ef.request_form(request, conn=conn, org_id=request.org_id),
        lambda v: ef.apply_request(conn, v, request.org_id, existing=request),
    )


def add_rfi_item(screen: Screen, request_id: str) -> None:
    from ...forms import entities as ef

    conn = _app(screen).conn
    push_form(
        screen,
        ef.rfi_item_form(conn=conn),
        lambda v: ef.apply_rfi_item(conn, v, request_id),
    )


def edit_rfi_item(screen: Screen, item: RfiItem) -> None:
    from ...forms import entities as ef

    conn = _app(screen).conn
    push_form(
        screen,
        ef.rfi_item_form(item, conn=conn),
        lambda v: ef.apply_rfi_item(conn, v, item.request_id, existing=item),
    )


def paste_rfi_items(screen: Screen, request_id: str) -> None:
    """One pasted block → one item per line. Refuses an empty paste in place
    (commit-in-place: the form stays up with the text intact)."""
    from ... import db
    from ...forms.spec import Field, FormSpec
    from ...imports.rfi_paste import split_items
    from ...repo import rfi as rfi_repo

    conn = _app(screen).conn

    def commit(values: dict) -> None:
        # push_form's contract is raise-to-refuse (see its docstring): its
        # inner commit wrapper calls on_save(values) and discards whatever
        # it returns, so a returned string here would vanish silently and
        # the form would close on an empty paste instead of staying open.
        prompts = split_items(values.get("pasted") or "")
        if not prompts:
            raise ValueError("nothing to add — paste one item per line")
        # the connection is AUTOCOMMIT: without a real transaction a failure
        # partway would leave a half-pasted batch committed
        with db.transaction(conn):
            for prompt in prompts:
                rfi_repo.add_item(conn, request_id, prompt)
        # NB: no "u undoes" here. base.insert logs creates as field='created'
        # and events.last_mutation excludes those, so u would skip every
        # pasted item and revert an unrelated field change elsewhere.
        screen.notify(f"added {len(prompts)} items")

    push_form(
        screen,
        FormSpec(
            "paste items — one per line",
            [Field("pasted", "items", "textarea", required=True)],
        ),
        commit,
    )
