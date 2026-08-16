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
        bits = [
            "OK to commit" if self.ok else "ERRORS — cannot commit",
            f"{len(self.records)} record(s)",
        ]
        if errors:
            bits.append(f"{errors} error(s)")
        if self.unmapped:
            bits.append(f"{len(self.unmapped)} column(s) ignored")
        return " · ".join(bits)

    def report(self, verbose: bool = False) -> str:
        """The dry-run text: counts by kind/action, per-record issues,
        unmapped headers. With verbose (paste previews), every record shows
        its PARSED FIELDS — what you're about to commit, not just how many."""
        lines = [f"import staging for {self.source}"]
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
        status = "OK to commit" if self.ok else "ERRORS — cannot commit"
        lines.append(f"  {status}")
        return "\n".join(lines)
