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
# towerkit (via the path dependency in uv.lock), macOS x86_64 + arm64,
# CPython 3.12/3.13. Attach the zip to a GitHub release, then update
# WHEELHOUSE_URL in install.sh if the tag changed. Re-run and re-upload
# (gh release upload --clobber) whenever dependencies change — same drill as
# towerkit's wheelhouse.
wheelhouse:
	rm -rf wheelhouse bookkit-wheelhouse-macos.zip
	mkdir wheelhouse
	uv export --frozen --no-dev --no-emit-project --no-emit-package towerkit \
	  --no-hashes -o wheelhouse/requirements.txt
	for pyver in 312 313; do \
	  python3 -m pip download -r wheelhouse/requirements.txt -d wheelhouse --only-binary=:all: \
	    --python-version $$pyver --implementation cp \
	    --platform macosx_11_0_arm64 --platform macosx_12_0_arm64 \
	    --platform macosx_10_9_universal2 --platform macosx_11_0_universal2 -q; \
	  python3 -m pip download -r wheelhouse/requirements.txt -d wheelhouse --only-binary=:all: \
	    --python-version $$pyver --implementation cp \
	    --platform macosx_10_9_x86_64 --platform macosx_10_12_x86_64 --platform macosx_10_13_x86_64 \
	    --platform macosx_11_0_x86_64 --platform macosx_12_0_x86_64 \
	    --platform macosx_10_9_universal2 --platform macosx_11_0_universal2 -q; \
	done
	python3 -m pip download hatchling editables -d wheelhouse --only-binary=:all: -q
	cd wheelhouse && zip -q -r ../bookkit-wheelhouse-macos.zip *.whl
