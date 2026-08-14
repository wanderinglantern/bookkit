# MCP Team Edits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let MCP correct an existing team assignment in place, rename a team member, and retire one — closing the two gaps the write-expansion phase left behind.

**Architecture:** Almost entirely additive to `src/bookkit/mcpserver.py`. Assignment editing and renaming ride the existing `edit_field` compare-and-set path by adding entries to the `_EDITABLE` registry and one `_edit_target` branch — no new value type is needed, because `_clean_typed` already treats a tuple as a closed vocabulary. Retirement gets two new tools (`member_deactivate` / `member_reactivate`), following the codebase convention that state transitions never ride `edit_field`.

**Tech Stack:** Python 3.12, SQLite, MCP SDK (`MCPServer`), pytest, mypy, ruff, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-14-mcp-team-edits-design.md`

## Global Constraints

- **Gates before every commit:** `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. When chaining in shell, never pipe test output before the `&&` gate — pipes eat exit codes. Redirect to a file, gate on the command, tail the file after.
- **Set `S` once at the start of your session**, to your own scratchpad directory (the path in your system prompt), and use it for every gate run below: `export S=<your scratchpad dir>`. Never redirect gate output to `/tmp` — concurrent pytest runs interleave there and you will read another session's results as your own.
- **No raw SQL outside `repo/`.** `mcpserver.py` calls `repo/` functions only. A convention test enforces this.
- **Every write goes through `_open_batch`** — one MCP call is one undo unit, revertible by `revert_batch`, counted against `db.BLAST_CAP` (currently 250).
- **Compare-and-set is not optional** on `edit_field`: `expecting` must match the stored value or the call refuses and writes nothing.
- **`ref` is never fuzzy-matched on a write target.** Exact name or exact id only.
- **Never re-scope an assignment** (`org_id` / `placement_id` stay out of `_EDITABLE`) — the DDL holds `CHECK ((org_id IS NULL) != (placement_id IS NULL))` and single-field compare-and-set cannot move both columns together.
- Tests call the private `_helpers` directly (e.g. `mcpserver._edit_field(rw, …)`), not the async tool wrappers. Use the existing `server_db` fixture and `_rw(server_db)` helper in `tests/test_mcpserver.py`.

---

### Task 1: Assignment edit — `edit_field(kind="team_assignment")`

Adds the registry entry, the resolution branch, and the `notes` key on `team_roster`. The roster change ships here rather than separately: without it, `notes` would be advertised as editable and be unreachable, because compare-and-set requires `expecting` to be a value a read returned.

**Files:**
- Modify: `src/bookkit/mcpserver.py` — `_editable()` (~line 1467), `_edit_target()` (~line 1602, before the final `raise`), `_team_roster()` (~line 1294)
- Test: `tests/test_mcpserver.py` (append to the `# -- team ---` section, after `test_unassign_takes_exact_id_and_reverts`)

**Interfaces:**
- Consumes: `mcpserver._edit_field`, `mcpserver._team_assign`, `mcpserver._member_create`, `db.connect`, `repo.team`, `models.TEAM_ROLES`, `models.TeamAssignment`, `repo.placements.get`
- Produces: `_EDITABLE["team_assignment"]` = `{"role": TEAM_ROLES, "lines": "text", "notes": "text"}`; `_edit_target` accepting `kind="team_assignment"` with `ref` = `assignment_id`, returning `(assignment_id, org_id, TeamAssignment)`; `_team_roster()` assignment dicts gaining a `"notes"` key

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcpserver.py`:

```python
def test_edit_field_changes_an_assignment_role(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                      role="analyst")
    aid = assigned["assignment_id"]

    out = mcpserver._edit_field(
        rw, "team_assignment", aid, "role", "account_lead",
        expecting="analyst",
    )
    assert out["batch"].startswith("MCP-")
    assert team.for_org(rw, org.id)[0]["role"] == "account_lead"


def test_edit_field_refuses_a_role_outside_the_vocabulary(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                      role="analyst")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", assigned["assignment_id"], "role",
            "wizard", expecting="analyst",
        )
    assert "account_lead" in str(err.value)


def test_edit_field_on_an_assignment_refuses_a_stale_expecting(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                      role="analyst")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", assigned["assignment_id"], "role",
            "account_lead", expecting="claims_advocate",
        )
    assert "analyst" in str(err.value)
    assert team.for_org(rw, org.id)[0]["role"] == "analyst"   # nothing written


