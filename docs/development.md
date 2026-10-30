# Development guide

`src/telegram_auth/` contains the owner policy; `config/` and `scripts/` contain
infrastructure and maintenance commands; `tests/` contains security checks;
`assets/` contains the setup page. Local tests require Python **3.11+** and the
standard library. The YAML profile helper runs inside the image.

Frontend development uses Node **24** and `npm ci --ignore-scripts`. Run
`npm run check` for JavaScript syntax. After `make test-integration`, run
`npx playwright install chromium` and `make test-browser`. Browser tests load
the actual public Telegram SDK, with a fake provider popup and fake backend;
no account credentials or real login are used. CI installs the required browser.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test
make lint RUFF=.venv/bin/ruff
make test-integration  # requires Docker; pulls the pinned Hermes image
make test-backups      # real restic encryption/recovery, fake data, no external storage
```

Live checks require outbound HTTPS:

```bash
HERMES_LIVE_URL=https://council.zavarov.com make test
# On the origin server, test origin auth independently of Cloudflare filtering:
HERMES_LIVE_URL=https://council.zavarov.com HERMES_LIVE_ORIGIN_IP=127.0.0.1 make test
```

The optional origin IP changes only routing; TLS still verifies the public
hostname. A Cloudflare rejection is not evidence that application auth works.

GitHub Actions runs Python 3.11/3.14 tests, Ruff, ShellCheck, actionlint,
Dockerfile checks, real Compose credential parsing and offline RSA/JWT tests
against the pinned Hermes adapter. It does not receive server/account secrets or deploy
automatically. Never print production `docker compose config`; use `--quiet`.

`src/council_ops/` is the standard-library host operator toolchain. Unit tests
exercise actual temporary-directory swaps and rollback after simulated schema
migration, tampering, configuration isolation and portable backup exclusions.
`make test-backups` uses the pinned restic maintenance image with a network-disabled
local repository, verifies wrong-password rejection, restores a real Hermes session
database and checks its contents through the pinned runtime. No R2 keys are used.


See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.
