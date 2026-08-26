/* Inline cell editing: Tab-hop and blur-commit — the two behaviours the
 * declarative half of macros/cell.html (hx-get/hx-post/hx-trigger) cannot
 * express on its own. Mirrors tui/widgets/inline_edit.py's CellEditor:
 * Enter commits and closes, Tab commits and hops to the next editable cell
 * in the same record, BLUR COMMITS, and Escape is the one discard.
 *
 * The bottom of this file also owns whole-form cancel (macros/form.html's
 * Cancel button and Escape) — a second small file for one button was worse
 * than one shared "nothing writes by surprise" file covering both the cell
 * editor and the form editor (2026-08-18).
 *
 * Enter needs no JS: it's a native form submit (the editor's <form> already
 * carries hx-post). Escape is still declarative — the cell's own
 * hx-trigger="keyup[key=='Escape']" in macros/cell.html. Tab-hop, blur-commit
 * and the Escape guard are handled entirely here, in JS, not split against a
 * declarative trigger:
 *
 *   - Tab's default action (move focus) is prevented so it never leaves via
 *     the browser's own tab order; we commit the cell instead, then open
 *     the next editable cell's editor once the commit lands.
 *   - Blur commits (2026-08-20; it cancelled until then). Every path that
 *     ends an edit — a commit's own outerHTML swap, Escape's revert — also
 *     removes the focused input and therefore fires focusout, so the
 *     listener has to know which one it is looking at. That is what the
 *     `__bkCommitting` (on the form) and `__bkCancelled` (on the cell) marks
 *     are for, and both are READ on every focusout rather than merely
 *     carried: fix round 2, 2026-08-17, found a flag that was set and
 *     cleared and never consulted, so every commit raced a spurious revert
 *     GET against its own save. They are marks on the NODES, not globals —
 *     a global flag shipped three bugs in one week (stuck on after a panel
 *     swap; cleared early by an unrelated request; and a zero-timer reset
 *     that lost the race to Escape's own revert and committed the
 *     discarded value).
 *   - An unchanged cell reverts instead of committing, so opening a cell to
 *     read it costs nothing.
 *
 * Selectors below are class-only, never tag-qualified (no "td.cell" or
 * "td.cell-editing") — macros/cell.html's `tag` parameter means a cell is a
 * <td> inside a table.rows row or a <div> inside a .contact-card, and this
 * script has to work for both without knowing which one it's looking at.
 * RECORD_SCOPE below is whatever ancestor groups one record's cells —
 * a <tr>, or a .contact-card. */
/* THE ONE EXCEPTION TO BLUR-COMMITS (program-worksheet hand-off,
 * 2026-08-24): the participation table's SHARE input previews before it
 * saves — blur posts a projection (routes/program.py market_share_preview),
 * never a write, and Save commits through the ordinary cell route. Taken
 * deliberately, because a share edit moves the one figure the worksheet
 * exists to close. It is a plain input, not a .cell, so nothing below
 * applies to it; every actual cell keeps blur-commit / Escape-discard. */
