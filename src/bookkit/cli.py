"""bookctl — the terminal entry point. No args launches the TUI; subcommands
work headless so the daily brief can be piped anywhere."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from . import db
from .dates import days_until
from .money import format_cents, format_cents_compact
from .repo import search as search_repo
from .repo import tasks as tasks_repo
from .services import renewals, sla, staleness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bookctl", description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="database path override")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="create the database and run migrations")
    sub.add_parser("migrate", help="apply pending migrations")

    seed_p = sub.add_parser("seed", help="load the demo fixture")
    seed_p.add_argument("--demo", action="store_true", required=True)
    seed_p.add_argument("--programs-dir", type=Path, default=None,
                        help="also write three linked towerkit program files here")

    sub.add_parser("today", help="today's brief as plain text")

    renew_p = sub.add_parser("renewals", help="upcoming renewals")
    renew_p.add_argument("--days", type=int, default=90)

    search_p = sub.add_parser("search", help="global full-text search")
    search_p.add_argument("query")

    sync_p = sub.add_parser("sync", help="project towerkit program files")
    sync_p.add_argument("--roots", type=Path, nargs="+", default=None,
                        help="override the configured program roots")

    roots_p = sub.add_parser("roots", help="show or set the program file locations")
    roots_p.add_argument("paths", type=Path, nargs="*",
                         help="directories to save (omit to show current)")

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

    args = parser.parse_args(argv)

    if args.command is None:
        from .tui.app import BookkitApp

        BookkitApp(db_path=args.db).run()
        return 0

    conn = db.connect(args.db)
    try:
        return _dispatch(args, conn)
    finally:
        conn.close()


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
        from .seed import seed

        counts = seed(conn, programs_dir=args.programs_dir)
        print("seeded: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        return 0

    if args.command == "today":
        _print_today(conn)
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
                print(f"not a directory: {missing[0]}")
                return 2
            settings_repo.set_program_roots(
                conn, [str(p.expanduser()) for p in args.paths]
            )
        roots = sync.configured_roots(conn)
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

    return 2


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

    items = renewals.upcoming(conn, today, days=90)
    print(f"\nRENEWALS NEXT 90 DAYS ({len(items)})")
    for item in items[:15]:
        print(
            f"  {item.placement.period_to} ({item.days_remaining:>3}d) "
            f"{item.org.name} — {item.placement.program_name} [{item.placement.status}]"
        )
    if not items:
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
