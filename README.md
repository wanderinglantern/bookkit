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
The connector panel is hand-entry, so ask for the values rather than typing
them from memory — run this **on the machine the connector will run on**:

```
bookctl mcp --connector-info
```

It prints one line per field, ready to paste:

```
Add MCP Connector — paste one line per field:

  Name         bookkit
  Command      /Users/you/Developer/bookkit/.venv/bin/bookctl
  Arguments    mcp
  Env Secrets  (none)
  Mode         both
```

Command is an absolute path on purpose. `bookctl` is not on `PATH` — the
repo-root `bookctl` is an `sh` wrapper around `./.venv/bin/bookctl`, and a GUI
app inherits neither your `PATH` nor a shell alias, so a bare `bookctl` fails
to launch. Arguments gains `--db, <path>, mcp` when the database lives
anywhere the connector could not find on its own; a database path is not a
secret, so it goes in Arguments rather than the encrypted Env Secrets store.

Then verify before you trust it:

```
bookctl mcp --check
```

It exits 0 only when the console script is executable, the database exists
(reporting its size and age, so a stale copy is obvious), the schema is
migrated, and startup writes nothing to stdout — stdout is the MCP wire, and
one stray `print` corrupts the protocol. Neither flag starts the server, and
neither creates the database.

The assistant can manage the whole book side: read everything, create
clients/contacts/opportunities/projects/needs/tasks/RFIs/team members,
assign the team, move pipeline stages one gate at a time, and deliberately
edit fields (compare-and-set: it must prove it read the current value, or
the edit refuses). Every write is one revertible batch — the MCP CHANGES
section in the Navigator lists them, `enter` shows before→after, and `R`
puts one back. Placements and towerkit program files are read-only to the
assistant by design.
