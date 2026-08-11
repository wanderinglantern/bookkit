# bookkit

A local, single-user CRM for an insurance broker, run entirely as a terminal
application over a SQLite database. It manages the book of business: accounts,
the people at them, what was said and when, what's renewing, what's out to
market, and which carriers we place with.

It sits alongside [towerkit](../towerkit), which owns insurance program
structure as validated JSON files. bookkit projects those files into a query
cache (never authoritative) and edits them only through towerkit's own model,
validator, and canonical serialiser.

## Quick start (dev machine, uv)

```bash
uv sync --group dev
uv run bookctl init          # create the DB, run migrations
uv run bookctl seed --demo   # realistic demo fixture
uv run bookctl               # launch the TUI
```

`make demo` spins up a throwaway seeded database (with linked towerkit
program files) and drops you in the TUI. `make check` runs lint + mypy +
tests.

## Offline install (corporate machine, no PyPI)

Clone **both** repos side by side, then run the installer — the only network
access it needs is github.com, for the prebuilt wheelhouse:

```bash
git clone https://github.com/wanderinglantern/towerkit
git clone https://github.com/wanderinglantern/bookkit
cd bookkit && ./install.sh
./bookctl init
./bookctl                    # bare = the TUI; subcommands pass through
```

Re-run `./install.sh` after `git pull` in either repo. The wheelhouse zip is
built with `make wheelhouse` and attached to a GitHub release (same drill as
towerkit: rebuild and `gh release upload --clobber` whenever dependencies
change).

`bookctl today`, `bookctl renewals --days 90`, `bookctl search "swiss re"`,
`bookctl sync --roots ~/programs`, and `bookctl backup` work headless.

The database lives at `$XDG_DATA_HOME/bookkit/bookkit.db` (0600 — it holds
client contacts and premium figures; never commit it). `BOOKKIT_DB` overrides
the path.
