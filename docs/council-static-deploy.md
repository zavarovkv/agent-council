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
