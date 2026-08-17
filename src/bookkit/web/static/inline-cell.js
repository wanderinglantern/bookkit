/* Inline cell editing: Tab-hop and blur-cancel — the two behaviours the
 * declarative half of macros/cell.html (hx-get/hx-post/hx-trigger) cannot
 * express on its own. Mirrors tui/widgets/inline_edit.py's CellEditor:
 * Enter commits and closes, Tab commits and hops to the next editable cell
 * in the same record, Escape and blur both cancel — never a surprise write.
 *
 * Enter needs no JS: it's a native form submit (the editor's <form> already
 * carries hx-post). Escape is still declarative — the cell's own
 * hx-trigger="keyup[key=='Escape']" in macros/cell.html. Both Tab-hop and
 * blur-cancel are handled entirely here, in JS, not split against a
 * declarative trigger:
 *
 *   - Tab's default action (move focus) is prevented so it never leaves via
 *     the browser's own tab order; we commit the cell instead, then open
 *     the next editable cell's editor once the commit lands.
 *   - Blur cancels by re-fetching the display cell — but a COMMIT's own
 *     outerHTML swap removes the focused input from the DOM too, which
 *     ALSO fires focusout. Fix round 2, 2026-08-17: this used to be a
 *     declarative hx-trigger="focusout" on the cell, running unconditionally
 *     alongside a `committing` flag that was set and cleared but never
 *     actually read anywhere — so every commit raced a spurious revert GET
 *     against its own save (harmless by luck here, since both requests
 *     converge on the same display value, but a real bug: the flag existed
 *     to prevent exactly this and did not). focusout is now handled here in
 *     JS, where `committing` actually gates the revert fetch — the flag is
 *     read on every focusout, not just carried.
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

  // Blur cancels — never a surprise write. `committing` is what stops a
  // commit's own removal-triggered focusout from firing a second, spurious
  // revert request right behind its own save.
  document.body.addEventListener("focusout", function (evt) {
    var cell = evt.target.closest && evt.target.closest(".cell-editing");
    if (!cell || committing) return;
    var action = cell.getAttribute("data-cell-action");
    if (!action || typeof htmx === "undefined") return;
    htmx.ajax("GET", action, { target: cell, swap: "outerHTML" });
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

  document.body.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape") pendingHop = null;
  });

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
      if (input) input.focus();
      return;
    }

    if (!pendingHop) return;
    var hop = pendingHop;
    pendingHop = null;
    if (!el.classList.contains("cell") || !hop.scope.contains(el)) return; // wrong swap, or a refusal
    var action = hop.nextCell.getAttribute("data-cell-action");
    if (!action || typeof htmx === "undefined") return;
    htmx.ajax("GET", action + "/edit", { target: hop.nextCell, swap: "outerHTML" });
  });
})();
