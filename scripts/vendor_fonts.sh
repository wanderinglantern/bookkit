#!/usr/bin/env bash
# Regenerate the self-hosted webfonts under src/bookkit/web/static/fonts/.
#
# The output of this script is COMMITTED. You only need to run it to change a
# weight, a source version, or the subset. Everything it needs is either on
# disk (Noto, from towerkit) or fetched from a pinned GitHub release
# (JetBrains Mono) — the app fetches no font at runtime, from anywhere.
#
# The converter runs under `uvx`, deliberately: fonttools is a BUILD-TIME tool
# and adding it to pyproject.toml would drag the wheelhouse rebuild-and-publish
# drill (see the `wheelhouse` target in the Makefile) behind a dependency that
# never ships. Nothing here touches pyproject.toml or uv.lock.
#
# Weights: Noto ships 400 and 700 only in towerkit — there is no 500, and a
# browser-synthesised 600 renders smeared (visual-direction spec, Type).
# JetBrains Mono is taken as STATIC TTFs rather than the variable font so all
# three weights (400/500/700) are real outlines.
#
# WHAT IS PINNED, AND WHAT IS NOT
#
#   JetBrains Mono   release tag v2.304, and the release asset's sha256 is
#                    checked below before anything is unzipped. curl runs with
#                    --fail so an HTML error page can never be mistaken for a
#                    zip. Same lesson as WHEELHOUSE_SHA256 in the Makefile: an
#                    unverified downloaded asset is a real risk, and the hash
#                    must come from the artefact you actually shipped.
#
#   Noto             NOT pinned. $noto_src is a WORKING CHECKOUT of towerkit
#                    at whatever revision that clone happens to be on. Re-run
#                    this script after a towerkit pull and the four Noto
#                    outputs can change without anything here noticing. Stated,
#                    not fixed — pinning it means vendoring a Noto release
#                    into this repo, which is a separate decision.
#
# SHA256 OF THE COMMITTED OUTPUT, so the whole chain is checkable offline
# without re-running the subsetter (`shasum -a 256 src/bookkit/web/static/fonts/*`):
#
#   003165f023baaa75597c6d4e956e5437d267cb029e37f1aaa31b33b87757a3e6  JetBrainsMono-Bold.woff2
#   4847e031083c2ffeeb700cc066c12dc53bc21026c8cff4a44bdeab5ac3e4e6c3  JetBrainsMono-Medium.woff2
#   19791e08f55213907400762b426a86a658b7d7c85730c82ef230f6b73476cbcb  JetBrainsMono-Regular.woff2
#   5ba0cc2f1caa2abca985b8d8a767d9a229572dffab8d3c0eaeed4233888a44bf  NotoSans-Bold.woff2
#   9161cd0dc08b142baf0093506d35aa3988441f44a3d05eeeb0343eed71bccc16  NotoSans-Regular.woff2
#   bf246b1ec0365e767ba715f8cca082478f807a9d66fe73812f06a513653d3bd7  NotoSerif-Bold.woff2
#   9e9fadbe1ba1fcf0cc44807c37ccdf54eaa3b7cde7e5b3cbe6c691269fd80f78  NotoSerif-Regular.woff2
#   30f0c136e3c88e422d0791acd97238870f9054a9729bc34cf2ff0d4ed8cac4ad  OFL-JetBrainsMono.txt
#   cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a  OFL-Noto.txt
#
# pyftsubset is not bit-reproducible across fontTools versions, so these are a
# record of what was committed, not a build assertion — a mismatch after a
# re-run means "look at what changed", not "you have been tampered with".
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$here/src/bookkit/web/static/fonts"
noto_src="${NOTO_SRC:-$here/../towerkit/src/towerkit/fonts}"
jbm_tag="v2.304"          # latest stable JetBrains/JetBrainsMono release, 2026-08-18
jbm_sha256="6f6376c6ed2960ea8a963cd7387ec9d76e3f629125bc33d1fdcd7eb7012f7bbf"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Latin (Google's "latin" + "latin-ext" ranges — accented insured and contact
# names are real data), plus the UI glyph vocabulary from the visual-direction
# spec (◆ △ ★ ✓ ▶ ▼ · → …) UNIONED with the glyphs the templates measurably
# use today (— · … ◆ ★ ✕). The union, not either list: dropping a glyph a
# designer is about to use is a silent regression, and the two lists disagree
# — the app uses ✕ (U+2715) and … (U+2026), the spec's list has ✓ and "···".
latin="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
latin_ext="U+0100-02AF,U+1E00-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"
#
# U+2605 (★) IS ASKED FOR HERE AND COMES BACK IN NOTHING. Subsetting can only
# keep what the source font has, and none of the four Noto faces nor JetBrains
# Mono carries U+2605 — verified against the committed .woff2 cmaps, not
# assumed. It stays in the list so the request is on the record rather than
# looking like an oversight; the ★ in _contacts_panel.html renders from a
# system fallback and always has. Removing it from this list would change
# nothing at all. Same check before you trust any glyph in this line:
#   uvx --from "fonttools[woff]==4.63.0" python -c "\
#     from fontTools.ttLib import TTFont; import sys; \
#     print(0x2605 in TTFont(sys.argv[1]).getBestCmap())" <file.woff2>
ui_glyphs="U+2192,U+2713,U+2715,U+25B3,U+25B6,U+25BC,U+25C6,U+2605"
unicodes="$latin,$latin_ext,$ui_glyphs"

subset() {  # subset <source.ttf> <dest.woff2>
  uvx --from "fonttools[woff]==4.63.0" pyftsubset "$1" \
    --output-file="$2" \
    --flavor=woff2 \
    --unicodes="$unicodes" \
    --layout-features='*' \
    --name-IDs='*'
  printf '  %-34s %7s -> %7s\n' "$(basename "$2")" \
    "$(du -h "$1" | cut -f1)" "$(du -h "$2" | cut -f1)"
}

sha256_of() {  # sha256_of <file> -> bare hex digest, macOS or Linux
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d" " -f1
  else
    sha256sum "$1" | cut -d" " -f1
  fi
}

mkdir -p "$out"

echo "Noto (source: $noto_src)"
for face in NotoSans-Regular NotoSans-Bold NotoSerif-Regular NotoSerif-Bold; do
  subset "$noto_src/$face.ttf" "$out/$face.woff2"
done
cp "$noto_src/OFL.txt" "$out/OFL-Noto.txt"

echo "JetBrains Mono (source: github.com/JetBrains/JetBrainsMono @ $jbm_tag)"
# --fail, or curl writes GitHub's 404 HTML page into jbm.zip with exit 0 and
# the failure surfaces as a confusing unzip error several lines later.
curl -sSL --fail -o "$work/jbm.zip" \
  "https://github.com/JetBrains/JetBrainsMono/releases/download/$jbm_tag/JetBrainsMono-${jbm_tag#v}.zip"
got="$(sha256_of "$work/jbm.zip")"
if [ "$got" != "$jbm_sha256" ]; then
  echo "  JetBrainsMono-${jbm_tag#v}.zip does not match its pinned sha256." >&2
  echo "    expected $jbm_sha256" >&2
  echo "    got      $got" >&2
  echo "  Either the release was re-cut upstream or the download was altered." >&2
  echo "  Check the release page before touching jbm_sha256 — take the new" >&2
  echo "  hash from the asset you verified, never from this failure message." >&2
  exit 1
fi
unzip -q -o "$work/jbm.zip" -d "$work"
for face in JetBrainsMono-Regular JetBrainsMono-Medium JetBrainsMono-Bold; do
  subset "$work/fonts/ttf/$face.ttf" "$out/$face.woff2"
done
cp "$work/OFL.txt" "$out/OFL-JetBrainsMono.txt"

echo
echo "Done. The .woff2 files and both OFL.txt licences are COMMITTED — the"
echo "licence requires the notice to travel with the fonts, including inside"
echo "the wheel. Weights must stay in step with the @font-face blocks at the"
echo "top of src/bookkit/web/static/app.css; tests/test_web_shell.py fails the"
echo "build if a src: there does not resolve to a file here."
echo
echo "sha256 of what was just written (update the block at the top of this"
echo "script if these are the files you commit):"
for f in "$out"/*.woff2 "$out"/*.txt; do
  printf '  %s  %s\n' "$(sha256_of "$f")" "$(basename "$f")"
done