def test_edit_field_refuses_an_unknown_assignment_id(server_db):
    rw, _ = _rw(server_db)
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", "NOPE", "lines", "cyber", expecting="x",
        )
    assert "team_roster" in str(err.value)


def test_edit_field_refuses_rescoping_an_assignment(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", assigned["assignment_id"], "org_id",
            "somewhere-else", expecting=None,
        )
    assert "not editable" in str(err.value)


def test_assignment_notes_round_trip_through_the_roster(server_db):
    """notes is only editable if a read hands the model its current value —
    compare-and-set has nothing to compare against otherwise."""
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme")
    aid = assigned["assignment_id"]

    dana = next(m for m in mcpserver._team_roster(rw)["members"]
                if m["name"] == "Dana Cruz")
    assert dana["assignments"][0]["notes"] is None

    mcpserver._edit_field(rw, "team_assignment", aid, "notes",
                          "covers the London tower", expecting=None)

    dana = next(m for m in mcpserver._team_roster(rw)["members"]
                if m["name"] == "Dana Cruz")
    assert dana["assignments"][0]["notes"] == "covers the London tower"


def test_edit_field_resolves_org_for_a_deal_level_assignment(server_db):
    """A placement-scoped assignment has org_id NULL; the batch still has to
    be stamped with the org, or the change is invisible to that client's
    history."""
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import placements

    rw, org = _rw(server_db)
    placement = placements.create(rw, org.id, program_name="Tower GL",
                                  effective_on="2026-01-01",
                                  expires_on="2027-01-01")
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz",
                                      placement_ref=placement.ref,
                                      role="analyst")

    out = mcpserver._edit_field(
        rw, "team_assignment", assigned["assignment_id"], "role",
        "account_lead", expecting="analyst",
    )
    assert batches_repo.get(rw, out["batch"]).org_id == org.id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "assignment" 2>&1 | tail -20
```
Expected: FAIL. `test_edit_field_changes_an_assignment_role` fails with `ValueError: cannot edit kind 'team_assignment'; editable: [...]`.

Note: `test_edit_field_resolves_org_for_a_deal_level_assignment` uses `placements.create` and `batches_repo.get`. If either signature differs from the call above, fix the *test* to match the real signature — do not change the source to fit the test. Confirm with `grep -n "def create" src/bookkit/repo/placements.py` and `grep -n "def get" src/bookkit/repo/batches.py`.

- [ ] **Step 3: Add the registry entry**

In `src/bookkit/mcpserver.py`, `_editable()`, change the import line and add the entry after `"team_member"`:

```python
def _editable() -> dict[str, dict[str, Any]]:
    from .models import PROJECT_STATUSES, TEAM_ROLES
```

```python
        "team_member": {
            "title": "text", "specialty": "text", "email": "text",
            "phone": "text", "notes": "text",
        },
        # role reuses team_assign's vocabulary so the two paths cannot drift.
        # org_id / placement_id are deliberately absent: re-scoping moves two
        # columns at once and single-field compare-and-set cannot do it.
        "team_assignment": {
            "role": TEAM_ROLES, "lines": "text", "notes": "text",
        },
```

- [ ] **Step 4: Add the `_edit_target` branch**

In `_edit_target()`, immediately before the final `raise ValueError(f"cannot edit kind {kind!r}; …")`:

```python
    if kind == "team_assignment":
        from .models import TeamAssignment
        from .repo import base as base_repo

        row = base_repo.get(conn, "team_assignment", ref)
        if row is None:
            raise ValueError(
                f"no assignment {ref!r} — read team_roster for exact ids"
            )
        assignment = TeamAssignment.from_row(row)
        org_id = assignment.org_id
        if org_id is None and assignment.placement_id is not None:
            from .repo import placements as placements_repo

            org_id = placements_repo.get(conn, assignment.placement_id).org_id
        return assignment.id, org_id, assignment
