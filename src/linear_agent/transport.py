"""Fixed-destination, bounded JSON transport with no redirects or caller proxies."""

import json
import urllib.error
import urllib.request


class RemoteError(RuntimeError):
    def __init__(self, status=0):
        self.status = status
        super().__init__("Integration request failed; private diagnostics omitted.")


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
        return json.loads(data)
    except urllib.error.HTTPError as error:
        raise RemoteError(error.code) from None
    except Exception:
        raise RemoteError() from None
