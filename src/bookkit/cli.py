"""bookctl — the terminal entry point. No args launches the TUI; subcommands
work headless so the daily brief can be piped anywhere."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

from . import connector, db
from .dates import days_until
from .models import Org
from .money import format_cents, format_cents_compact
from .repo import search as search_repo
from .repo import tasks as tasks_repo
from .services import renewals, sla, staleness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bookctl", description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="database path override")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="create the database and run migrations")
    sub.add_parser("migrate", help="apply pending migrations")

    seed_p = sub.add_parser("seed", help="load the demo fixture")
    seed_p.add_argument("--demo", action="store_true", required=True,
                        help="load the demo fixture (the only mode there is)")
    seed_p.add_argument("--force", action="store_true",
                        help="seed a book that already has accounts — takes a "
                             "backup first")
    seed_p.add_argument("--programs-dir", type=Path, default=None,
                        help="also write three linked towerkit program files here")

    sub.add_parser("today", help="today's brief as plain text")

    open_p = sub.add_parser("open", help="launch the TUI on one client")
    open_p.add_argument("who", help="ref (ACC-0001) or part of the account name")

    mcp_p = sub.add_parser("mcp", help="stdio MCP server (work-machine cowork connector)")
    mcp_p.add_argument("--connector-info", action="store_true",
                       help="print the Add MCP Connector field values instead of serving")
    mcp_p.add_argument("--check", action="store_true",
                       help="verify the connector starts and finds the book; exit 1 if not")

    renew_p = sub.add_parser("renewals", help="upcoming renewals")
    renew_p.add_argument("--days", type=int, default=90)

    search_p = sub.add_parser("search", help="global full-text search")
    search_p.add_argument("query")

    sync_p = sub.add_parser("sync", help="project towerkit program files")
    sync_p.add_argument("--roots", type=Path, nargs="+", default=None,
                        help="override the configured program roots")
    sync_p.add_argument("--path", type=Path, default=None,
                        help="project exactly one file instead of scanning the roots")

    roots_p = sub.add_parser("roots", help="show or set the program file locations")
    roots_p.add_argument("paths", type=Path, nargs="*",
                         help="directories to save (omit to show current)")
    roots_p.add_argument("--json", action="store_true",
                         help="emit {\"roots\": [...]} for other tools to read")

    backup_p = sub.add_parser("backup", help="timestamped copy + integrity check")
    backup_p.add_argument("--dest", type=Path, default=None)

    template_p = sub.add_parser("template", help="write a populate-and-reimport workbook")
    template_p.add_argument("flow", choices=["book", "program"])
    template_p.add_argument("out", type=Path, metavar="template.xlsx")

    import_p = sub.add_parser("import", help="stage a bulk import and print the report")
    import_p.add_argument("flow", choices=["book"])
    import_p.add_argument("file", type=Path)
    import_p.add_argument("--dry-run", action="store_true",
                          help="report only (committing happens in the TUI)")

    export_p = sub.add_parser("export", help="client-facing workbook exports")
    export_p.add_argument("flow", choices=["open-items"])
    export_p.add_argument("org", help="client name or ref")
    export_p.add_argument("--out", type=Path, default=None,
                          help="default: <ref>-open-items-<date>.xlsx")

    web_p = sub.add_parser("web", help="serve the browser interface on localhost")
    web_p.add_argument("--port", type=int, default=8931)
    web_p.add_argument("--no-browser", action="store_true")

    return parser


def _run(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    guard = _refuse_a_missing_book(args)
    if guard is not None:
        return guard

    if args.command is None:
        from .tui.app import BookkitApp

        BookkitApp(db_path=args.db).run()
        return 0

    if args.command == "open":
        # a shell alias should be able to put you in front of a client, rather
        # than in front of the navigator with the name still to type (F20)
        from .tui.app import BookkitApp

        conn = db.connect(args.db)
        try:
            org = resolve_org(conn, args.who)
        finally:
            conn.close()
        if org is None:
            print(f"no account matches {args.who!r}", file=sys.stderr)
            return 1
        BookkitApp(db_path=args.db, open_org_id=org.id).run()
        return 0

    if args.command == "mcp" and (args.connector_info or args.check):
        # Deliberately ahead of db.connect: it creates and migrates the file,
        # so running these through _dispatch would conjure the very database
        # --check exists to tell you is missing.
        if args.connector_info:
            _print_connector_fields(connector.fields(args.db))
            return 0
        report = connector.check(args.db)
        for result in report.checks:
            print(f"{'✓' if result.ok else '✗'} {result.label:<8}  {result.detail}")
        return 0 if report.ok else 1

    if args.command == "web":
        from .web.serve import serve

        return serve(args.db, args.port, open_browser=not args.no_browser)

    conn = db.connect(args.db)
    try:
        return _dispatch(args, conn)
    finally:
        conn.close()


# Commands that only READ. db.connect creates the file on demand, so without
# this a typo in --db (or a wrong $BOOKKIT_DB, or the wrong machine) produced
# a cheerful all-zeros brief and exit 0 — indistinguishable from a quiet book,
# and it left an empty database behind. Creation belongs to init/migrate/seed.
READ_ONLY_COMMANDS = frozenset(
    {"today", "renewals", "search", "export", "open", "web"}
)


def _refuse_a_missing_book(args: argparse.Namespace) -> int | None:
    if args.command not in READ_ONLY_COMMANDS:
        return None
    path = Path(args.db) if args.db else db.default_db_path()
    if path.exists():
        return None
    print(
        f"no book at {path} — run `bookctl init` to create one",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    """Thin wrapper: `_run` does the work, this makes a failure legible.

    argparse still exits 2 on usage errors. What this catches is everything
    below it — a --db pointing at a text file, a read-only filesystem, a
    backup destination that already exists. Each of those used to reach the
    terminal as a raw traceback, and a typo'd --db is a routine mistake, not
    a crash."""
    # sys.argv when called from the entry point, or the hint below names the
    # DEFAULT database while the failure is about the --db you passed —
    # misleading in exactly the message meant to clear that up
    argv = list(sys.argv[1:]) if argv is None else argv
    try:
        return _run(argv)
    except (sqlite3.DatabaseError, OSError, ValueError) as exc:
        path = _db_hint(argv)
        where = f" ({path})" if path else ""
        print(f"bookctl: {exc}{where}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _db_hint(argv: list[str] | None) -> str:
    """Which database the failure is about — the single most useful thing to
    add, because the usual cause is that it is not the one you meant."""
    try:
        if argv and "--db" in argv:
            return str(Path(argv[argv.index("--db") + 1]))
        return str(db.default_db_path())
    except (IndexError, ValueError):
        return ""


def resolve_org(conn: sqlite3.Connection, who: str) -> Org | None:
    """Find one account by ref or by name. An exact ref wins outright; a name
    goes through the same fuzzy matcher the book filter uses, so
    `bookctl open atomic` finds "Atomic Industries, Inc."."""
    from rapidfuzz import fuzz, process

    from .repo import orgs

    needle = who.strip()
    if not needle:
        return None
    everyone = orgs.list_orgs(conn)
    by_ref = {o.ref.casefold(): o for o in everyone}
    exact = by_ref.get(needle.casefold())
    if exact is not None:
        return exact
    by_name = {o.name: o for o in everyone}
    match = process.extractOne(
        needle, list(by_name), scorer=fuzz.WRatio, score_cutoff=75
    )
    return by_name[match[0]] if match else None


def _dispatch(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    if args.command == "init":
        path = args.db or db.default_db_path()
        print(f"database ready at {path} (schema v{db.schema_version(conn)})")
        print("next: bookctl  (the TUI — press b then a to create your first account,")
        print("       m then a for markets; or `bookctl seed --demo` for a demo book)")
        return 0

    if args.command == "migrate":
        applied = db.apply_migrations(conn)
        print(f"applied {applied}" if applied else "already up to date")
        return 0

    if args.command == "seed":
        from .repo import orgs as orgs_repo
        from .seed import seed

        # $BOOKKIT_DB defaults to the REAL book and this command is in the
        # README quick start, so an unguarded seed interleaves 35 fake
        # accounts and 200 fake interactions into live data. The rows carry
        # no provenance and are not a batch, so they cannot be cleanly
        # removed afterwards — refusing is the only cheap undo.
        path = args.db or db.default_db_path()
        existing = len(orgs_repo.list_orgs(conn))
        if existing and not args.force:
            print(
                f"{path} already has {existing} accounts — refusing to seed "
                f"demo data into a book in use.\n"
                f"  pass --force to seed anyway (a backup is taken first), "
                f"or point --db at a scratch file.",
                file=sys.stderr,
            )
            return 2
        if existing:
            made = db.snapshot(conn, Path(path))
            print(f"backed up {existing} accounts to {made}")
        print(f"seeding demo data into {path}")
        counts = seed(conn, programs_dir=args.programs_dir)
        print("seeded: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        return 0

    if args.command == "today":
        _print_today(conn)
        return 0

    if args.command == "mcp":
        from .mcpserver import serve

        serve(args.db)
        return 0

    if args.command == "renewals":
        for item in renewals.upcoming(conn, days=args.days):
            premium = (
                format_cents_compact(item.placement.total_premium)
                if item.placement.total_premium
                else "—"
            )
            print(
                f"{item.placement.period_to}  {item.days_remaining:>3}d  "
                f"[{item.placement.status:<11}] {item.org.name:<32} "
                f"{item.placement.program_name}  {premium}"
            )
        return 0

    if args.command == "search":
        hits = search_repo.search(conn, args.query)
        if not hits:
            print("no matches")
            return 1
        current = None
        for hit in hits:
            if hit.kind != current:
                current = hit.kind
                print(f"\n{current.upper()}S")
            print(f"  {hit.title}" + (f"  — {hit.snippet}" if hit.snippet else ""))
        return 0

    if args.command == "sync":
        from . import sync

        if args.path is not None:
            try:
                diags = sync.project(conn, Path(args.path))
            except sync.AmbiguousPlacement as exc:
                print(f"✗ {args.path}: {exc} — confirm it in the review queue")
                return 1
            for diag in diags.items:
                print(f"  {diag}")
            print(("✓ " if diags.ok else "✗ ") + str(args.path))
            return 0 if diags.ok else 1

        roots = (
            [Path(r) for r in args.roots] if args.roots else sync.configured_roots(conn)
        )
        if not roots:
            print("no program roots configured — run `bookctl roots <dir> …` first")
            return 2
        report = sync.project_all(conn, roots)
        print(report.render())
        return 0 if report.ok else 1

    if args.command == "roots":
        from . import sync
        from .repo import settings as settings_repo

        if args.paths:
            missing = [p for p in args.paths if not p.expanduser().is_dir()]
            if missing:
                # stderr, so `roots --json` stdout stays parseable on the
                # error path too — towerctl json.loads() it either way
                print(f"not a directory: {missing[0]}", file=sys.stderr)
                return 2
            # .resolve(), not just .expanduser(): a relative root validates
            # against the CURRENT cwd and then means nothing from anywhere
            # else, including inside towerctl's own process
            settings_repo.set_program_roots(
                conn, [str(p.expanduser().resolve()) for p in args.paths]
            )
        roots = sync.configured_roots(conn)
        if args.json:
            # towerctl mcp --connector-info parses this; keep it the only thing
            # on stdout, including when nothing is configured.
            print(json.dumps({"roots": [str(r) for r in roots]}))
            return 0
        if not roots:
            print("no program roots configured")
        for root in roots:
            count = len(sync.scan([root]))
            print(f"{root}  ({count} program file(s))")
        return 0

    if args.command == "backup":
        from datetime import datetime

        dest = args.dest
        if dest is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            base = args.db or db.default_db_path()
            dest = base.parent / "backups" / f"bookkit-{stamp}.db"
        db.backup(conn, dest)
        print(f"backup written and verified: {dest}")
        return 0

    if args.command == "template":
        if args.flow == "book":
            from .imports.fieldspec import BOOK_FIELDS, write_template

            print(write_template(BOOK_FIELDS, args.out))
        else:  # program — one registry each side, no duplication
            from towerkit.ingest_template import write_template as tk_template

            print(tk_template(args.out))
        return 0

    if args.command == "import":
        from .imports.fieldspec import BOOK_FIELDS
        from .imports.mappers.book import stage_book
        from .imports.readers import read_table
        from .imports.tablemap import map_headers

        try:
            table = read_table(args.file)
        except (ValueError, OSError) as exc:
            print(exc)
            return 1
        mapping = map_headers(table.headers, BOOK_FIELDS)
        staged = stage_book(conn, table, mapping)
        print(staged.report())
        if not staged.ok:
            return 1
        if not args.dry_run:
            print("commit happens in the TUI import screen; use --dry-run for now")
            return 2
        return 0

    if args.command == "export":
        from .repo import orgs as orgs_repo
        from .services import export_open_items

        org = orgs_repo.find(conn, args.org) or orgs_repo.find_by_name(conn, args.org)
        if org is None:
            from rapidfuzz import process

            names = [o.name for o in orgs_repo.list_orgs(conn, kind="client")]
            close = process.extract(args.org, names, limit=3, score_cutoff=60)
            hint = f" — did you mean: {', '.join(m[0] for m in close)}" if close else ""
            print(f"no client matching {args.org!r}{hint}")
            return 2
        today = date.today()
        out = args.out or Path(f"{org.ref}-open-items-{today.isoformat()}.xlsx")
        try:
            path = export_open_items.write(conn, org.id, out, today)
        except ImportError as exc:
            print(f"export needs a newer towerkit — update it ({exc})")
            return 1
        except OSError as exc:
            print(f"could not write {out}: {exc}")
            return 1
        print(
            f"wrote {path}"
            f"{export_open_items.withheld_note(conn, org.id)}"
            f"{export_open_items.soi_note(conn, org.id, today)}"
        )
        return 0

    return 2


def _print_connector_fields(fields: connector.ConnectorFields) -> None:
    """One line per input in the Add MCP Connector dialog, in panel order."""
    env = ", ".join(f"{k}={v}" for k, v in fields.env.items()) or "(none)"
    print("Add MCP Connector — paste one line per field:\n")
    print(f"  Name         {fields.name}")
    print(f"  Command      {fields.command}")
    print(f"  Arguments    {', '.join(fields.arguments)}")
    print(f"  Env Secrets  {env}")
    print(f"  Mode         {fields.mode}")


def _print_today(conn: sqlite3.Connection) -> None:
    today = date.today()
    iso = today.isoformat()
    print(f"bookkit — {iso}\n")

    due = tasks_repo.open_tasks(conn, due_by=iso)
    print(f"TASKS DUE ({len(due)})")
    for task in due[:15]:
        overdue = days_until(task.due_on, today) if task.due_on else 0
        marker = f"{-overdue}d overdue" if overdue < 0 else "today"
        print(f"  [{marker:>10}] {task.title}")
    if not due:
        print("  none")

    items = renewals.upcoming(conn, today, days=120)
    print(f"\nRENEWALS NEXT 120 DAYS ({len(items)})")
    for item in items[:15]:
        cover = f" ({item.lines})" if item.lines else ""
        print(
            f"  {item.placement.period_to} ({item.days_remaining:>3}d) "
            f"{item.org.name} — {item.placement.program_name}{cover} "
            f"[{item.placement.status}]"
        )
    if not items:
        print("  none")

    from .repo import projects as projects_repo

    needs = projects_repo.needs_due(conn, today, days=120)
    print(f"\nPROJECT NEEDS ({len(needs)})")
    for need in needs[:15]:
        d = days_until(need["needed_by"], today)
        marker = f"{-d}d overdue" if d < 0 else f"{d}d"
        print(
            f"  {need['needed_by']} ({marker:>10}) {need['org_name']} — "
            f"{need['line']} for {need['project_name']} [{need['status']}]"
        )
    if not needs:
        print("  none")

    from .services import rfi as rfi_svc

    chases = rfi_svc.outstanding_requests(conn, today, days=120)
    print(f"\nREQUESTS TO CHASE ({len(chases)})")
    for chase in chases[:15]:
        when = (
            f"{-chase.days_remaining}d overdue"
            if chase.days_remaining < 0
            else f"{chase.days_remaining:>3}d"
        )
        asker = f" ({chase.market_name})" if chase.market_name else ""
        print(
            f"  [{when:>10}] {chase.org_name} — {chase.request.title}{asker} "
            f"· {chase.open_count} of {chase.total_count} open"
        )
    if not chases:
        print("  none")

    stale = staleness.stale_accounts(conn, today)
    print(f"\nSTALE ACCOUNTS ({len(stale)})")
    for account in stale[:10]:
        premium = format_cents(account.premium) if account.premium else "no premium on file"
        last = account.last_interaction_on or "never"
        print(f"  {account.org.name}: last touch {last} ({account.days_stale}d), {premium}")
    if not stale:
        print("  none")

    overdue_subs = sla.past_sla(conn, today)
    print(f"\nSUBMISSIONS PAST SLA ({len(overdue_subs)})")
    for late in overdue_subs[:10]:
        print(
            f"  {late.market.name}: {late.account.name}, out {late.days_out}d "
            f"(sent {late.submission.sent_on})"
        )
    if not overdue_subs:
        print("  none")


if __name__ == "__main__":
    sys.exit(main())
