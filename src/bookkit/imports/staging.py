"""The reviewable middle of every import: parsed records, proposed actions,
issues — everything the preview screen shows and the committer trusts.
Pure data; no SQL, no repo imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    field: str
    message: str

    def __str__(self) -> str:
        mark = "✗" if self.severity is Severity.ERROR else "⚠"
        return f"{mark} {self.field}: {self.message}"


@dataclass
class StagedRecord:
    kind: str  # "account" | "contact" | "placement" | ...
    key: str  # display identity, e.g. "Atomic" or "Atomic/Rosa Silva"
    fields: dict[str, object]
    source_row: int
    action: str = "create"  # create | update | skip
    target_id: str | None = None
    issues: list[Issue] = field(default_factory=list)

    def error(self, field_name: str, message: str) -> None:
        self.issues.append(Issue(Severity.ERROR, field_name, message))

    def warn(self, field_name: str, message: str) -> None:
        self.issues.append(Issue(Severity.WARNING, field_name, message))


@dataclass
class StagedImport:
    source: str
    sha256: str
    records: list[StagedRecord]
    unmapped: list[str]
    # header → canonical key, and which of those headers were FUZZY matches.
    # A mapping is a prefill of the user's own column meanings; it has to be
    # rendered or nobody can see that "Expiration Date" became `expiry`.
    assigned: dict[str, str] = field(default_factory=dict)
    fuzzy: tuple[str, ...] = ()

    @property
    def errors(self) -> list[Issue]:
        return [
            issue
            for record in self.records
            for issue in record.issues
            if issue.severity is Severity.ERROR
        ]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def empty(self) -> bool:
        """No records at all — a readable file with nothing in it, or a paste
        that parsed to nothing."""
        return not self.records

    @property
    def committable(self) -> bool:
        """What green MEANS: committing will actually do something.

        `ok` alone said yes to an empty import, so an empty-but-readable
        spreadsheet rendered a green "OK to commit · 0 record(s)", took a
        backup and changed nothing. Zero records is not green."""
        return self.ok and not self.empty

    def first_error(self) -> tuple[StagedRecord, Issue] | None:
        for record in self.records:
            for issue in record.issues:
                if issue.severity is Severity.ERROR:
                    return record, issue
        return None

    def first_error_text(self) -> str | None:
        """`row 4 commission — '0.15' is ambiguous…`. A refusal that names the
        count and not the field leaves the user hunting; every caller that
        says "fix the errors first" says WHICH one."""
        first = self.first_error()
        if first is None:
            return None
        record, issue = first
        where = f"row {record.source_row}" if record.source_row else record.key
        return f"{where} {issue.field} — {issue.message}"

    def verdict(self) -> str:
        """The go/no-go line, on its own.

        report() puts this last, and the preview pane clips — so the single
        line the user needs to read was the first to disappear. Callers render
        this OUTSIDE the scrolling detail, where truncation cannot reach it."""
        # count ERRORS, the same thing `ok` is decided by — counting every
        # issue on skipped records would report warnings as blockers and
        # miss an error on a record staged to commit
        errors = sum(
            1
            for record in self.records
            for issue in record.issues
            if issue.severity is Severity.ERROR
        )
        if self.empty:
            head = "NOTHING TO COMMIT — no records read"
        elif self.ok:
            head = "OK to commit"
        else:
            head = "ERRORS — cannot commit"
        bits = [head, f"{len(self.records)} record(s)"]
        if errors:
            bits.append(f"{errors} error(s)")
        if self.unmapped:
            bits.append(f"{len(self.unmapped)} column(s) ignored")
        first = self.first_error_text()
        if first is not None:
            bits.append(f"first: {first}")
        return " · ".join(bits)

    def report(self, verbose: bool = False) -> str:
        """The dry-run text: counts by kind/action, per-record issues,
        unmapped headers. With verbose (paste previews), every record shows
        its PARSED FIELDS — what you're about to commit, not just how many."""
        lines = [f"import staging for {self.source}"]
        lines.extend(self._mapping_lines())
        kinds: dict[str, dict[str, int]] = {}
        for record in self.records:
            kinds.setdefault(record.kind, {}).setdefault(record.action, 0)
            kinds[record.kind][record.action] += 1
        for kind, actions in kinds.items():
            counts = ", ".join(f"{n} {action}" for action, n in sorted(actions.items()))
            lines.append(f"  {kind}: {counts}")
        for record in self.records:
            if verbose:
                lines.append(f"  {record.kind} {record.key} [{record.action}]")
                for field_name, value in record.fields.items():
                    if field_name in ("org_key", "org_id", "body") or value in (None, ""):
                        continue
                    lines.append(f"    {field_name}: {value}")
                lines.extend(f"    {issue}" for issue in record.issues)
            elif record.issues:
                lines.append(f"  {record.kind} {record.key} (row {record.source_row})")
                lines.extend(f"    {issue}" for issue in record.issues)
        if self.unmapped:
            lines.append("  unmapped columns (ignored): " + ", ".join(self.unmapped))
        lines.append(f"  {self.verdict()}")
        return "\n".join(lines)

    def _mapping_lines(self) -> list[str]:
        """Every header→field decision, fuzzy ones flagged.

        The fuzzy matcher runs at threshold 85 and nothing rendered its
        verdict: "Expiration Date" silently became `expiry` and the user had
        no way to check a prefill of their own column meanings."""
        if not self.assigned:
            return []
        lines = ["  columns read as:"]
        for header, key in self.assigned.items():
            mark = "  (fuzzy match — confirm)" if header in self.fuzzy else ""
            lines.append(f"    {header!r} → {key}{mark}")
        return lines
