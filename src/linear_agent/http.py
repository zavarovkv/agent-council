"""Public signed webhooks/OAuth and a separate, unpublished executor-tool listener."""

import hmac
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import private_json
from .transport import RemoteError


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, service, *, internal=False):
        self.service, self.internal = service, internal
        self.connections = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, *_args):
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # OAuth codes, webhook bodies and tool arguments never enter access logs.

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def respond(self, status, value=None, *, headers=None):
        body = b"" if value is None else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        for name, item in (headers or {}).items():
            self.send_header(name, item)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def read_body(self):
        length = self.headers.get("Content-Length", "")
        if (
            self.headers.get("Transfer-Encoding")
            or not length.isdigit()
            or not 0 < int(length) <= 131072
            or self.headers.get_content_type() != "application/json"
        ):
            raise ValueError("Bounded JSON required.")
        body = self.rfile.read(int(length))
        if len(body) != int(length):
            raise ValueError("Incomplete request.")
        return body

    def route(self):
        parsed = urlsplit(self.path)
        parts = parsed.path.strip("/").split("/")
        if self.server.internal:
            if len(parts) == 2:
                return parts[0], parts[1], parsed
        elif len(parts) >= 4 and parts[:2] == ["integrations", "linear"]:
            return parts[2], "/".join(parts[3:]), parsed
        return "", "", parsed

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok"})
            return
        try:
            slug, action, parsed = self.route()
            registry = self.server.service.registry()
            agent = registry.agents[slug]
            if self.server.internal:
                self.respond(404, {"error": "not_found"})
                return
            cookie_name = "__Secure-council-linear-" + slug
            cookie_path = "/integrations/linear/" + slug + "/oauth/"
            if action == "oauth/authorize":
                setup = private_json(self.server.service.home / "setup.json", limit=4096)
                if (
                    set(setup) != {"expires"}
                    or type(setup["expires"]) not in {int, float}
                    or not time.time() < setup["expires"] <= time.time() + 1800
                ):
                    raise PermissionError("Open a short setup window through the host CLI first.")
                browser = secrets.token_urlsafe(32)
                location = self.server.service.linear.authorization(registry, agent, browser)
                self.respond(
                    303,
                    headers={
                        "Location": location,
                        "Set-Cookie": f"{cookie_name}={browser}; Path={cookie_path}; Max-Age=600; HttpOnly; Secure; SameSite=Lax",
                    },
                )
            elif action == "oauth/callback":
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) != {"state", "code"} or any(
                    len(values) != 1 or not 1 <= len(values[0]) <= 2048 for values in query.values()
                ):
                    raise ValueError("Invalid OAuth callback.")
                cookies = SimpleCookie()
                cookies.load(self.headers.get("Cookie", ""))
                browser = cookies[cookie_name].value
                self.server.service.linear.callback(
                    registry, agent, state=query["state"][0], browser=browser, code=query["code"][0]
                )
                self.respond(
                    303,
                    headers={
                        "Location": registry.origin + f"/integrations/linear/{slug}/connected",
                        "Set-Cookie": f"{cookie_name}=; Path={cookie_path}; Max-Age=0; HttpOnly; Secure; SameSite=Lax",
                    },
                )
            elif action == "connected":
                self.respond(
                    200,
                    {
                        "message": "Application installed. Complete model setup and enable execution on the host."
                    },
                )
            else:
                self.respond(404, {"error": "not_found"})
        except PermissionError:
            self.respond(403, {"error": "setup_not_authorized"})
        except BlockingIOError:
            self.respond(429, {"error": "setup_busy"})
        except (KeyError, ValueError, TypeError, OSError):
            self.respond(400, {"error": "setup_unavailable"})
        except RemoteError:
            self.respond(503, {"error": "provider_unavailable"})

    def do_POST(self):
        try:
            slug, action, parsed = self.route()
            if parsed.query:
                raise ValueError("Query parameters are not accepted.")
            if self.server.internal:
                registry = self.server.service.registry()
                agent = registry.agents[slug]
                expected = ("Bearer " + agent.executor_key).encode()
                if not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode(), expected
                ):
                    self.respond(401, {"error": "unauthorized"})
                    return
                payload = json.loads(self.read_body())
                if not isinstance(payload, dict):
                    raise ValueError("Object required.")
                if action == "delegate":
                    result = self.server.service.delegate(slug, payload)
                elif action == "children":
                    result = self.server.service.child_tasks(slug, payload)
                elif action == "agents":
                    result = self.server.service.agent_catalog(slug, payload)
                elif action == "issues":
                    try:
                        result = self.server.service.issue_tasks(slug, payload)
                    except (ValueError, PermissionError) as error:
                        self.respond(
                            403 if isinstance(error, PermissionError) else 400,
                            {"error": str(error)},
                        )
                        return
                else:
                    self.respond(404, {"error": "not_found"})
                    return
                self.respond(200, result)
            elif action == "webhook":
                signature = self.headers.get("Linear-Signature", "")
                if len(self.headers.get_all("Linear-Signature") or []) != 1:
                    raise PermissionError("Expected one signature.")
                accepted = self.server.service.ingest(slug, self.read_body(), signature)
                self.respond(200, {"accepted": accepted})
            else:
                self.respond(404, {"error": "not_found"})
        except PermissionError:
            self.respond(403, {"error": "not_authorized"})
        except BlockingIOError:
            self.respond(503, {"error": "temporarily_unavailable"}, headers={"Retry-After": "30"})
        except (KeyError, ValueError, TypeError, OSError):
            self.respond(400, {"error": "invalid_request"})
        except RemoteError:
            self.respond(503, {"error": "operation_uncertain"})