```

- [ ] **Step 5: Add `notes` to the roster payload**

In `_team_roster()`, extend the assignment dict:

```python
            assignments.append({
                "assignment_id": row["id"],
                "account": row["org_name"] if "org_name" in row.keys() else None,
                "placement": row["placement_ref"] if "placement_ref" in row.keys() else None,
                "role": row["role"], "lines": row["lines"],
                "notes": row["notes"],
            })
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "assignment or roster" 2>&1 | tail -20
```
Expected: PASS, all of them.

- [ ] **Step 7: Run the full gates**

```bash
uv run pytest -q > $S/p.txt 2>&1 && uv run mypy src > $S/m.txt 2>&1 && uv run ruff check src tests > $S/r.txt 2>&1 && echo GATES-PASS; tail -3 $S/p.txt $S/m.txt $S/r.txt
```
Expected: `GATES-PASS`.

- [ ] **Step 8: Commit**

```bash
git add src/bookkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: edit_field reaches an existing team assignment (role, lines, notes)"
```

---

### Task 2: Rename a team member

**Files:**
- Modify: `src/bookkit/mcpserver.py` — `_editable()` `team_member` entry; new `_guard_member_rename()` helper next to `_find_member` (~line 1277); one call site in `_edit_field()` after the `cleaned = _clean_typed(...)` line
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `mcpserver._member_create`, `mcpserver._edit_field`, `repo.team.list_members`
- Produces: `_guard_member_rename(conn, member_id: str, new_name: str) -> None` — raises `ValueError` when another member holds `new_name`; `_EDITABLE["team_member"]["name"] = "text"`

- [ ] **Step 1: Write the failing tests**

```python
def test_edit_field_renames_a_member_by_their_old_name(server_db):
    from bookkit.repo import team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruze")

    out = mcpserver._edit_field(
        rw, "team_member", "Dana Cruze", "name", "Dana Cruz",
        expecting="Dana Cruze",
    )
    assert out["batch"].startswith("MCP-")
    names = [m.name for m in team.list_members(rw, active_only=False)]
    assert names == ["Dana Cruz"]


def test_rename_refuses_a_name_another_member_holds(server_db):
    """Two members sharing a name makes every lookup ambiguous — _find_member
    and _edit_target both take the first match."""
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_create(rw, "Sam Okafor")

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_member", "Sam Okafor", "name", "dana cruz",
            expecting="Sam Okafor",
        )
    assert "Dana Cruz" in str(err.value)


def test_rename_refuses_a_name_an_INACTIVE_member_holds(server_db):
    """Inactive members still resolve in _find_member (active_only=False), so
    they collide just as hard as active ones."""
    from bookkit.repo import base, team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_create(rw, "Sam Okafor")
    gone = next(m for m in team.list_members(rw, active_only=False)
                if m.name == "Dana Cruz")
    base.update(rw, "team_member", gone.id, {"active": 0})

    with pytest.raises(ValueError):
        mcpserver._edit_field(
            rw, "team_member", "Sam Okafor", "name", "Dana Cruz",
            expecting="Sam Okafor",
        )


def test_renaming_to_the_same_name_is_not_a_self_collision(server_db):
    """The guard must exclude the member being renamed, or a no-op rename
    reports a collision with itself."""
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    out = mcpserver._edit_field(
        rw, "team_member", "Dana Cruz", "name", "Dana  Cruz",
        expecting="Dana Cruz",
    )
    assert out["batch"].startswith("MCP-")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "rename or renaming" 2>&1 | tail -20
```
Expected: FAIL with `ValueError: 'name' is not editable on a team_member; allowed: [...]`.

Note on the last test: `_clean_field_value` may normalise `"Dana  Cruz"` (double space) back to `"Dana Cruz"`. If it does, that test still exercises the self-collision path and should pass once the guard excludes `member_id`. If `_clean_field_value` leaves it unchanged, it also passes. Either way the assertion is on the batch, not the stored name.

- [ ] **Step 3: Add `name` to the registry**

```python
        "team_member": {
            "name": "text", "title": "text", "specialty": "text",
            "email": "text", "phone": "text", "notes": "text",
        },
```

- [ ] **Step 4: Write the guard helper**

Add directly below `_find_member` in `src/bookkit/mcpserver.py`:

```python
def _guard_member_rename(
    conn: sqlite3.Connection, member_id: str, new_name: str
) -> None:
    """Renaming onto a name someone else holds makes every member lookup
    ambiguous — _find_member and _edit_target both take the first match — so
    this refuses rather than letting a later write land on the wrong row."""
    from .repo import team

    for other in team.list_members(conn, active_only=False):
        if other.id != member_id and other.name.lower() == new_name.lower():
            raise ValueError(
                f"team member {other.name} already holds that name — rename "
                f"or deactivate them first"
            )
