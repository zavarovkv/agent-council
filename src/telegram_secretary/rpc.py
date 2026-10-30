"""Authenticated fixed-destination RPC; never forward credentials or caller proxies."""

import json
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_service(key, path, payload):
    request = urllib.request.Request(
        "http://secretary:9120" + path,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    # Ignore caller proxy configuration and never forward this key through a redirect.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=55) as response:
        data = response.read(20000)
    return json.loads(data)


def acknowledge_reply(key, identifier):
    request_service(key, "/ack", {"request_id": identifier})


def request_reply(key, payload):
    value = request_service(key, "/reply", payload)
    text = value.get("reply") if isinstance(value, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError("Invalid secretary response.")
    return text
