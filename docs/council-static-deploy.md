# Deploy Council static landing from blog build

Source of truth: zavarovkv/zavarov.com. Public URL: https://council.zavarov.com/. Auth and application routes remain on this origin.

## Reviewed artifact

Successful blog main workflow run 41303875094 produced:
- Artifact: council-landing-05d96217a68bad200afa3402a921c2f66b35c57f-deploy
- GitHub artifact ID: 13082825703

Artifact is a ZIP containing council-landing.tar.gz. Download using authorized GitHub tooling. Verify the ZIP and tar contents locally before transfer. The installer requires the exact SHA-256 of the inner tar.gz, not the ZIP. Do not trust a checksum copied from an unverified source.

## Operator deployment (after PR checks and review)

1. Retrieve the reviewed artifact and extract council-landing.tar.gz to a temporary local directory.
2. Compute `sha256sum council-landing.tar.gz`; inspect tar entries for `index.html`, `en/index.html`, `council/avatars/*.png` and fingerprinted CSS. Confirm canonical URLs point to council.zavarov.com and sign-in to /login.
3. Copy the archive to a restricted temporary path on the server, then use the reviewed `scripts/install-council-landing.py <archive> --sha256 <digest>` as an authorized operator. The installer validates entries and atomically points /var/www/agent-council-landing to a versioned release.
4. Verify the static files exist and Nginx can read them. Review and apply the route through the existing guarded Council route management workflow, run `nginx -t` and reload gracefully.
5. Smoke test /, /en/, /council/avatars/qui-gon.png, /login, /profiles, /auth/*, /api/*, /integrations/linear/*, including an unauthorized request to protected API.
6. Verify Telegram OAuth callbacks remain on the same origin. Monitor HTTP errors and login failures.

## Rollback

Restore the prior Nginx route via the existing guarded route management rollback. Restore the previous static release symlink only if needed. Do not remove or reset authentication data. Never change DNS or OAuth credentials for this migration.

No automatic production deployment is wired in this PR. The host installer requires an authorized operator and an explicit reviewed artifact.

## Restricted SSH receiver (not installed by this PR)

The reviewed `scripts/council-landing-deploy.py` reads the forced-command's `SSH_ORIGINAL_COMMAND`, accepts only `council-landing-deploy <64 lowercase hex SHA-256>`, bounds the incoming archive to 8 MiB and invokes the fixed installer with `sudo -n`. It must be installed as an immutable root-owned file outside the deploy user's writable directories. The authorized_keys entry should use `command="/usr/local/libexec/council/council-landing-deploy.py",restrict` and a separately verified Ed25519 public key. Set a dedicated `sudoers` rule for only the fixed installer path, review it with `visudo -c`, and deny all other privileged operations. Do not use the root password.

**Release limitation:** this receiver installs the artifact but does not itself reload Nginx or perform an HTTP smoke test/rollback. Do not activate the GitHub workflow for production until a separately reviewed server-side verification/rollback step exists and is tested. The current pipeline is a preparation, not a production-ready one-click deployment.

## HTTP smoke gate

After Nginx serves the candidate, run `python3 scripts/verify-council-landing.py --base-url https://council.zavarov.com`. It requires HTTP 200 for the RU and EN landing and the existing `/login?next=%2Fprofiles` route, and checks a recognizable public marker. This is read-only and does not assert that authenticated sessions or Telegram OAuth actually succeed. A failed smoke test must block release completion and trigger an operator-controlled rollback to the previous Nginx config/static release. The current restricted SSH receiver does not invoke this check or perform that rollback automatically; do not claim full automated recovery.