```

- [ ] **Step 5: Call the guard from `_edit_field`**

In `_edit_field()`, directly after `cleaned = _clean_typed(vtype, field, value)` and before the `with _open_batch(...)` block:

```python
    if kind == "team_member" and field == "name":
        _guard_member_rename(conn, entity_id, cleaned)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "rename or renaming" 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 7: Run the full gates**

```bash
uv run pytest -q > $S/p.txt 2>&1 && uv run mypy src > $S/m.txt 2>&1 && uv run ruff check src tests > $S/r.txt 2>&1 && echo GATES-PASS; tail -3 $S/p.txt $S/m.txt $S/r.txt
```
Expected: `GATES-PASS`.

- [ ] **Step 8: Commit**

```bash
git add src/bookkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: rename a team member through edit_field, behind a duplicate guard"
```

---

### Task 3: `member_deactivate` — refuse while assignments are live

No cascade yet. This task delivers the refusal and the clean-retirement path.

**Files:**
- Modify: `src/bookkit/mcpserver.py` — new `_assignment_label()` and `_member_deactivate()` helpers (place after `_team_unassign`, ~line 1403); new `@server.tool()` wrapper after `team_unassign` (~line 494)
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `mcpserver._find_member`, `_open_batch`, `_provenance`, `repo.team.for_member`, `repo.base.update`
- Produces: `_member_deactivate(conn, name: str) -> dict[str, Any]` returning `{"name": str, "active": False, "unassigned": int, "batch": str}`; tool `member_deactivate(name)`; `_assignment_label(row) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def test_member_deactivate_retires_someone_with_no_assignments(server_db):
    from bookkit.repo import team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")

    out = mcpserver._member_deactivate(rw, "Dana Cruz")
    assert out["active"] is False
    assert out["unassigned"] == 0
    assert out["batch"].startswith("MCP-")
    assert [m.name for m in team.list_members(rw, active_only=True)] == []
    assert [m.name for m in team.list_members(rw, active_only=False)] == ["Dana Cruz"]


def test_member_deactivate_refuses_and_names_the_clients(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme")

    with pytest.raises(ValueError) as err:
        mcpserver._member_deactivate(rw, "Dana Cruz")
    message = str(err.value)
    assert "Acme" in message
    assert "cascade" in message
    # refused means nothing moved
    assert len(team.for_org(rw, org.id)) == 1
    assert team.list_members(rw, active_only=True)[0].name == "Dana Cruz"


def test_member_deactivate_refuses_someone_already_inactive(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_deactivate(rw, "Dana Cruz")
    with pytest.raises(ValueError) as err:
        mcpserver._member_deactivate(rw, "Dana Cruz")
    assert "already inactive" in str(err.value)


def test_member_deactivate_is_registered(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "member_deactivate" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "deactivate" 2>&1 | tail -20
```
Expected: FAIL with `AttributeError: module 'bookkit.mcpserver' has no attribute '_member_deactivate'`.

- [ ] **Step 3: Write the label helper and the tool helper**

Add after `_team_unassign` in `src/bookkit/mcpserver.py`:

```python
def _assignment_label(row: sqlite3.Row) -> str:
    """How one assignment reads in a refusal: the client, plus the placement
    ref when it is deal-level rather than account-level."""
    keys = row.keys()
    account = row["org_name"] if "org_name" in keys else None
    placement = row["placement_ref"] if "placement_ref" in keys else None
    label = account or "unscoped"
    return f"{label} ({placement})" if placement else label


def _member_deactivate(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """Retire a colleague. Refuses while they still hold assignments — a
    roster that silently keeps pointing at someone who left is worse than a
    refusal — and names every one so the caller can act."""
    from .repo import base, team

    member = _find_member(conn, name)
    if not member.active:
        raise ValueError(f"{member.name} is already inactive")
    rows = team.for_member(conn, member.id)
    if rows:
        labels = ", ".join(_assignment_label(r) for r in rows)
        raise ValueError(
            f"{member.name} is still on {len(rows)} assignments: {labels} — "
            f"unassign them first, or pass cascade=True to remove all "
            f"{len(rows)} and deactivate as one revertible batch"
        )
    with _open_batch(
        conn, tool="member_deactivate", summary=f"deactivated {member.name}",
    ) as batch:
        base.update(conn, "team_member", member.id, {"active": 0},
                    note="mcp deactivate")
        _provenance(conn, "team_member", member.id)
    return {"name": member.name, "active": False, "unassigned": 0,
            "batch": batch.ref}
```

