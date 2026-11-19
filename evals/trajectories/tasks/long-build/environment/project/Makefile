.PHONY: build test check

build:
	./scripts/build.sh

test:
	python -m pytest -q tests

check: build test
