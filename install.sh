#!/bin/sh
# Offline installer for corporate machines with no PyPI access.
#
#   ./install.sh
#
# bookkit depends on a live towerkit checkout sitting NEXT to this repo
# (../towerkit) — clone both first:
#
#   git clone https://github.com/wanderinglantern/towerkit
#   git clone https://github.com/wanderinglantern/bookkit
#
# Downloads the wheelhouse (every dependency of bookkit AND towerkit,
# prebuilt for macOS Intel/Apple Silicon, Python 3.12-3.13) from the GitHub
# release — the only network access needed is github.com — then installs
# both CURRENT checkouts editable into ./.venv, entirely from local wheels.
# Re-run after `git pull` (in either repo); the wheelhouse is cached and
# only re-downloaded if deleted.
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
case "$version" in
    3.12|3.13) ;;
    *) echo "warning: wheelhouse targets Python 3.12/3.13; found $version" \
           "(set PYTHON=/path/to/python3.12 to override)" ;;
esac

if [ ! -d wheelhouse ]; then
    echo "→ downloading wheelhouse (one time) …"
    curl -fSL --progress-bar -o wheelhouse.zip "$WHEELHOUSE_URL"
    mkdir wheelhouse
    unzip -q wheelhouse.zip -d wheelhouse
    rm wheelhouse.zip
fi

echo "→ creating .venv with $PY ($version) …"
rm -rf .venv
"$PY" -m venv .venv

echo "→ installing from local wheels (no PyPI) …"
./.venv/bin/pip install -q --no-index --find-links wheelhouse hatchling editables
# towerkit first (editable, from the sibling checkout) so bookkit's
# `towerkit` requirement is already satisfied when bookkit installs.
./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e "$TOWERKIT"
./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e .

echo
echo "✓ installed. Run:"
echo "    ./bookctl init          # first time: create the database"
echo "    ./bookctl               # the TUI"
