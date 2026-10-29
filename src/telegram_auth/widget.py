"""Exchange official Telegram Login results for existing verified Hermes sessions."""

import json
import logging
import os
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from hermes_cli.dashboard_auth.audit import AuditEvent, audit_log
from hermes_cli.dashboard_auth.base import InvalidCodeError, ProviderError
from hermes_cli.dashboard_auth.cookies import (
    clear_session_cookies,
    clear_sso_attempt_cookie,
    set_session_cookies,
)
from hermes_cli.dashboard_auth.prefix import prefix_from_request
from hermes_cli.dashboard_auth.registry import get_provider
from hermes_cli.dashboard_auth.request_utils import access_token_max_age, is_safe_next_path
from starlette.concurrency import run_in_threadpool

from .challenges import ChallengeRejected, ChallengeStore
from .owner import OwnerDenied

router = APIRouter(prefix="/auth/login/telegram-widget")
store = ChallengeStore()
NONCE_COOKIE = "__Host-council_telegram_nonce"
COOKIE_ATTRIBUTES = {"secure": True, "httponly": True, "samesite": "strict", "path": "/"}


async def revoke_access_cookie(token):
    """Existing Hermes logout also revokes the persistent application session."""
    provider = get_provider("telegram-owner")
    if provider is not None and token:
        try:
            await run_in_threadpool(provider.revoke_access_session, access_token=token)
        except ProviderError:
            logging.getLogger(__name__).warning("Dashboard session revocation unavailable.")


def _response(data, status=200):
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


def _check_origin(request):
    configured = urlsplit(os.environ.get("HERMES_PUBLIC_URL", ""))
    if configured.scheme != "https" or not configured.netloc:
        raise HTTPException(503, "Public HTTPS URL is not configured.")
    origin = f"https://{configured.netloc}"
    if request.url.scheme != "https" or request.headers.get("origin") != origin:
        raise HTTPException(403, "Same-origin HTTPS request required.")
    if request.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise HTTPException(403, "Same-origin request required.")
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(415, "JSON required.")


async def _body(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 16384:
            raise HTTPException(413, "Request too large.")
        data.extend(chunk)
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON.") from None
    if not isinstance(result, dict):
        raise HTTPException(400, "JSON object required.")
    return result


def _safe_next(value):
    # Check decoded forms too; browsers treat backslashes as URL separators.
    if not isinstance(value, str):
        return False
    candidate = value
    for _ in range(5):
        if (
            not is_safe_next_path(candidate)
            or candidate.startswith("//")
            or "\\" in candidate
            or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        ):
            return False
        decoded = unquote(candidate)
        if decoded == candidate:
            return True
        candidate = decoded
    return False


def _provider():
    provider = get_provider("telegram-owner")
    if provider is None or not getattr(provider, "ready", False):
        raise HTTPException(503, "Telegram Login is not configured.")
    return provider


@router.post("/challenge")
async def challenge(request: Request):
    _check_origin(request)
    body = await _body(request)
    provider = _provider()
    target = body.get("next", "")
    prefix = prefix_from_request(request)
    if prefix and not _safe_next(prefix):
        raise HTTPException(400, "Invalid proxy prefix.")
    target = target if target and _safe_next(target) else (prefix + "/")
    if prefix and not (target == prefix or target.startswith(prefix + "/")):
        target = prefix + target
    try:
        nonce = store.issue(
            client=request.client.host if request.client else "unknown", next_path=target
        )
    except ChallengeRejected:
        return _response({"detail": "Слишком много попыток. Попробуйте позже."}, 429)
    response = _response({"client_id": provider.widget_client_id, "nonce": nonce})
    response.set_cookie(NONCE_COOKIE, nonce, max_age=store.ttl, **COOKIE_ATTRIBUTES)
    return response


@router.post("/complete")
async def complete(request: Request):
    _check_origin(request)
    body = await _body(request)
    provider = _provider()
    token = body.get("id_token")
    nonce = request.cookies.get(NONCE_COOKIE, "")
    if not isinstance(token, str) or not 0 < len(token) <= 12000 or not nonce:
        raise HTTPException(400, "Token and browser challenge required.")
    try:
        pending = store.consume(nonce)
    except ChallengeRejected:
        return _response({"detail": "Попытка входа устарела. Обновите страницу."}, 403)
    try:
        session = await run_in_threadpool(provider.complete_widget_login, token, nonce)
    except OwnerDenied:
        response = _response({"detail": "Этому Telegram-аккаунту доступ не разрешен."}, 403)
    except InvalidCodeError:
        response = _response({"detail": "Не удалось подтвердить вход через Telegram."}, 401)
    except ProviderError:
        response = _response({"detail": "Telegram временно недоступен. Попробуйте позже."}, 503)
    else:
        response = _response({"next": pending.next_path})
        prefix = prefix_from_request(request)
        clear_session_cookies(response, prefix=prefix)
        clear_sso_attempt_cookie(response, prefix=prefix)
        set_session_cookies(
            response,
            access_token=session.access_token,
            refresh_token="",
            access_token_expires_in=access_token_max_age(session),
            use_https=True,
            prefix=prefix,
            provider=session.provider,
        )
        audit_log(AuditEvent.LOGIN_SUCCESS, provider=session.provider, user_id=session.user_id)
    response.set_cookie(NONCE_COOKIE, "", max_age=0, **COOKIE_ATTRIBUTES)
    return response
