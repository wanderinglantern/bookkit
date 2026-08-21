# Rename a suggested market, cascade it, and re-run the match — Grant, 2026-08-21

> "love how markets are suggested in the markets tab as they are found from
> programs; however, need ability to edit the name of the market which cascades
> updates while fixing and retrigger a match search after updating in line
> during that process"

Logged, not built.

## What exists now

`routes/markets.py` has an UNLINKED panel (`markets/_unlinked_panel.html`):
carrier names found in towerkit program files that answer to no market org in
the book. That discovery is the part he likes and it stays.

What it offers today is LINKING — point this spelling at an existing market —
and `repo/aliases.py` records the spelling so the same carrier resolves next
time. What it does not offer is fixing the NAME ITSELF when the name in the
program file is simply wrong (a typo, a stale legal entity), which is a
different act with different consequences.

## The three parts, and the order they have to happen in

**1. Edit the name.** Inline, on the row where it is read — the cell contract
every other surface uses. The value being edited is a carrier string INSIDE a
towerkit program file, so the write is `sync`/`program_files.write`, not a repo
update: one batch, a pre-image, revertible, and towerkit's validator gets its
say. This is NOT `repo/orgs.update` — the market org may not exist yet, which
is the whole reason the row is in the unlinked panel.

**2. Cascade it.** One misspelling is usually in more than one file, and
sometimes on more than one layer of one file. The edit has to offer to fix
every occurrence, and SAY how many before it does — the count is the
consequence, and a confirm without it is not a confirm. `repo/aliases.py` and
`services/merge.py::merge_markets` are the precedents for "everything pointing
at the loser must move".

**3. Re-run the match, in place.** After the rename, the corrected spelling
very likely now matches a real market — that is the point of fixing it. The row
should re-run the same matcher and come back showing what it found, without a
page reload, so the sequence is: fix the spelling → see the match appear →
accept the link. RapidFuzz already backs the matching (CLAUDE.md's standing
rule on parsing libraries); reuse whatever `markets.py` already calls rather
than writing a second matcher.

## Questions for Grant before building

1. **Does the rename write to the program FILE, or only to the book's alias
   table?** These are opposite answers. Writing the file corrects the source
   and every future projection; writing an alias leaves the file wrong and
   teaches bookkit to cope. I think he means the file — "cascades updates"
   reads like the files — but a file write across several programs at once is a
   bulk write and needs the backup story stated.
2. **Cascade across the whole BOOK, or just this account?** A carrier
   misspelling is book-wide by nature, but a bulk write across every client's
   program files is the largest write this app would have. `db.BLAST_CAP` is
   250 entities per batch and would apply.
3. **What happens when the corrected name matches nothing?** Leave it unlinked
   and say so, presumably — but that is the state he is already in, so the row
   must not silently look "done".

## Data-entry rules that bind this

- The rename cell is FREE TEXT with completion from existing market names
  (`repo/vocab`), not a picker: a carrier the book has never heard of is
  exactly what this panel exists to capture.
- The cascade confirm shows the count and the files. Never pre-fill a
  destination the user has not seen.
- A refusal names the fix.
