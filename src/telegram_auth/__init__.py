"""Telegram OIDC restricted to the configured owner and invited accounts."""

from __future__ import annotations

import hmac
import os
from pathlib import Path


def register(ctx):
    """Use Hermes' OIDC/session implementation, then restrict verified identities."""
    from hermes_cli.dashboard_auth import ProviderError
    from hermes_cli.dashboard_auth.base import InvalidCodeError
    from plugins.dashboard_auth.self_hosted import SelfHostedOIDCProvider

    from .owner import OwnerDenied, OwnerPolicy
    from .sessions import PREFIX, SessionStore, SessionStoreError

    class TelegramOwnerProvider(SelfHostedOIDCProvider):
        name = "telegram-owner"
        display_name = "Telegram"

        def __init__(self):
            client_id = os.environ.get("HERMES_TELEGRAM_CLIENT_ID", "").strip()
            self.widget_client_id = client_id
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
            additional_ids = os.environ.get("HERMES_TELEGRAM_ALLOWED_IDS", "")
            home = os.environ.get("HERMES_HOME", "").strip()
            self.owner_policy = None
            self.session_store = None
            if home and (owner_username or owner_id):
                try:
                    self.owner_policy = OwnerPolicy(
                        Path(home) / "telegram-owner.json", owner_username, owner_id, additional_ids
                    )
                    self.session_store = SessionStore(
                        Path(home).resolve() / "dashboard-sessions.sqlite3",
                        client_id,
                        days=int(os.environ.get("HERMES_TELEGRAM_SESSION_DAYS", "30")),
                    )
                except (OwnerDenied, ValueError):
                    # A malformed setting must not prevent provider registration:
                    # the dashboard must retain its authentication gate.
                    pass
            self.ready = bool(client_id and secret and self.owner_policy and self.session_store)

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
                raise InvalidCodeError(
                    "This Telegram account has not been granted access."
                ) from exc
            return claims

        # The upstream class aliases this method for per-request verification.
        # Override the alias too so an existing session cannot bypass the owner check.
        _claims_for = _verify_id_token

        def _persistent_session(self, claims):
            if not self.ready:
                raise ProviderError("Telegram Login is not configured.")
            self.owner_policy.authorize(claims)
            try:
                token, stored = self.session_store.issue(claims)
            except SessionStoreError as exc:
                raise ProviderError("Dashboard session store unavailable.") from exc
            return self._session(token, "", {**stored.claims, "exp": stored.expires_at})

        def complete_login(self, *, code, state, code_verifier, redirect_uri):
            session = super().complete_login(
                code=code, state=state, code_verifier=code_verifier, redirect_uri=redirect_uri
            )
            try:
                return self._persistent_session(super()._verify_id_token(session.access_token))
            except OwnerDenied as exc:
                raise InvalidCodeError(
                    "This Telegram account has not been granted access."
                ) from exc

        def verify_session(self, *, access_token):
            if not isinstance(access_token, str) or not access_token.startswith(PREFIX):
                return super().verify_session(access_token=access_token)
            if not self.ready:
                return None
            try:
                stored = self.session_store.lookup(access_token)
                if stored is None:
                    return None
                try:
                    self.owner_policy.authorize(stored.claims)
                except OwnerDenied:
                    self.session_store.revoke(access_token)
                    return None
                return self._session(access_token, "", {**stored.claims, "exp": stored.expires_at})
            except SessionStoreError as exc:
                raise ProviderError("Dashboard session store unavailable.") from exc

        def revoke_access_session(self, *, access_token):
            if self.session_store is not None:
                try:
                    self.session_store.revoke(access_token)
                except SessionStoreError as exc:
                    raise ProviderError("Dashboard session store unavailable.") from exc

        def complete_widget_login(self, id_token, nonce):
            if not self.ready or self.owner_policy is None:
                raise ProviderError("Telegram Login is not configured.")
            # Validate the signed claims and browser nonce BEFORE owner enrollment.
            claims = super()._verify_id_token(id_token)
            claim_nonce = claims.get("nonce")
            if (
                not isinstance(claim_nonce, str)
                or not claim_nonce.isascii()
                or not hmac.compare_digest(claim_nonce, nonce)
            ):
                raise InvalidCodeError("Telegram nonce does not match this browser.")
            return self._persistent_session(claims)

    # Register even before credentials arrive: the gate stays ON and refuses login.
    ctx.register_dashboard_auth_provider(TelegramOwnerProvider())
