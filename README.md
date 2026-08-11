# bookkit

A local, single-user CRM for an insurance broker, run entirely as a terminal
application over a SQLite database. It manages the book of business: accounts,
the people at them, what was said and when, what's renewing, what's out to
market, and which carriers we place with.

It sits alongside [towerkit](../towerkit), which owns insurance program
structure as validated JSON files. bookkit projects those files into a query
cache (never authoritative) and edits them only through towerkit's own model,
validator, and canonical serialiser.

## Quick start

```bash
uv sync --group dev
uv run bookctl init          # create the DB, run migrations
uv run bookctl seed --demo   # realistic demo fixture
uv run bookctl               # launch the TUI
```

`bookctl today`, `bookctl renewals --days 90`, `bookctl search "swiss re"`,
`bookctl sync --roots ~/programs`, and `bookctl backup` work headless.

The database lives at `$XDG_DATA_HOME/bookkit/bookkit.db` (0600 — it holds
client contacts and premium figures; never commit it). `BOOKKIT_DB` overrides
the path.
