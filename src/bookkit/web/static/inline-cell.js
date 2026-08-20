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
 *     listener has to know which one it is looking at. That is what
 *     `committing` and `cancelling` are for, and both are READ on every
 *     focusout rather than merely carried: fix round 2, 2026-08-17, found a
 *     `committing` flag that was set and cleared and never consulted, so
 *     every commit raced a spurious revert GET against its own save.
 *   - An unchanged cell reverts instead of committing, so opening a cell to
 *     read it costs nothing.
 *
 * Selectors below are class-only, never tag-qualified (no "td.cell" or
 * "td.cell-editing") — macros/cell.html's `tag` parameter means a cell is a
 * <td> inside a table.rows row or a <div> inside a .contact-card, and this
 * script has to work for both without knowing which one it's looking at.
 * RECORD_SCOPE below is whatever ancestor groups one record's cells —
 * a <tr>, or a .contact-card. */
(function () {
  "use strict";

  var RECORD_SCOPE = "tr, .contact-card";

  var committing = false;
  var cancelling = false;
  var pendingHop = null; // { scope: <element>, nextCell: <element> } set by a Tab keydown

  function editableCells(scope) {
    return Array.prototype.slice.call(scope.querySelectorAll(".cell[data-field]"));
  }

  // Capture phase: runs before htmx's own submit listener, so `committing`
  // is already true by the time htmx issues the request — and therefore
  // already true by the time the eventual outerHTML swap's own focusout
  // reaches the listener below.
  document.body.addEventListener(
    "submit",
    function (evt) {
      if (evt.target && evt.target.classList && evt.target.classList.contains("cell-editor")) {
        committing = true;
      }
    },
    true
  );

  // Reset once the request/swap/settle cycle for that submit is over, so a
  // later genuine blur (e.g. clicking away after a refused save left the
  // editor open) is not permanently suppressed.
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    if (
      evt.detail &&
      evt.detail.elt &&
      evt.detail.elt.classList &&
      evt.detail.elt.classList.contains("cell-editor")
    ) {
      committing = false;
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
  //   `committing`  — a commit's own outerHTML swap removes the focused
  //                   input, firing focusout again; without this the cell
  //                   would post twice.
  //   `cancelling`  — Escape's declarative revert ALSO removes the input and
  //                   fires focusout. Without this, Escape would commit the
  //                   very value it exists to discard.
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
    if (!cell || committing || cancelling) return;
    var form = cell.querySelector("form.cell-editor");
    if (!form) return;
    var opened = cell.getAttribute("data-opened-with");
    if (opened !== null && currentValue(cell) === opened) {
      revert(cell); // nothing typed — close it, exactly as blur used to
      return;
    }
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
    pendingHop = next ? { scope: scope, nextCell: next } : null;
    form.requestSubmit();
  });

  // Escape is the discard. It sets `cancelling` BEFORE the cell's own
  // declarative hx-trigger="keyup[key=='Escape']" revert fires, so the
  // focusout that revert causes cannot be read as a commit. keydown, not
  // keyup, for exactly that ordering.
  document.body.addEventListener("keydown", function (evt) {
    if (evt.key !== "Escape") return;
    pendingHop = null;
    if (evt.target.closest && evt.target.closest(".cell-editing")) {
      cancelling = true;
      window.setTimeout(function () {
        cancelling = false;
      }, 0);
    }
  });

  // Put the caret back on the cell a panel-wide swap replaced. Best effort
  // by design: a structure write (statutory, follows, applies-to) closes the
  // details row its control lived in, so there is nothing to return to, and
  // doing nothing is the right answer rather than guessing at another cell.
  function refocus(section, token) {
    var parts = token.split(":");
    var scope = parts[0];
    var field = parts.slice(1).join(":");
    var row =
      scope === "cell"
        ? section
        : section.querySelector('[data-layer-row="' + CSS.escape(scope) + '"]');
    if (!row) return;
    var cell = row.querySelector('.cell[data-field="' + CSS.escape(field) + '"]');
    if (cell) cell.focus();
  }

  // A commit's outerHTML swap replaces the editing cell with a plain
  // display cell on success, or a fresh (still cell-editing) one carrying
  // the refusal on failure. Only the success case advances the hop — the
  // TUI's CellEditor.action_commit_next has the same guard (`if
  // self._table._commit(...)`) before it hops.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var el = evt.target;
    if (!el || !el.classList) return;

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

    // A write answers with the WHOLE program section (routes/program.py
    // `_panel` retargets onto it), so the cell the caret was in no longer
    // exists. data-refocus names its replacement — "<layer_id>:<field>", or
    // "cell:<field>" for a placement header cell — and putting focus back is
    // what keeps a run of edits down one column from becoming a run of
    // hunting for where the row went.
    if (el.hasAttribute && el.hasAttribute("data-refocus")) {
      refocus(el, el.getAttribute("data-refocus"));
    }

    if (!pendingHop) return;
    var hop = pendingHop;
    pendingHop = null;
    if (!el.classList.contains("cell") || !hop.scope.contains(el)) return; // wrong swap, or a refusal
    var action = hop.nextCell.getAttribute("data-cell-action");
    if (!action || typeof htmx === "undefined") return;
    htmx.ajax("GET", action + "/edit", { target: hop.nextCell, swap: "outerHTML" });
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