The refusal mentions `cascade=True` before Task 4 adds it. That is deliberate: the two tasks land together in one branch, and a refusal that names no way forward is worse than one that names the next task's flag. If Task 4 is abandoned, this string must change.

- [ ] **Step 4: Register the tool**

Add after the `team_unassign` tool in `build_server`:

```python
    @server.tool()
    async def member_deactivate(name: str) -> dict[str, Any]:
        """Retire a colleague (exact name — read team_roster). Refuses while
        they still hold assignments, naming every one. They stay in the
        record and stop appearing in pickers; member_reactivate undoes it."""
        return _member_deactivate(rw, name)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "deactivate" 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 6: Run the full gates**

```bash
uv run pytest -q > $S/p.txt 2>&1 && uv run mypy src > $S/m.txt 2>&1 && uv run ruff check src tests > $S/r.txt 2>&1 && echo GATES-PASS; tail -3 $S/p.txt $S/m.txt $S/r.txt
```
Expected: `GATES-PASS`.

- [ ] **Step 7: Commit**

```bash
git add src/bookkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: member_deactivate — retires a colleague, refuses while assignments are live"
```

---

### Task 4: `member_deactivate(cascade=True)` — one revertible batch

**Files:**
- Modify: `src/bookkit/mcpserver.py` — `_member_deactivate()` signature and body; the `member_deactivate` tool wrapper
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: everything from Task 3, plus `repo.team.unassign`, `mcpserver._revert_batch`
- Produces: `_member_deactivate(conn, name: str, cascade: bool = False)` — same return shape, `unassigned` now counts removed assignments

- [ ] **Step 1: Write the failing tests**

```python
def test_member_deactivate_cascade_removes_every_assignment(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="cyber")

    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["active"] is False
    assert out["unassigned"] == 1
    assert team.for_org(rw, org.id) == []
    assert team.list_members(rw, active_only=True) == []


def test_cascade_is_ONE_batch_and_revert_restores_everything(server_db):
    """The whole point of cascade over N separate unassigns: one undo unit."""
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="cyber")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="property")

    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["unassigned"] == 2
    assert team.for_org(rw, org.id) == []

    mcpserver._revert_batch(rw, out["batch"], now="2026-08-14T04:00:00Z")
    assert len(team.for_org(rw, org.id)) == 2
    assert team.list_members(rw, active_only=True)[0].name == "Dana Cruz"


