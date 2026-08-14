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
# Tries PyPI first (standard pip env like PIP_INDEX_URL / HTTPS_PROXY is
# honored, so corporate mirrors work). If that fails, the reason is saved to
# .install-pypi.log and the prebuilt wheelhouse from the GitHub release takes
# over (github.com only). A wheelhouse that cannot satisfy the install is
# thrown away and re-downloaded once — stale caches must never fail the
# install.
#
# Everything installs into ./.venv so the ./bookctl wrapper always works and
# the system Python is never touched. Re-run after every `git pull`.
#
# Afterwards:  ./bookctl

set -eu
cd "$(dirname "$0")"

WHEELHOUSE_URL="https://github.com/wanderinglantern/bookkit/releases/download/v0.1.0/bookkit-wheelhouse-macos.zip"
PY="${PYTHON:-python3}"
# Bump this in the SAME commit that re-uploads the release asset, and take the
# hash from the *uploaded* file, not the local copy. towerkit's went stale when
# mcp>=2.0 landed and the fallback then aborted with "altered in transit" — a
# tamper warning about a file nobody tampered with.
WHEELHOUSE_SHA256="9c7fdbf1262c9261216295990a29242729de4b75d073fb1e48b80b4a15a8c5e0"
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

fetch_wheelhouse() {
    echo "→ downloading wheelhouse (~85MB) …"
    curl -fSL --progress-bar -o wheelhouse.zip "$WHEELHOUSE_URL"
    # the corporate proxy has been caught altering pip downloads —
    # verify this artifact against the hash pinned in git
    echo "$WHEELHOUSE_SHA256  wheelhouse.zip" | shasum -a 256 -c - || {
        echo "error: wheelhouse.zip hash mismatch — the download was altered in transit." >&2
        echo "Do NOT bypass this. Re-try on a trusted network, or copy the zip manually." >&2
        rm -f wheelhouse.zip
        exit 1
    }
    rm -rf wheelhouse
    mkdir wheelhouse
    unzip -q wheelhouse.zip -d wheelhouse
    rm wheelhouse.zip
}

offline_install() {
    ./.venv/bin/pip install -q --no-index --find-links wheelhouse hatchling editables         && ./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e "$TOWERKIT"         && ./.venv/bin/pip install -q --no-index --find-links wheelhouse --no-build-isolation -e .
}

# towerkit installs first (editable, from the sibling checkout) so bookkit's
# `towerkit` requirement is satisfied locally and never fetched from an index.
if [ "${OFFLINE:-0}" = "1" ]; then
    echo "→ OFFLINE=1 — skipping PyPI, using the wheelhouse …"
    [ -d wheelhouse ] || fetch_wheelhouse
    if ! offline_install; then
        echo "→ cached wheelhouse could not satisfy the install — refreshing it …"
        fetch_wheelhouse
        offline_install
    fi
    echo "✓ installed from the wheelhouse"
elif ./.venv/bin/pip install -q -e "$TOWERKIT" >.install-pypi.log 2>&1 \
    && ./.venv/bin/pip install -q -e . >>.install-pypi.log 2>&1; then
    echo "✓ installed from PyPI"
    rm -f .install-pypi.log
else
    echo "→ PyPI attempt failed (reason saved to .install-pypi.log):"
    tail -3 .install-pypi.log | sed 's/^/    /'
    echo "→ falling back to the local wheelhouse …"
    case "$version" in
        3.12|3.13) ;;
        *) echo "warning: wheelhouse targets Python 3.12/3.13; found $version" \
               "(set PYTHON=/path/to/python3.12 to override)" ;;
    esac
    [ -d wheelhouse ] || fetch_wheelhouse
    if ! offline_install; then
        echo "→ cached wheelhouse could not satisfy the install — refreshing it …"
        fetch_wheelhouse
        offline_install
    fi
    echo "✓ installed from the wheelhouse"
fi

echo
echo "✓ installed. Run:"
echo "    ./bookctl init          # first time: create the database"
echo "    ./bookctl               # the TUI"
