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

## Install (corporate machine)

Clone **both** repos side by side, then run the installer. It tries PyPI
first (fast, picks up new dependencies automatically after every pull) and
falls back to the prebuilt wheelhouse from the GitHub release when there is
no PyPI access (github.com is the only network needed then):

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

## MCP connector (work-machine assistant)

The `bookctl mcp` command exposes an MCP server for a work-machine assistant.
Configure it with these settings:

- Name: `bookkit`
- Command: `bookctl`
- Arguments: `mcp`
- Environment: `BOOKKIT_DB` (only if the DB lives off the default path)
- Mode: both

Smoke check: `bookctl mcp` starts silently, waits on stdin, and can be stopped
with Ctrl+C. Any output to stdout on startup is a bug.