def test_cascade_on_someone_with_no_assignments_still_works(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["unassigned"] == 0
    assert out["active"] is False


def test_cascade_covers_deal_level_assignments_too(server_db):
    from bookkit.repo import placements, team

    rw, org = _rw(server_db)
    placement = placements.create(rw, org.id, program_name="Tower GL",
                                  effective_on="2026-01-01",
                                  expires_on="2027-01-01")
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", placement_ref=placement.ref)

    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["unassigned"] == 1
    assert team.for_org(rw, org.id) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "cascade" 2>&1 | tail -20
```
Expected: FAIL with `TypeError: _member_deactivate() got an unexpected keyword argument 'cascade'`.

- [ ] **Step 3: Add the cascade path**

Replace `_member_deactivate` with:

```python
def _member_deactivate(
    conn: sqlite3.Connection, name: str, cascade: bool = False
) -> dict[str, Any]:
    """Retire a colleague. Refuses while they still hold assignments — a
    roster that silently keeps pointing at someone who left is worse than a
    refusal — and names every one so the caller can act. cascade=True removes
    them and deactivates in ONE batch, so revert_batch puts it all back."""
    from .repo import base, team

    member = _find_member(conn, name)
    if not member.active:
        raise ValueError(f"{member.name} is already inactive")
    rows = team.for_member(conn, member.id)
    if rows and not cascade:
        labels = ", ".join(_assignment_label(r) for r in rows)
        raise ValueError(
            f"{member.name} is still on {len(rows)} assignments: {labels} — "
            f"unassign them first, or pass cascade=True to remove all "
            f"{len(rows)} and deactivate as one revertible batch"
        )
    summary = f"deactivated {member.name}"
    if rows:
        summary += f" and removed {len(rows)} assignments"
    # org_id stays None: a cascade spans clients, so no single org owns it.
    with _open_batch(
        conn, tool="member_deactivate", summary=summary,
    ) as batch:
        for row in rows:
            team.unassign(conn, row["id"])
        base.update(conn, "team_member", member.id, {"active": 0},
                    note="mcp deactivate")
        _provenance(conn, "team_member", member.id)
    return {"name": member.name, "active": False, "unassigned": len(rows),
            "batch": batch.ref}
```

- [ ] **Step 4: Expose `cascade` on the tool**

```python
    @server.tool()
    async def member_deactivate(
        name: str, cascade: bool = False
    ) -> dict[str, Any]:
        """Retire a colleague (exact name — read team_roster). Refuses while
        they still hold assignments, naming every one; cascade=True removes
        all of them and deactivates as ONE revertible batch. They stay in the
        record and stop appearing in pickers; member_reactivate undoes the
        deactivation, but NOT the cascaded assignments — revert_batch does
        that."""
        return _member_deactivate(rw, name, cascade=cascade)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "cascade or deactivate" 2>&1 | tail -20
```
Expected: PASS, including Task 3's refusal tests (unchanged behaviour when `cascade` is omitted).

- [ ] **Step 6: Run the full gates**

```bash
uv run pytest -q > $S/p.txt 2>&1 && uv run mypy src > $S/m.txt 2>&1 && uv run ruff check src tests > $S/r.txt 2>&1 && echo GATES-PASS; tail -3 $S/p.txt $S/m.txt $S/r.txt
```
Expected: `GATES-PASS`.

- [ ] **Step 7: Commit**

```bash
git add src/bookkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: member_deactivate cascade — unassign everywhere and retire as one undo unit"
```

---

### Task 5: `member_reactivate` and the `edit_field` redirect

**Files:**
- Modify: `src/bookkit/mcpserver.py` — new `_EDIT_REDIRECTS` map beside `_EDITABLE` (~line 1509); `_edit_field()` refusal branch; new `_member_reactivate()` after `_member_deactivate`; new `@server.tool()` wrapper
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `mcpserver._member_deactivate`, `_find_member`, `_open_batch`, `_provenance`, `repo.base.update`
- Produces: `_member_reactivate(conn, name: str) -> dict[str, Any]` returning `{"name": str, "active": True, "batch": str}`; tool `member_reactivate(name)`; `_EDIT_REDIRECTS: dict[tuple[str, str], str]`

- [ ] **Step 1: Write the failing tests**

```python
def test_member_reactivate_brings_someone_back(server_db):
    from bookkit.repo import team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_deactivate(rw, "Dana Cruz")

    out = mcpserver._member_reactivate(rw, "Dana Cruz")
    assert out["active"] is True
    assert out["batch"].startswith("MCP-")
    assert [m.name for m in team.list_members(rw, active_only=True)] == ["Dana Cruz"]


def test_member_reactivate_refuses_someone_already_active(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    with pytest.raises(ValueError) as err:
        mcpserver._member_reactivate(rw, "Dana Cruz")
    assert "already active" in str(err.value)


def test_reactivate_does_NOT_resurrect_cascaded_assignments(server_db):
    """Spec decision: revert_batch is the undo for a cascade. Half-restoring
    would be worse than saying so."""
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme")
    mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)

    mcpserver._member_reactivate(rw, "Dana Cruz")
    assert team.list_members(rw, active_only=True)[0].name == "Dana Cruz"
    assert team.for_org(rw, org.id) == []      # assignments stay gone


def test_edit_field_redirects_active_to_the_deactivate_tools(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_member", "Dana Cruz", "active", "no", expecting="yes",
        )
    message = str(err.value)
    assert "member_deactivate" in message
    assert "member_reactivate" in message


def test_member_reactivate_is_registered(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"member_deactivate", "member_reactivate"} <= names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "reactivate or redirect" 2>&1 | tail -20
```
Expected: FAIL with `AttributeError: module 'bookkit.mcpserver' has no attribute '_member_reactivate'`.

- [ ] **Step 3: Add the redirect map and wire it into `_edit_field`**

Beside `_EDITABLE`:

```python
# Fields that exist but are owned by a transition tool. The generic refusal
# only lists what IS editable; these say where the caller should go instead.
_EDIT_REDIRECTS: dict[tuple[str, str], str] = {
    ("team_member", "active"): "member_deactivate / member_reactivate",
}
```

In `_edit_field()`, replace the `vtype is None` branch:

```python
    vtype = allowed.get(field)
    if vtype is None:
        redirect = _EDIT_REDIRECTS.get((kind, field))
        if redirect is not None:
            raise ValueError(
                f"{field!r} on a {kind} is not a field edit — use {redirect}"
            )
        raise ValueError(
            f"{field!r} is not editable on a {kind}; allowed: {sorted(allowed)}"
        )
```

- [ ] **Step 4: Write `_member_reactivate`**

Add directly after `_member_deactivate`:

```python
def _member_reactivate(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """Bring a retired colleague back. Assignments a cascade removed do NOT
    come back — revert_batch is the undo for those."""
    from .repo import base

    member = _find_member(conn, name)
    if member.active:
        raise ValueError(f"{member.name} is already active")
    with _open_batch(
        conn, tool="member_reactivate", summary=f"reactivated {member.name}",
    ) as batch:
        base.update(conn, "team_member", member.id, {"active": 1},
                    note="mcp reactivate")
        _provenance(conn, "team_member", member.id)
    return {"name": member.name, "active": True, "batch": batch.ref}
```

- [ ] **Step 5: Register the tool**

Directly after the `member_deactivate` tool:

```python
    @server.tool()
    async def member_reactivate(name: str) -> dict[str, Any]:
        """Bring a retired colleague back (exact name — read team_roster).
        Assignments removed by a cascading deactivate do NOT come back; use
        revert_batch for those."""
        return _member_reactivate(rw, name)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
uv run pytest -q tests/test_mcpserver.py -k "reactivate or redirect" 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 7: Extend the tool-registration test**

In `test_write_expansion_tools_are_registered`, add the two new names to the asserted set:

```python
        "team_roster", "opportunity_stage", "task_reopen",
        "request_item_waive", "member_deactivate", "member_reactivate",
```

- [ ] **Step 8: Run the full gates**

```bash
uv run pytest -q > $S/p.txt 2>&1 && uv run mypy src > $S/m.txt 2>&1 && uv run ruff check src tests > $S/r.txt 2>&1 && echo GATES-PASS; tail -3 $S/p.txt $S/m.txt $S/r.txt
```
Expected: `GATES-PASS`. Tool count is now 41.

- [ ] **Step 9: Commit**

```bash
git add src/bookkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: member_reactivate, and edit_field points active at the transition tools"
```

---

### Task 6: Update CLAUDE.md and close the phase

**Files:**
- Modify: `CLAUDE.md` — the MCP bullet in the Architecture section
- Modify: `docs/superpowers/specs/2026-08-14-mcp-team-edits-design.md` — Status line

- [ ] **Step 1: Record the team-edit surface in CLAUDE.md**

The Architecture section documents load-bearing rules an agent must not rediscover. Add after the existing "One MCP call is ONE undo unit" bullet:

```markdown
- Team writes: assignments are corrected in place via
  `edit_field(kind="team_assignment")` over role/lines/notes — NEVER
  re-scoped (unassign+assign moves someone between clients). Retiring a
  colleague is `member_deactivate`, not a field edit; it refuses while
  assignments are live and `cascade=True` removes them all in one
  revertible batch. Renames go through edit_field behind a duplicate
  guard, because two members sharing a name makes every lookup ambiguous.
```

- [ ] **Step 2: Mark the spec implemented**

Change the spec's Status line to:

```markdown
Status: implemented (2026-08-14) — plan at
docs/superpowers/plans/2026-08-14-mcp-team-edits.md
```

- [ ] **Step 3: Run the full gates one final time**

```bash
uv run pytest -q > $S/p.txt 2>&1 && uv run mypy src > $S/m.txt 2>&1 && uv run ruff check src tests > $S/r.txt 2>&1 && echo GATES-PASS; tail -3 $S/p.txt $S/m.txt $S/r.txt
```
Expected: `GATES-PASS`.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-08-14-mcp-team-edits-design.md
git commit -m "docs: MCP team edits landed — assignment edit, rename, deactivate/reactivate"
```

---

## Notes for the executor

- **Fresh-eyes review before declaring done.** This repo's process requires it (`CLAUDE.md`, Process section). Run it after Task 5, before Task 6.
- **Worktree.** If another session is active, this phase gets its own worktree at `.claude/worktrees/mcp-team-edits`. Two sessions in one working directory has bitten this repo before.
- **If a test's helper signature does not match reality** (`placements.create`, `batches_repo.get`), fix the test to match the source. Do not reshape the source to fit a test written from memory.
- **`active` is stored as INTEGER 0/1** but `TeamMember.active` is typed `bool`. Write `0` / `1` through `base.update`; assert on the `bool` coming back off the model.
