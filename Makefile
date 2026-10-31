.PHONY: test test-integration test-backups test-secretary test-linear test-browser test-nginx lint format inspect

PYTHON ?= python3
RUFF ?= ruff

test:
	$(PYTHON) -m unittest discover -s tests -v
	for script in scripts/*.sh; do bash -n "$$script"; done

test-integration:
	docker build --build-arg COUNCIL_REVISION=$$(git rev-parse HEAD) -t agent-council-hermes:test .
	$(PYTHON) scripts/check-compose.py
	$(PYTHON) scripts/check-runtime.py
	docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m --user 10000:10000 --entrypoint /opt/hermes/.venv/bin/python -e PYTHONDONTWRITEBYTECODE=1 -v "$(CURDIR)/tests/integration:/tests:ro" --workdir /opt/hermes agent-council-hermes:test -m unittest discover -s /tests -v

test-browser:
	mkdir -p .browser-fixture
	docker run --rm --network none --read-only --user 10000:10000 --entrypoint /opt/hermes/.venv/bin/python -e PYTHONDONTWRITEBYTECODE=1 -v "$(CURDIR)/tests/integration:/tests:ro" --workdir /opt/hermes agent-council-hermes:test /tests/render_login_fixture.py > .browser-fixture/login.html
	npx playwright test

test-backups:
	$(PYTHON) scripts/check-backups.py

test-secretary:
	$(PYTHON) scripts/check-secretary.py

test-linear:
	$(PYTHON) scripts/check-linear.py

test-nginx:
	$(PYTHON) scripts/check-nginx.py

lint:
	$(RUFF) check src tests scripts/*.py
	$(RUFF) format --check src tests scripts/*.py
	for script in scripts/*.sh; do bash -n "$$script"; done

format:
	$(RUFF) format src tests scripts/*.py

inspect:
	bash scripts/inspect-server.sh
