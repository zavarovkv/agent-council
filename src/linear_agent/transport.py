"""Fixed-destination, bounded JSON transport with no redirects or caller proxies."""

import json
import math
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime


class RemoteError(RuntimeError):
    def __init__(self, status=0, *, retry_at=0, headers=None):
        self.status = status
        self.retry_at = retry_at
        self.headers = headers or {}
        super().__init__("Integration request failed; private diagnostics omitted.")


def rate_headers(headers):
    return {
        name.lower(): value
        for name, value in headers.items()
        if name.lower() == "retry-after" or name.lower().startswith("x-ratelimit-")
    }


def retry_deadline(headers, now):
    """Honor Retry-After and exhausted Linear budgets (reset is epoch milliseconds)."""
    deadlines = [0]
    value = headers.get("retry-after", "")
    try:
        delay = float(value)
        if math.isfinite(delay) and delay >= 0:
            deadlines.append(now + delay)
    except (ValueError, TypeError):
        try:
            deadlines.append(parsedate_to_datetime(value).timestamp())
        except (ValueError, TypeError, OverflowError):
            pass
    for budget in ("requests", "complexity", "endpoint-requests"):
        prefix = "x-ratelimit-" + budget
        try:
            if float(headers.get(prefix + "-remaining", "1")) <= 0:
                reset = float(headers[prefix + "-reset"]) / 1000
                if math.isfinite(reset):
                    deadlines.append(reset + 1)
        except (KeyError, ValueError, TypeError):
            pass
    return max(deadlines)


class JsonResponse(dict):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = rate_headers(headers)


def rate_limited(value):
    errors = value.get("errors") if isinstance(value, dict) else None
    return isinstance(errors, list) and any(
        isinstance(error, dict)
        and isinstance(error.get("extensions"), dict)
        and error["extensions"].get("code") in ("RATELIMITED", "RATE_LIMITED")
        for error in errors
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_json(url, *, method="GET", body=None, headers=None, timeout=5):
    data = (
        body
        if isinstance(body, bytes)
        else None
        if body is None
        else json.dumps(body, ensure_ascii=False).encode()
    )
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            data = response.read(1048577)
            if len(data) > 1048576:
                raise ValueError("Oversized integration response")
            value = json.loads(data)
            return JsonResponse(value, response.headers) if isinstance(value, dict) else value
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            body = error.read(1048577)
            if len(body) <= 1048576 and rate_limited(json.loads(body)):
                status = 429
        except (ValueError, OSError):
            pass
        finally:
            error.close()
        raise RemoteError(status, headers=rate_headers(error.headers)) from None
    except Exception:
        raise RemoteError() from None
