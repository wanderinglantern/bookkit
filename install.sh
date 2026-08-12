#!/bin/sh
# Installer for corporate machines.
#
#   ./install.sh
#
# bookkit depends on a live towerkit checkout sitting NEXT to this repo
# (../towerkit) — clone both first:
#
#   git clone https://github.com/wanderinglantern/towerkit
#   git clone https://github.com/wanderinglantern/bookkit
#
# Tries PyPI first — on machines where pip has network access (directly or
# through the corporate proxy) every dependency, including newly added ones,
# resolves in seconds after a `git pull` in either repo. Machines with no
# PyPI access fall back to the prebuilt wheelhouse from the GitHub release
# (macOS Intel/Apple Silicon, Python 3.12-3.13; github.com only).
#
# Everything installs into ./.venv so the ./bookctl wrapper always works and
# the system Python is never touched. Re-run after every `git pull`.
#
# Afterwards:  ./bookctl

set -eu
cd "$(dirname "$0")"

WHEELHOUSE_URL="https://github.com/wanderinglantern/bookkit/releases/download/v0.1.0/bookkit-wheelhouse-macos.zip"
PY="${PYTHON:-python3}"
TOWERKIT="../towerkit"

if [ ! -f "$TOWERKIT/pyproject.toml" ]; then
    echo "error: towerkit checkout not found at $TOWERKIT" >&2
    echo "clone it next to this repo first:  git clone https://github.com/wanderinglantern/towerkit ../towerkit" >&2
    exit 1
fi

version=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')

echo "→ creating .venv with $PY ($version) …"
rm -rf .venv
"$PY" -m venv .venv

# towerkit installs first (editable, from the sibling checkout) so bookkit's
# `towerkit` requirement is satisfied locally and never fetched from an index.
echo "→ trying PyPI …"
if ./.venv/bin/pip install -q -e "$TOWERKIT" 2>/dev/null \
    && ./.venv/bin/pip install -q -e . 2>/dev/null; then
    echo "✓ installed from PyPI"
else
    echo "→ no PyPI access — installing from the local wheelhouse …"
    case "$version" in
        3.12|3.13) ;;
        *) echo "warning: wheelhouse targets Python 3.12/3.13; found $version" \
               "(set PYTHON=/path/to/python3.12 to override)" ;;
    esac
    # refresh when a required wheel is missing — new deps land in the wheelhouse
    if [ -d wheelhouse ] && ! ls wheelhouse/textual_autocomplete-*.whl >/dev/null 2>&1; then
        echo "→ wheelhouse is stale (missing textual-autocomplete) — refreshing …"
        rm -rf wheelhouse
    fi
    if [ ! -d wheelhouse ]; then
        echo "→ downloading wheelhouse (one time) …"
        curl -fSL --progress-bar -o wheelhouse.zip "$WHEELHOUSE_URL"
        mkdir wheelhouse
        unzip -q wheelhouse.zip -d wheelhouse
        rm wheelhouse.zip
    fi
    ./.venv/bin/pip install -q --no-index --find-links wheelhouse hatchling editables
    ./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e "$TOWERKIT"
    ./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e .
fi

echo
echo "✓ installed. Run:"
echo "    ./bookctl init          # first time: create the database"
echo "    ./bookctl               # the TUI"
