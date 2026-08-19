/* Form-host hygiene + row toggles — the section-level counterpart to
 * inline-cell.js (which owns the cell editor's behaviours).
 *
 * ONE OPEN EDITOR PER SECTION (review finding F2, 2026-08-19): nothing used
 * to close an open form when another opened, so a market editor left open
 * plus "+ New program" stacked two live forms with the first stranded
 * mid-page. When a .form-host receives content, every OTHER .form-host
 * whose inputs all still equal their defaults is emptied. One that carries
 * TYPED input is left alone — commit-in-place means typed work is never
 * discarded by a click somewhere else; at worst two forms coexist because
 * both hold real input, which is the honest outcome.
 *
 * Also here, because they are the same "no inline onclick, no second small
 * file" rule inline-cell.js records:
 *   - [data-row-close]  removes its own row (the layer-details expander).
 *   - [data-row-expand] toggles: when the next row is already the details
 *     row, remove it and swallow the click BEFORE htmx's own element
 *     listener can fire the fetch (capture phase), so the chevron closes
 *     what it opened instead of stacking duplicates. */
(function () {
  "use strict";

  function untouched(host) {
    var controls = host.querySelectorAll("input, textarea, select");
    for (var i = 0; i < controls.length; i++) {
      var el = controls[i];
      if (el.tagName === "SELECT") {
        var opt = el.querySelector("option[selected]");
        var def = opt ? opt.value : (el.options[0] ? el.options[0].value : "");
        if (el.value !== def) return false;
      } else if (el.value !== el.defaultValue) {
        return false;
      }
    }
    return true;
  }

  document.body.addEventListener("htmx:beforeSwap", function (evt) {
    var target = evt.detail && evt.detail.target;
    if (!target || !target.classList || !target.classList.contains("form-host")) return;
    if (!evt.detail.serverResponse || !evt.detail.serverResponse.trim()) return;
    var hosts = document.querySelectorAll(".form-host");
    for (var i = 0; i < hosts.length; i++) {
      var host = hosts[i];
      if (host === target || !host.firstElementChild) continue;
      if (untouched(host)) host.innerHTML = "";
    }
  });

  document.body.addEventListener("click", function (evt) {
    var btn = evt.target.closest && evt.target.closest("[data-row-close]");
    if (!btn) return;
    var row = btn.closest("tr");
    if (row) row.remove();
  });

  document.body.addEventListener(
    "click",
    function (evt) {
      var btn = evt.target.closest && evt.target.closest("[data-row-expand]");
      if (!btn) return;
      var row = btn.closest("tr");
      var next = row && row.nextElementSibling;
      if (next && next.classList.contains("layer-details")) {
        next.remove();
        evt.preventDefault();
        evt.stopPropagation();
      }
    },
    true
  );
})();
