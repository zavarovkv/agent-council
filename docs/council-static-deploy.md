# Deploy the blog-built Council landing

The public source is `zavarovkv/zavarov.com`. The reviewed Hugo artifact serves
`https://council.zavarov.com/` and `/en/` without redirecting to the blog.
`/login`, `/auth/*`, `/api/*` and Linear integrations keep their existing routes.
Team statistics still use the public, aggregate-only `/auth/login/council/team` endpoint.

## Artifact and initial deployment

1. Select an exact successful **main push** run of the blog's GitHub Pages workflow.
   Download its `council-landing-<commit-sha>-deploy` artifact with authenticated GitHub
   tooling. Record the source commit and run; do not reuse a historical example run.
2. Extract `council-landing.tar.gz` from the ZIP and compute the inner archive's SHA-256.
   Inspect both pages, all four avatars and their CSS, JS, fonts and image references.
   All static assets must live below `/council/`; root `/css/` or `/fonts/` paths would
   reach the application instead. Check localized canonical URLs, the existing login
   URL, theme/language controls, Telegram link and live team statistics.
3. Transfer the archive to a restricted temporary path. Run the reviewed installer
   as an operator: `python3 scripts/install-council-landing.py ARCHIVE --sha256 DIGEST`.
   On the first installation, omit `--verify-url` until Nginx has been switched.
4. Apply the Council route through the guarded route-management procedure. It must
   retain the previous Council configuration, validate with `nginx -t`, reload
   gracefully and verify that TravelLine's process and configuration are unchanged.
5. Run the installer again with the same archive/hash and
   `--verify-url https://council.zavarov.com`. It verifies the exact RU/EN page bytes
   through **127.0.0.1 over HTTPS**, preserving the real hostname for Host, TLS SNI
   and certificate validation. Cloudflare's cached page cannot satisfy this check.
   Login must return 200; an unauthenticated protected API request must return 401.
6. Check all static resources, team metrics and both themes at desktop/mobile sizes.
   Check the existing authenticated routes separately; a public smoke test does not
   prove that a real Telegram OAuth exchange succeeds.

The installer reads and hashes one bounded archive snapshot before extraction;
replacing the sender's file cannot change the checked bytes. Compressed and expanded
sizes, entry count, paths and duplicate names are bounded. Links are rejected.
Releases use the full SHA-256, and cached releases must exactly match the archive.
Only the public symlink changes. The same Council operations lock excludes concurrent
application/routing maintenance. Credentials and application data are not modified.

## Rollback

For later deployments, a failed origin smoke check restores the previous static
symlink automatically. This does **not** roll back Nginx configuration or authenticated
application state. If the initial routing cutover fails, restore the saved Council
Nginx configuration through the guarded operator procedure, validate and reload.
Retain prior releases until validation and rollback checks have passed.

## Automatic publication and restricted receiver

`scripts/council-landing-deploy.py` accepts only the forced command
`council-landing-deploy <64 lowercase hex SHA-256>`. It reads at most 8 MiB from stdin
and invokes the fixed privileged installer with origin verification. The installer,
verifier and receiver must be root-owned files outside the deploy user's writable
paths, installed together under `/usr/local/libexec/council/`. The installer uses
`/etc/agent-council/operations.lock`; it returns temporary failure code 75 if another owned operation is active. The
receiver retries only that condition, for up to 65 seconds; validation failures
are not retried.

If separately authorized, use a dedicated unprivileged SSH account, a verified key
with `command="/usr/local/libexec/council/council-landing-deploy.py",restrict`, and a
narrow sudoers rule for only the installer path, validated by `visudo -c`. Keep strict
SSH host-key verification. No shared `/tmp` lock, root password, forwarding or arbitrary
remote shell is needed.

The owner-authorized blog pipeline deploys after its successful main-branch build
and publication. PRs cannot deploy. Bootstrap or rotate the dedicated public key
from a reviewed checkout as root:

```bash
bash scripts/setup-council-static-deploy.sh /secure/path/council-deploy.pub
```

The script validates the public key, existing account and ownership, serializes
with Council operations, installs the three root-owned programs and validates its
single-installer sudoers rule. The account has a locked password, no extra groups,
and root-owned SSH configuration. No service restart or Nginx change is needed.
Private keys stay outside this repository. Verify arbitrary SSH commands fail,
then configure the blog's `council-production` environment secrets/variables as
described in its `docs/council-deploy-automation.md`. Restrict that environment to
`main`; automatic publication uses no reviewer gate. A new key replaces the old
public key. To revoke deployment, remove this account's authorized key and the
GitHub environment private-key secret; existing static content remains served.

Use the blog's manual deployment workflow with a previous successful build for
rollback, or revert the source on main and rebuild. Agents and the authenticated
application have their own deployment lifecycle.