(function () {
  "use strict";

  // A RECORD is whatever groups one thing's editable cells. `.marketing-block`
  // is the third: a line of coverage's own expectations (basis, rate per,
  // exposure, what expired) are cells in the block HEADER, outside any <tr>,
  // so without it Tab in the header found no scope and the hop was dropped —
  // a key the editor advertises and does not honour. It is last on purpose:
  // `closest` walks UP, so a cell inside one of the block's rows still finds
  // its <tr> first and hops along the row, not into the header.
  var RECORD_SCOPE = "tr, .contact-card, .marketing-block";

  // { field: "<data-field>", record: "<data-layer-row>" | null } set by a Tab
  // keydown — the FIELD KEY, not the node. A node reference is detached the
  // moment a save answers with the whole record row instead of the one cell
  // (the marketing grid does that, because four of its cells feed a derived
  // Total that would otherwise sit there stale), and htmx.ajax against a
  // detached element is a request whose answer lands nowhere. A key is
  // re-resolved against whatever is on the page when the swap arrives, which
  // is correct for both shapes.
  //
  // `record` is what makes that re-resolve land on the RIGHT row when the
  // answer is bigger than the row: three marketing cells answer with the whole
  // block, because they move the premium bridge and the clearance strip above
  // them, and a block holds one `premium` cell per market — so a key alone
  // would hop into the first market's row from anywhere in the grid.
  var pendingHop = null;

  function editableCells(scope) {
    return Array.prototype.slice.call(scope.querySelectorAll(".cell[data-field]"));
  }

  // THE COMMIT MARK LIVES ON THE FORM, not in a page-global flag. A global
  // had two failure modes in one week: reset only when the completed
  // request's element was the .cell-editor (never true after a panel-
  // retargeting swap, so the flag stuck ON and blur-commit died page-wide),
  // then reset unconditionally (any unrelated request completing — another
  // cell's Escape-revert — cleared it while a commit was still in flight,
  // reopening the double-submit race it exists to prevent; fresh-eyes
  // review, 2026-08-21). A mark on the form itself has neither: form A's
  // mark is invisible to form B's traffic, and a detached form's mark
  // simply dies with the node.
  //
  // Capture phase: runs before htmx's own submit listener, so the mark is
  // set by the time htmx issues the request — and therefore by the time the
  // eventual outerHTML swap's own focusout reaches the listener below.
  document.body.addEventListener(
    "submit",
    function (evt) {
      if (evt.target && evt.target.classList && evt.target.classList.contains("cell-editor")) {
        evt.target.__bkCommitting = true;
      }
    },
    true
  );

  // Un-mark once the form's own request settles, so a later genuine blur
  // (clicking away after a NETWORK ERROR left the editor open) is not
  // suppressed forever. Only the error/no-swap path needs this: a
  // successful save and a refusal both swap in a fresh element whose mark is
  // unset by construction, and on those paths the old form is detached — its
  // afterRequest re-fires on an attached ancestor (`if(!le(r))` in
  // htmx.min.js), which this check correctly ignores, leaving the mark to
  // die with the node. Only when NOTHING swapped is `detail.elt` still the
  // form itself, and that is exactly the case that needs the reset.
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var elt = evt.detail && evt.detail.elt;
    if (!elt || !elt.classList) return;
    if (elt.classList.contains("cell-editor")) {
      elt.__bkCommitting = false;
    }
    // The Escape mark's only surviving case: a revert whose GET failed, so
    // nothing swapped and the marked cell is still attached — clear it so a
    // later blur on that editor can commit again. The revert's request
    // element is the CELL (the declarative trigger lives on it).
    if (elt.classList.contains("cell-editing")) {
      elt.__bkCancelled = false;
    }
  });

  // BLUR COMMITS (Grant, 2026-08-20). It used to cancel, which meant
  // clicking out of a cell silently threw away what had just been typed —
  // the failure people actually hit. A surprise write is at worst a visible
  // value you can edit again or take back with the undo toast; a surprise
  // DISCARD leaves nothing to take back. Escape is now the single discard,
  // and macros/cell.html says so beside the input. Enter and Tab unchanged.
  //
  // Three guards, all load-bearing:
  //   `__bkCommitting` (a mark ON the form, see the submit listener above)
  //                 — a commit's own outerHTML swap removes the focused
  //                   input, firing focusout again; without this the cell
  //                   would post twice.
  //   `__bkCancelled` (a mark ON the cell, see the Escape listener below)
  //                 — Escape's declarative revert ALSO removes the input and
  //                   fires focusout. Without this, Escape would commit the
  //                   very value it exists to discard — and DID, while this
  //                   was a global reset on a zero-timer that lost the race
  //                   against the revert's own network round trip.
  //   unchanged     — an untouched cell must not write. Opening a cell to
  //                   read it and clicking away would otherwise cost a
  //                   write-through, a rewritten towerkit file and an undo
  //                   batch per glance, with heal_follows re-seating the
  //                   tower each time.
  function currentValue(cell) {
    var input = cell.querySelector("input, select");
    return input ? input.value : null;
  }

  function revert(cell) {
    var action = cell.getAttribute("data-cell-action");
    if (!action || typeof htmx === "undefined") return;
    htmx.ajax("GET", action, { target: cell, swap: "outerHTML" });
  }

  document.body.addEventListener("focusout", function (evt) {
    var cell = evt.target.closest && evt.target.closest(".cell-editing");
    if (!cell || cell.__bkCancelled) return;
    var form = cell.querySelector("form.cell-editor");
    if (!form || form.__bkCommitting) return;
    var opened = cell.getAttribute("data-opened-with");
    if (opened !== null && currentValue(cell) === opened) {
      revert(cell); // nothing typed — close it, exactly as blur used to
      return;
    }
    form.requestSubmit();
  });

  // --- a refusal clears on the FIRST KEYSTROKE ----------------------------
  // Researched rule, not a preference: validating while typing measurably
  // RAISES error rates, so bookkit validates on blur — but the other half of
  // that finding is that a message which survives the correction makes a
  // now-valid entry read as still broken, and people stop trusting the
  // messages at all (Baymard, "premature error blindness"; NN/g on hostile
  // error messages; .claude/skills/data-entry-integrity rule 3).
  //
  // Until this listener, EVERY web refusal persisted until the next POST: the
  // red .cell-error outline and its .cell-error-msg sat under the input while
  // the fix was typed, and .form-error stayed pinned at the top of a form
  // through the whole correction. The TUI never had this problem — its
  // refusals are notify() toasts that dismiss themselves — so the two
  // surfaces disagreed about how long a "no" lasts.
  //
  // Delegated, like everything else in this file: the fragments this has to
  // cover are swapped in by htmx long after load, and an inline handler per
  // input would be a copy of this rule per template (the house rule the
  // form Cancel and the toast close below already follow).
  //
  // Scopes are walked UPWARDS, not matched once, because a refusal is not
  // always rendered inside the form that caused it: a refused named-limit add
  // re-renders the whole layer-details row with the message at the top of the
  // <td>, OUTSIDE the .market-add-form whose input is being corrected
  // (_layer_details.html). Clearing only the nearest scope would leave that
  // one on screen — the exact failure this listener exists to end.
  var ERROR_SCOPE = ".cell-editing, .entity-form, .market-add-form, .marketing-line-add, .marketing-assign, .layer-details";

  function clearError(scope) {
    scope.classList.remove("cell-error");
    var msgs = scope.querySelectorAll(".cell-error-msg, .form-error");
    for (var i = 0; i < msgs.length; i++) msgs[i].remove();
  }

  document.body.addEventListener("input", function (evt) {
    if (!evt.target.closest) return;
    var scope = evt.target.closest(ERROR_SCOPE);
    while (scope) {
      clearError(scope);
      scope = scope.parentElement && scope.parentElement.closest(ERROR_SCOPE);
    }
  });

  // --- shift+Enter: commit and hop DOWN THE COLUMN ------------------------
  //
  // A QUOTE LETTER FILLS A ROW; A MARKETING ROUND FILLS A COLUMN. Tab walks
  // along the row, which is right for one market's answer arriving all at
  // once — but chasing four markets means four reply dates and then four
  // premiums, and there was no way down a column but the mouse, one cell at a
  // time (Grant, 2026-08-26: scrolling and updating the grid is clunky).
  //
  // shift+Enter, NOT an arrow key. Down-arrow already means something in both
  // controls this editor renders — it moves the caret in a text input and it
  // changes the selected option in a `<select>` — and the status column is
  // precisely a column somebody wants to fill down. Enter commits and closes,
  // shift+Enter commits and carries on; neither does anything native in a
  // one-line input or a select, so nothing is being taken away.
  //
  // IT REUSES `pendingHop` WHOLE. That mechanism already resolves by
  // { field, record } against whatever markup the answer brought back, which
  // is what makes it survive a save that answers with the entire block — so a
  // vertical hop is the same object with the SAME field key and a DIFFERENT
  // record, and needs no second path through the swap handler.
  function siblingRecord(cell, forward) {
    var row = cell.closest("[data-layer-row]");
    if (!row) return null;
    // SCOPED TO ONE TABLE, never `document`. `data-layer-row` is the record
    // hook on the layers panel too, and the Program tab renders a marketing
    // block per line of coverage AND a placement per account — so a global
    // query would hop out of General Liability into Auto, or out of the
    // marketing grid into the tower's layers, on the last row of a column.
    var table = row.closest("table");
    if (!table) return null;
    var key = CSS.escape(cell.getAttribute("data-field"));
    // Only rows that actually HAVE this column as a cell. The add row and the
    // "no markets approached" row are single colspan cells with nothing to
    // edit, and hopping into one would land the caret nowhere while the
    // commit's own focus had already gone.
    var rows = Array.prototype.slice
      .call(table.querySelectorAll("[data-layer-row]"))
      .filter(function (r) {
        return r.querySelector('.cell[data-field="' + key + '"]');
      });
    var idx = rows.indexOf(row);
    if (idx < 0) return null;
    var next = rows[idx + (forward ? 1 : -1)];
    return next ? next.getAttribute("data-layer-row") : null;
  }

  document.body.addEventListener("keydown", function (evt) {
    if (evt.key !== "Enter" || !evt.shiftKey) return;
    var form = evt.target.closest && evt.target.closest("form.cell-editor");
    if (!form) return;
    var cell = form.closest(".cell-editing");
    if (!cell) return;
    var field = cell.getAttribute("data-field");
    var record = siblingRecord(cell, true);
    // Always prevent the default: whether or not there is a row below, this
    // commits rather than letting the browser submit and then wonder where
    // the caret went — the same reasoning Tab's own handler gives.
    evt.preventDefault();
    pendingHop = record ? { field: field, record: record } : null;
    form.requestSubmit();
  });

  document.body.addEventListener("keydown", function (evt) {
    if (evt.key !== "Tab") return;
    var form = evt.target.closest && evt.target.closest("form.cell-editor");
    if (!form) return;
    var cell = form.closest(".cell-editing");
    var scope = cell && cell.closest(RECORD_SCOPE);
    if (!scope) return;
    var cells = editableCells(scope);
    var idx = cells.indexOf(cell);
    var next = cells[idx + (evt.shiftKey ? -1 : 1)];
    // Always prevent the default focus-jump: whether or not there's a next
    // cell, Tab commits this one rather than abandoning the browser to its
    // own tab order mid-edit.
    evt.preventDefault();
    pendingHop = next
      ? {
          field: next.getAttribute("data-field"),
          record: scope.getAttribute("data-layer-row"),
        }
      : null;
    form.requestSubmit();
  });

  // Escape is the discard. It marks THE CELL as cancelled before the cell's
  // own declarative hx-trigger="keyup[key=='Escape']" revert fires, so the
  // focusout that revert causes cannot be read as a commit. keydown, not
  // keyup, for exactly that ordering.
  //
  // A MARK ON THE NODE, NOT A GLOBAL WITH A TIMER. This was
  // `cancelling = true` with a setTimeout(0) reset, and the timer LOST THE
  // RACE it existed to win: the revert is a network GET, so its swap — and
  // the focusout that swap fires — arrives long after timeout zero. By then
  // the flag was false, the value differed from data-opened-with, and the
  // focusout handler committed THE VERY VALUE ESCAPE DISCARDED. Driven with
  // real keys against a live app: the discarded text landed in the database
  // with an event-log row (2026-08-21). Every earlier check had exercised
  // blur-commit, never Escape-then-watch-the-DB.
  //
  // The mark needs no reset at all on the ordinary path: the revert's swap
  // replaces the whole cell, and the new cell arrives unmarked. Only a
  // revert that FAILS (network error, nothing swapped) leaves a marked cell
  // behind, and the afterRequest listener below clears exactly that case —
  // the same shape as the form's own commit mark, for the same reasons.
  document.body.addEventListener("keydown", function (evt) {
    if (evt.key !== "Escape") return;
    pendingHop = null;
    var cell = evt.target.closest && evt.target.closest(".cell-editing");
    if (cell) cell.__bkCancelled = true;
  });

  // Put the caret back on the cell a panel-wide swap replaced. Best effort
  // by design: a structure write (statutory, follows, applies-to) closes the
  // details row its control lived in, so there is nothing to return to, and
  // doing nothing is the right answer rather than guessing at another cell.
  // The saved signal, in one place because two call sites raise it — see the
  // comment at the swap handler for why there are two.
  function flashSaved(cell) {
    cell.classList.add("cell-saved");
    window.setTimeout(function () {
      cell.classList.remove("cell-saved");
    }, 1200);
  }

  function refocus(section, token, wrote) {
    var parts = token.split(":");
    var scope = parts[0];
    var field = parts.slice(1).join(":");
    var row =
      scope === "cell"
        ? section
        : section.querySelector('[data-layer-row="' + CSS.escape(scope) + '"]');
    if (!row) return;
    var cell = row.querySelector('.cell[data-field="' + CSS.escape(field) + '"]');
    if (cell) {
      cell.focus();
      if (wrote) flashSaved(cell);
    }
  }

  // A commit's outerHTML swap replaces the editing cell with a plain
  // display cell on success, or a fresh (still cell-editing) one carrying
  // the refusal on failure. Only the success case advances the hop — the
  // TUI's CellEditor.action_commit_next has the same guard (`if
  // self._table._commit(...)`) before it hops.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var el = evt.target;
    if (!el || !el.classList) return;
    // WAS THIS SWAP A WRITE? Every revert — Escape's discard, the unchanged-
    // value blur-close — is a GET that returns the *identical* display-cell
    // markup a successful save does, so the swapped element alone cannot say
    // whether anything was written. The verb can: every save is a POST and
    // every revert is a GET. Without this gate the saved flash fired on
    // Escape, congratulating the user on a write that never happened — found
    // by fresh-eyes review driving the discard path in a live app
    // (2026-08-21); the original browser check had only driven the save path.
    var wrote =
      evt.detail &&
      evt.detail.requestConfig &&
      evt.detail.requestConfig.verb === "post";

    // Any newly-swapped editor gets focus — covers opening a cell fresh,
    // re-showing it after a refusal, and the hop's own second swap below.
    // Doing this here (rather than relying on the `autofocus` attribute)
    // is what makes focus reliable for content inserted after page load.
    if (el.classList.contains("cell-editing")) {
      var input = el.querySelector("input, select");
      // Remember what the cell held when it opened, so blur can tell "the
      // user typed something" from "the user looked and left".
      el.setAttribute("data-opened-with", input ? input.value : "");
      if (input) input.focus();
      return;
    }

    // A SAVE HAS TO LOOK LIKE SOMETHING (Grant, 2026-08-21: "unclear when
    // changes are saved as it just stays blue"). A committed cell swaps back
    // to a display cell that is, by design, identical to the one that was
    // there before the edit — so a save that changed nothing visible, or
    // changed a value the eye was already on, gave no sign it had happened.
    //
    // The flash is on the DISPLAY cell only. A refusal comes back still
    // `.cell-editing` (commit-in-place) and is handled above, so this cannot
    // congratulate the user on a write that was refused.
    //
    // TWO PLACES, because there are two shapes of successful save and only one
    // of them swaps a cell. A plain cell save swaps the cell itself and is
    // caught here; a program write answers with the WHOLE section, so the
    // swapped element is a <section> and there is no cell to flash — that one
    // is caught in `refocus` below, on the cell the caret is being put back
    // into. Flashing only here looked right and silently did nothing on the
    // Program tab, which is the surface the report came from (verified in a
    // browser, 2026-08-21).
    if (wrote && el.classList.contains("cell") && el.hasAttribute("data-field")) {
      flashSaved(el);
    }

    // A write answers with the WHOLE program section (routes/program.py
    // `_panel` retargets onto it), so the cell the caret was in no longer
    // exists. data-refocus names its replacement — "<layer_id>:<field>", or
    // "cell:<field>" for a placement header cell — and putting focus back is
    // what keeps a run of edits down one column from becoming a run of
    // hunting for where the row went.
    if (el.hasAttribute && el.hasAttribute("data-refocus")) {
      // data-refocus only rides write responses today, but the flash inside
      // refocus() is gated on the verb anyway — one rule, both call sites,
      // so a future GET that grows the attribute cannot resurrect the bug.
      refocus(el, el.getAttribute("data-refocus"), wrote);
    }

    if (!pendingHop) return;
    var hop = pendingHop;
    pendingHop = null;
    // WHAT LANDED IS EITHER THE CELL OR THE RECORD IT BELONGS TO. A plain cell
    // save swaps the cell and the record scope is its ancestor; a save whose
    // write moved a value in a SIBLING cell has to answer with the whole row
    // (routes/marketing.py `_row_response`, because Total is the sum of four
    // typed cells) and the swapped element IS the scope. Anything else — a
    // refusal, an unrelated request completing — matches neither and the hop
    // is dropped, which is the same guard `hop.scope.contains(el)` was.
    var scope = null;
    if (el.classList.contains("cell")) {
      scope = el.closest(RECORD_SCOPE);
    } else if (el.matches && el.matches(RECORD_SCOPE)) {
      scope = el;
    }
    if (!scope) return;
    // NARROW TO THE RECORD THE HOP STARTED IN. When the answer is the record
    // itself the two are the same element; when it is the whole block, the
    // record is one row inside it and the same `data-layer-row` the refocus
    // token resolves against finds it.
    var within = scope;
    if (hop.record && scope.getAttribute("data-layer-row") !== hop.record) {
      within =
        scope.querySelector('[data-layer-row="' + CSS.escape(hop.record) + '"]') ||
        // A VERTICAL HOP LEAVES THE SCOPE THAT ANSWERED, and the fallback to
        // `scope` is wrong for it. Tab hops WITHIN a record, so the record
        // that answered always contains the target; shift+Enter hops to the
        // NEXT record, and a cell that answers with its own `<tr>` hands back
        // an element that cannot contain it — so the old fallback re-opened
        // the cell that had just been saved, and the key read as doing
        // nothing at all. Eight of the marketing grid's cells answer that way;
        // only the four block-sized ones happened to work (2026-08-26).
        //
        // The document is the right place to look: `data-layer-row` holds a
        // ULID, so the lookup cannot land on the wrong record, and the row
        // being hopped INTO is by definition somewhere other than the
        // fragment that came back.
        document.querySelector('[data-layer-row="' + CSS.escape(hop.record) + '"]') ||
        scope;
    }
    var next = within.querySelector('.cell[data-field="' + CSS.escape(hop.field) + '"]');
    if (!next) return;
    var action = next.getAttribute("data-cell-action");
    if (!action || typeof htmx === "undefined") return;
    htmx.ajax("GET", action + "/edit", { target: next, swap: "outerHTML" });
  });

  // --- whole-form cancel (macros/form.html) -------------------------------
  // Every add/edit form (+ Add task, + Add contact, + Add request, + Add
  // item, and every field edit that isn't an inline cell) opened with a
  // Save button and nothing else to back out with — no Cancel, no Escape.
  // You either saved a record you did not want or navigated away and lost
  // your place. Both close the form the same way Save's own success swap
  // already does: clear .form-host, the exact target/swap the form's
  // hx-target/hx-swap use (macros/form.html). Neither ever submits or
  // posts anything, so a refused save (which re-renders the form with the
  // typed values intact, commit-in-place) still closes cleanly on Cancel —
  // nothing is retyped, nothing is written.
  function closeForm(withinForm) {
    var host = withinForm.closest && withinForm.closest(".form-host");
    if (host) host.innerHTML = "";
  }

  document.body.addEventListener("click", function (evt) {
    var btn = evt.target.closest && evt.target.closest("[data-form-cancel]");
    if (!btn) return;
    closeForm(btn);
  });

  // --- toast dismiss (account/page.html) ----------------------------------
  // The revert toast never auto-dismisses on a timer: a message saying a
  // write was REFUSED must not vanish while it is being read. That makes its
  // close button the only way out of it, so it is a real button with a
  // delegated listener here — the same "no inline onclick, no second script"
  // rule the form Cancel above follows.
  document.body.addEventListener("click", function (evt) {
    var btn = evt.target.closest && evt.target.closest("[data-toast-close]");
    if (!btn) return;
    var toast = btn.closest(".toast");
    if (toast) toast.remove();
  });

  // Matches the cell editor's own Escape-cancels rule (macros/cell.html's
  // hx-trigger="keyup[key=='Escape']") — same key, same "nothing writes by
  // surprise" rule from the visual-direction spec, applied to the whole
  // form instead of one cell. Scoped to .entity-form so it never fires for
  // an Escape typed inside an unrelated cell editor elsewhere on the page.
  //
  // The contact-removal confirm (_contact_confirm_remove.html) deliberately
  // does NOT join this selector: after its htmx swap, focus stays on the
  // "Remove" trigger in .contact-actions, which is OUTSIDE the swapped
  // fragment, so evt.target.closest would never match and the key would be
  // advertised-but-dead — the failure mode tests/test_dead_keys.py exists to
  // stop on the other surface. Its Cancel button is the way out.
  document.body.addEventListener("keydown", function (evt) {
    if (evt.key !== "Escape") return;
    var form = evt.target.closest && evt.target.closest(".entity-form");
    if (!form) return;
    closeForm(form);
  });
})();
