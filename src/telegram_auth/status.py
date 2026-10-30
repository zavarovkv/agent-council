"""Expose only login bootstrap metadata until an operator session is verified."""

from fastapi.responses import JSONResponse
from hermes_cli.dashboard_auth.base import ProviderError
from hermes_cli.dashboard_auth.cookies import read_session_cookies
from hermes_cli.dashboard_auth.registry import get_provider
from hermes_cli.dashboard_auth.request_utils import extract_bearer
from starlette.concurrency import run_in_threadpool

BOOTSTRAP_FIELDS = frozenset({"auth_required", "auth_providers", "auth_flows"})


def response(value):
    return JSONResponse(
        value, headers={"Cache-Control": "no-store", "Vary": "Cookie, Authorization"}
    )


async def anonymous_status(request, auth):
    """Return a small public response, or allow the existing detailed handler."""
    provider = get_provider("telegram-owner")
    token = extract_bearer(request) or read_session_cookies(request)[0]
    if provider is not None and isinstance(token, str) and 0 < len(token) <= 16384:
        try:
            session = await run_in_threadpool(provider.verify_session, access_token=token)
        except ProviderError:
            session = None
        if session is not None:
            request.state.session = session
            return None
    return response({key: auth[key] for key in BOOTSTRAP_FIELDS if key in auth})
