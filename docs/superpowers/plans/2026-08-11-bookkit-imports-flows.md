# bookkit imports flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Executed in-session immediately after authoring; interfaces are pinned here, code lives in the commits.

**Goal:** The remaining three import flows (program-from-schedule, contact/interaction capture, renewal updates) plus the TUI surfaces that let imports commit.

**Architecture:** Same pipeline as the core plan; each flow adds a mapper (`imports/mappers/`), a committer function (`imports/commit.py`), and a thin TUI surface. Program/renewal commits follow the platform write-through order: towerkit file first (validated), then DB, via existing `sync` machinery (`project`, `renew`, `update_layer`). Carrier names unknown to the market aliases ride the existing post-projection carrier-suggestion flow rather than a new one.

**Global constraints:** as the core plan (branch `imports`, no SQL in imports/, parsers only via money/dates/normalize/towerkit, mypy strict outside tui/, ruff clean).

### Task A: contact/interaction paste capture

- `imports/mappers/contact_paste.py`: `stage_contact_paste(conn, text, org_id, org_name) -> StagedImport`. Signature parsing: email via regex anywhere in text (`clean_email`), phone via line containing 7+ digits (`clean_phone`), LinkedIn via URL (`clean_linkedin`), name = first non-empty line unless it parses as email/phone (then warn), title = line after the name when it isn't contact data. Stages one `contact` record (match by email → update) and one `interaction` record (kind "interaction", type "note", subject "pasted capture", body = full text, `fields["org_id"] = org_id`).
- `commit_contact_paste(conn, staged, org_id, db_path)` in commit.py: same gate/backup/transaction shape; contact via repo.contacts, interaction via `repo.interactions.log` (check signature at src/bookkit/repo/interactions.py:12 when implementing).
- Tests: signature paste with all fields; email-only; match-existing→update; garbage → name warning, still commits as note-only interaction.

### Task B: program-from-schedule

- `imports/mappers/program_paste.py`: `stage_program(conn, source: str | list[dict], org_name, program_name, period_from, period_to) -> tuple[StagedImport, DraftProgram]`. Text → `towerkit.ingest.parse_tower`; rows → `program_from_rows`. Draft gets insured=org_name, program=program_name, and the placement's period when the paste carries none. Draft error diagnostics → ERROR issues on the one `program` record; warnings → warnings. Each unique carrier stages a `carrier` record: `aliases.resolve` hit → action "update" (info), miss → warning "unknown — will surface in carrier suggestions after projection".
- `commit_program(conn, staged, draft, placement_id, dest, db_path) -> tuple[Path | None, Diagnostics]` in commit.py: gate + snapshot, refuse when placement already linked or dest exists (mirror `sync.scaffold_program` checks), `draft.to_program()` → `dump_program` → `links.confirm(source="import")` → `sync.project(placement_id=...)`. File write happens before any DB row, per write-through order.
- Tests: paste → staged carriers flagged; commit writes file, links, projects (placement gains program_path, layers visible via `sync.layer_details`); dirty draft refuses; existing link refuses.

### Task C: renewal updates

- `imports/mappers/renewal_paste.py`: `stage_renewal(conn, placement_id, text) -> StagedImport`. Parse paste via `parse_tower`; load the CURRENT linked program; diff by layer name (case-insensitive): premium/limit/attach deltas stage `layer` records (action "update", fields old_/new_ cents); paste layers with no counterpart → warning record ("new layer — build it in towerkit"); program layers absent from the paste → warning ("unchanged"). Unlinked placement → single ERROR record.
- `commit_renewal(conn, staged, placement_id, db_path) -> tuple[str | None, Diagnostics]`: gate + snapshot, `sync.renew(placement_id)` (clone-at-birth machinery), then per staged layer delta find the renewed placement's layer id by name via `sync.layer_details` and apply `sync.update_layer(..., premium_cents/limit_cents/attach_cents)`. Returns the new placement id.
- Tests: seed a linked program (via `sync.scaffold_program` + `add_layer`), paste new premiums → diff staged; commit creates next-period placement whose file carries the new premium; unlinked placement stages an error.

### Task D: TUI surfaces + pilot test

- `tui/screens/import_screen.py`: `ImportScreen` (book file import: path Input → dry-run staging report in a scrollable Static → `c` commits via `commit_book`, gated on `staged.ok`) opened with `i` from TodayScreen.
- `tui/widgets/paste_import.py`: `PasteImportModal(title, stage, commit)` — generic TextArea + preview + commit, used by AccountScreen actions: program paste (on a selected linked-less placement), renewal paste (on a selected linked placement), contact paste (current org). Keys on AccountScreen: reuse its existing action-menu conventions; check its BINDINGS when implementing.
- Pilot tests: book import screen end-to-end on a temp DB + template file; contact paste modal commit on the account screen.

### Deviation notes
- Carrier aliasing at import time is read-only (resolve + warn); creating aliases stays in the existing markets-tab flow the projection already feeds.
- Renewal participant changes are reported, not auto-applied — binding markets onto layers is deliberate book-event work in the existing flows.
