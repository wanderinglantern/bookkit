# `./bookctl web` should do the maintenance — Grant, 2026-08-21

> "I appreciate having the ability to use these CLI commands, but the launcher
> should really do much of the maintenance for me when launching to web"

He is right, and today is the argument for it. Two outages in one afternoon,
both fixable in one command, both presenting as something else:

1. **A towerkit/bookkit skew.** bookkit named `Layer.policy_group`; the
   towerkit beside it did not have it yet. Result: `AttributeError` inside a
   route, a 500, and a chevron that looked simply dead. The fix was
   `git pull` in towerkit and `uv sync`.
2. **A moved folder.** `render.theme` resolved against the working directory,
   so `themes/marsh.json` stopped loading — and because every program write
   re-validates the file, that WEDGED the program: no write to it would
   succeed, including the write that would have changed the theme.

Neither is a bug a user should have to diagnose from a traceback.

## What the launcher should check, and what it should do about it

Order matters: cheapest and most diagnostic first, and NOTHING silently
destructive.

1. **towerkit capability check — REFUSE TO START, name the fix.**
   `web/parity.py` already introspects towerkit at runtime; the suite goes red
   when towerkit grows a field bookkit has not covered. The launcher needs the
   mirror of that: does the installed towerkit have every field/op bookkit
   NAMES? A missing one is not recoverable at runtime and must not become a
   500 twenty minutes later.

   Say it plainly and give the command that WORKS WHERE HE RUNS IT:

       bookkit needs towerkit features this install does not have:
         Layer.policy_group, Layer.auditable
       update it:  cd ../towerkit && git pull && cd - && ./install.sh

   `./install.sh`, NOT `uv sync` (Grant, 2026-08-21: "the UV command fails at
   work — believe that is why we started using the installer script and
   wheelhouse files"). install.sh is PyPI-first honouring the corporate proxy,
   with the pinned wheelhouse as fallback, and it picks ../towerkit up as a
   live checkout — which is the step that actually clears a skew. Its own
   header already says "Re-run after every `git pull`", and that is the step
   that gets missed.

   Do NOT auto-pull, and do NOT run install.sh. The pull is a network write
   against a repo he may have local work in; install.sh DELETES AND REBUILDS
   .venv, which is not something to do under a running app without asking.
   Both are exactly the class of action CLAUDE.md says to confirm.

   Detecting WHICH of the two is wrong is cheap and worth doing, because both
   resolve to the same instruction: bookkit's git HEAD moved without .venv
   being rebuilt, or ../towerkit is ahead of the towerkit that is installed.

2. **Migrations — run them, say so.** Additive-only so far, and the launcher
   already has to open the DB. Print what ran.

3. **Program roots and themes — WARN, do not fix.** A `program_path` that no
   longer resolves, and a `render.theme` no theme answers to, are both data
   questions with a person's judgement in them. Name the placement, name the
   path tried. (The theme half is much less likely now — towerkit resolves by
   name as of today — but the root half is exactly what moved yesterday.)

4. **An install that is older than the checkout.** The commonest failure and
   the one that caused today: `git pull` without re-running `./install.sh`.
   Compare the installed distribution's metadata against the checkout's git
   HEAD and say so. Print the command; never run it.

## The shape

A `bookkit doctor` that both `./bookctl web` and `./bookctl` (TUI) call on the
way up, so the terminal gets the same protection. `web` should refuse to serve
on a category-1 failure and print the rest as warnings above the URL.

Everything it reports must name the command that fixes it. A checker that says
"towerkit is out of date" and stops has moved the diagnosis, not removed it.

## Why this is worth doing before more features

It converts the failure mode that cost today from "a control is mysteriously
dead, send the traceback" into "the launcher told me what to run". Grant is the
only user; he is also the person who cannot afford to be debugging a web app
between client calls.

## Related

- The details-row fix (b589b92) makes a route-level failure legible AFTER
  launch. This is the half that catches it BEFORE.
- `web/parity.py::TOWERKIT_MODEL_FIELDS` / `TOWERKIT_EDIT_OPS` are the existing
  runtime introspection to build the capability check on — do not write a
  second list.
