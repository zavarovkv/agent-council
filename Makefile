.PHONY: test test-integration lint format inspect

PYTHON ?= python3
RUFF ?= ruff

test:
	$(PYTHON) -m unittest discover -s tests -v
	for script in scripts/*.sh; do bash -n "$$script"; done

test-integration:
	docker build -t agent-council-hermes:test .
	$(PYTHON) scripts/check-compose.py
	docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m --user 10000:10000 --entrypoint /opt/hermes/.venv/bin/python -e PYTHONDONTWRITEBYTECODE=1 -v "$(CURDIR)/tests/integration:/tests:ro" --workdir /opt/hermes agent-council-hermes:test -m unittest discover -s /tests -v

lint:
	$(RUFF) check src tests scripts/*.py
	$(RUFF) format --check src tests scripts/*.py
	for script in scripts/*.sh; do bash -n "$$script"; done

format:
	$(RUFF) format src tests scripts/*.py

inspect:
	bash scripts/inspect-server.sh
