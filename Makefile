.PHONY: test lint typecheck check demo clean wheelhouse

test:
	uv run --group dev pytest -q

lint:
	uv run --group dev ruff check src tests

typecheck:
	uv run --group dev mypy src/bookkit

check: lint typecheck test

# Throwaway demo: fresh scratch DB + three linked towerkit program files.
DEMO_DIR ?= /tmp/bookkit-demo
demo:
	rm -rf $(DEMO_DIR) && mkdir -p $(DEMO_DIR)
	BOOKKIT_DB=$(DEMO_DIR)/bookkit.db uv run bookctl seed --demo --programs-dir $(DEMO_DIR)/programs
	BOOKKIT_DB=$(DEMO_DIR)/bookkit.db BOOKKIT_PROGRAM_ROOTS=$(DEMO_DIR)/programs uv run bookctl

clean:
	rm -rf wheelhouse bookkit-wheelhouse-macos.zip

# Offline wheelhouse for corporate machines: every runtime dep of bookkit AND
# towerkit (via the path dependency in uv.lock), macOS **arm64**, CPython
# 3.12/3.13.
#
# arm64 only since 2026-08-14. mcp>=2.0 pulls pyjwt[crypto] -> cryptography,
# and cryptography 50.0.0 publishes no macOS x86_64 or universal2 wheels at
# all, so the x86_64 pass could not resolve and failed the whole build. An
# Intel Mac therefore has no offline path; install.sh tries PyPI first, so
# this only bites an Intel machine with no pip access. Do NOT "fix" it by
# pinning cryptography back — it is the one dependency worth keeping current.
#
# Drill when dependencies change: `make wheelhouse`, verify an offline
# install (see below), `gh release upload v0.1.0 bookkit-wheelhouse-macos.zip
# --clobber`, then set WHEELHOUSE_SHA256 in install.sh from the *uploaded*
# asset, in the same commit. Update WHEELHOUSE_URL too if the tag changed.
#
# Verify before uploading — the build succeeding is not the same as the
# wheelhouse working:
#   python3 -m venv /tmp/wh && /tmp/wh/bin/pip install --no-index \
#     --find-links wheelhouse hatchling editables && …-e ../towerkit && …-e .
wheelhouse:
	rm -rf wheelhouse bookkit-wheelhouse-macos.zip
	mkdir wheelhouse
	uv export --frozen --no-dev --no-emit-project --no-emit-package towerkit \
	  --no-hashes -o wheelhouse/requirements.txt
	for pyver in 312 313; do \
	  python3 -m pip download -r wheelhouse/requirements.txt -d wheelhouse --only-binary=:all: \
	    --python-version $$pyver --implementation cp \
	    --platform macosx_11_0_arm64 --platform macosx_12_0_arm64 \
	    --platform macosx_10_9_universal2 --platform macosx_11_0_universal2 \
	    --platform macosx_10_12_universal2 -q; \
	done
	python3 -m pip download hatchling editables -d wheelhouse --only-binary=:all: -q
	cd wheelhouse && zip -q -r ../bookkit-wheelhouse-macos.zip *.whl
