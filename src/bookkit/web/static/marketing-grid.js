/* The marketing grid's two LAYOUT behaviours, which CSS alone cannot express.
 *
 * The grid is 22 columns wide and does not fit: measured on the running app
 * (2026-08-26, 1600px window) the table asks for 1811px and gets 1064px, so
 * 41% of it is off the right-hand edge. app.css pins the two columns that say
 * WHOSE row it is and sticks the header; these are the two pieces of that
 * which need a measurement or an event.
 *
 * 1. THE SECOND PIN'S OFFSET IS THE FIRST PIN'S WIDTH, and auto table layout
 *    decides that from content. It cannot be a number in the stylesheet, so it
 *    is measured here and published as `--pin-2` on the table.
 *
 * 2. TWO LINES OF COVERAGE, TWO SCROLLBARS. Each block owns its own
 *    `.table-scroll`, so comparing Premium across GL and Auto meant aligning
 *    two scroll positions by hand — and the more blocks a placement has, the
 *    worse it got. They share one position instead.
 *
 * WHY IT RE-RUNS ON EVERY SWAP. Four cells on this grid answer with the whole
 * BLOCK (routes/marketing.py `_BLOCK_CELLS`), so the table a save lands in is
 * a NEW table with new widths and a fresh scroll position — a measurement
 * taken once at load is stale the first time anybody edits a premium.
 */
(function () {
  "use strict";

  var GRID = ".marketing-grid";
  var SCROLLER = ".marketing-block .table-scroll";

  // ECHO SUPPRESSION, PER ELEMENT — never one shared boolean.
  //
  // Setting `scrollLeft` fires a `scroll` event on that element, and it fires
  // ASYNCHRONOUSLY (at the next animation frame), which is long after a
  // `syncing = true … syncing = false` around the write has already cleared
  // itself. The flag would be false by the time the echoes arrived, each echo
  // would drive every other scroller again, and with blocks whose content
  // widths differ — the provisional block has fewer figures than a line of
  // coverage — the clamped values ping-pong instead of converging.
  //
  // So the element about to be written is MARKED, and its own next scroll
  // event spends the mark and returns. A write always changes the value (the
  // caller only writes where they differ), so a mark is always spent.
  var ECHO = "syncEcho";

  function measure(table) {
    var first = table.querySelector("thead th.pin-1");
    if (!first) return;
    // getBoundingClientRect, not offsetWidth: the cell can be a fractional
    // width under auto layout, and a rounded offset leaves a hairline of the
    // scrolling content visible between the two pinned columns.
    var width = first.getBoundingClientRect().width;
    if (width > 0) table.style.setProperty("--pin-2", width + "px");
  }

  function measureAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var tables = scope.querySelectorAll(GRID);
    for (var i = 0; i < tables.length; i++) measure(tables[i]);
    // A swap answers with a `<tr>` or an `<article>`, so the table itself is
    // usually an ANCESTOR of what landed rather than inside it. Walking up
    // catches the row-sized answers; the querySelectorAll above catches the
    // block-sized ones.
    if (root && root.closest) {
      var own = root.closest(GRID);
      if (own) measure(own);
    }
  }

  function scrollers() {
    return Array.prototype.slice.call(document.querySelectorAll(SCROLLER));
  }

  function onScroll(evt) {
    var source = evt.target;
    if (!source.matches || !source.matches(SCROLLER)) return;
    if (source.dataset[ECHO]) {
      delete source.dataset[ECHO];
      return;
    }
    var all = scrollers();
    for (var i = 0; i < all.length; i++) {
      var other = all[i];
      if (other === source) continue;
      // ONLY WHERE THERE IS ROOM TO GO. A block with fewer columns of content
      // — the provisional block, or a line whose figures are all blank —
      // scrolls less far, and driving it past its own end would silently clamp
      // to a DIFFERENT number from the one it was told, which is an echo that
      // says something new and would drag every other block back to it.
      var room = other.scrollWidth - other.clientWidth;
      if (room <= 0) continue;
      var want = Math.min(source.scrollLeft, room);
      if (Math.abs(other.scrollLeft - want) <= 1) continue;
      other.dataset[ECHO] = "1";
      other.scrollLeft = want;
    }
  }

  // Capture, because `scroll` does not bubble. One listener on the document
  // rather than one per scroller, so a block swapped in by htmx is covered
  // without re-binding anything.
  document.addEventListener("scroll", onScroll, true);

  document.addEventListener("DOMContentLoaded", function () {
    measureAll(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    measureAll(evt.target);
  });

  // The first pinned column's width tracks the window: it is a prose column
  // that wraps, so a narrower window makes it taller and sometimes narrower.
  var pending = null;
  window.addEventListener("resize", function () {
    if (pending) return;
    pending = window.requestAnimationFrame(function () {
      pending = null;
      measureAll(document);
    });
  });
})();
