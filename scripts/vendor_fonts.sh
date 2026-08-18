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
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$here/src/bookkit/web/static/fonts"
noto_src="${NOTO_SRC:-$here/../towerkit/src/towerkit/fonts}"
jbm_tag="v2.304"          # latest stable JetBrains/JetBrainsMono release, 2026-08-18
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

mkdir -p "$out"

echo "Noto (source: $noto_src)"
for face in NotoSans-Regular NotoSans-Bold NotoSerif-Regular NotoSerif-Bold; do
  subset "$noto_src/$face.ttf" "$out/$face.woff2"
done
cp "$noto_src/OFL.txt" "$out/OFL-Noto.txt"

echo "JetBrains Mono (source: github.com/JetBrains/JetBrainsMono @ $jbm_tag)"
curl -sL -o "$work/jbm.zip" \
  "https://github.com/JetBrains/JetBrainsMono/releases/download/$jbm_tag/JetBrainsMono-${jbm_tag#v}.zip"
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
