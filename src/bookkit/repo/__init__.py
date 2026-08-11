"""Data access. Every SQL query in bookkit lives under this package; the TUI
never touches SQL. Mutations funnel through repo.base so the event_log and
soft-delete rules cannot be forgotten."""
