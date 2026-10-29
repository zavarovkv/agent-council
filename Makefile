.PHONY: test lint format inspect

PYTHON ?= python3
RUFF ?= ruff

test:
	$(PYTHON) -m unittest discover -s tests -v
	bash -n scripts/*.sh

lint:
	$(RUFF) check src tests scripts/*.py
	$(RUFF) format --check src tests scripts/*.py
	bash -n scripts/*.sh

format:
	$(RUFF) format src tests scripts/*.py

inspect:
	bash scripts/inspect-server.sh
