# Public Council landing migration

Public marketing content is owned by `zavarovkv/zavarov.com` and rendered at `https://zavarov.com/council/` (EN: `/en/council/`). Agent Council remains the application/auth/API origin at `https://council.zavarov.com`.

## Safe release order

1. Review and merge blog PR #3 first. Verify Hugo CI, both public pages, mobile presentation, image loading and sign-in link.
2. Verify `https://council.zavarov.com/login?next=%2Fprofiles` works before changing Nginx. Preserve Telegram Login Widget allowed origin and OAuth callbacks on `council.zavarov.com`.
3. Review and merge application PR #55. Run `make test-nginx` and integration tests, then deploy through the existing guarded operator workflow. Validate `nginx -t` and exact live routes.
4. Confirm `/` returns a temporary 302 to the blog while `/login`, `/auth/*`, `/api/*`, `/integrations/linear/*` and the dashboard remain on the app origin. Confirm auth remains enforced.
5. Monitor failures and rollback the application Nginx release if login or callbacks fail. Keep 302 until search indexing and operations are confirmed; consider 301 later.

## Intentional limitations

- Current blog PR is a static adaptation, not pixel-perfect parity with the old Beauty.css login/roster view.
- The blog currently references public portrait endpoints hosted by the application; do not retire these endpoints until binary PNGs have been copied to blog-owned static assets and verified.
- Model, task and token counters are not migrated. Do not expose private telemetry or enable broad CORS solely for marketing. If public metrics are required, design a sanitized read-only snapshot with caching, schema, rate limits and failure states.
- Do not move Telegram/OAuth credentials or authentication widgets to the static blog.

## Acceptance checklist

- [ ] Blog PR CI green for pinned and latest Hugo
- [ ] RU/EN pages rendered and links checked
- [ ] Mobile and dark/light contrast reviewed
- [ ] Portraits load and no sensitive browser requests
- [ ] App PR Nginx and integration CI green
- [ ] Blog deployed before app redirect
- [ ] Live login, OAuth callbacks, dashboard and API smoke-tested
- [ ] Temporary redirect verified, rollback path documented
