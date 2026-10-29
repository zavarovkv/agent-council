"""Owner-only Telegram OIDC provider for the separate Hermes deployment."""

from __future__ import annotations

import os
from pathlib import Path


def register(ctx):
    """Use Hermes' OIDC/session implementation, then restrict verified identities."""
    from hermes_cli.dashboard_auth import ProviderError
    from plugins.dashboard_auth.self_hosted import SelfHostedOIDCProvider

    from .owner import OwnerDenied, OwnerPolicy

    class TelegramOwnerProvider(SelfHostedOIDCProvider):
        name = "telegram-owner"
        display_name = "Telegram"

        def __init__(self):
            client_id = os.environ.get("HERMES_TELEGRAM_CLIENT_ID", "").strip()
            secret = os.environ.get("HERMES_TELEGRAM_CLIENT_SECRET", "").strip()
            super().__init__(
                issuer="https://oauth.telegram.org",
                client_id=client_id or "not-configured",
                client_secret=secret,
                scopes="openid profile",
                id_token_leeway=30,
            )
            owner_username = os.environ.get("HERMES_TELEGRAM_OWNER_USERNAME", "").strip()
            owner_id = os.environ.get("HERMES_TELEGRAM_ALLOWED_ID", "").strip()
            home = os.environ.get("HERMES_HOME", "").strip()
            self.owner_policy = None
            if home and (owner_username or owner_id):
                try:
                    self.owner_policy = OwnerPolicy(
                        Path(home) / "telegram-owner.json", owner_username, owner_id
                    )
                except OwnerDenied:
                    # A malformed setting must not prevent provider registration:
                    # the dashboard must retain its authentication gate.
                    pass
            self.ready = bool(client_id and secret and self.owner_policy is not None)

        def start_login(self, *, redirect_uri):
            if not self.ready:
                raise ProviderError("Telegram login is not configured yet. Contact the owner.")
            return super().start_login(redirect_uri=redirect_uri)

        def _verify_id_token(self, id_token):
            if self.owner_policy is None:
                raise ProviderError("No Telegram owner is configured.")
            # JWT signature, issuer, audience and expiry are verified FIRST.
            claims = super()._verify_id_token(id_token)
            try:
                self.owner_policy.authorize(claims)
            except OwnerDenied as exc:
                raise ProviderError(
                    "Access is restricted to the owner's Telegram account."
                ) from exc
            return claims

        # The upstream class aliases this method for per-request verification.
        # Override the alias too so an existing session cannot bypass the owner check.
        _claims_for = _verify_id_token

    # Register even before credentials arrive: the gate stays ON and refuses login.
    ctx.register_dashboard_auth_provider(TelegramOwnerProvider())
