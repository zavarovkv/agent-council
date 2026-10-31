#!/usr/bin/env python3
"""Install the Council theme and official-widget bridge into the pinned Hermes build."""

import ast
import hashlib
import sys
from pathlib import Path

from council_team.view import markup

UPSTREAM_SHA256 = "bdd4cadc0b4725b7f133fe4a897e0b6a6c5620f4349614f2c9505e32d07a738d"
UPSTREAM_ROUTES_SHA256 = "b88945b2586de1288875ba1da6d8923d07bf34c1c97b16861e42e7d9633fdd08"
UPSTREAM_STATUS_SHA256 = "91d723e7db56ae3c22c4b98eb964e01ea9e2d11d6e209f612d891a951068449b"


def install_status(path: Path) -> None:
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != UPSTREAM_STATUS_SHA256:
        raise SystemExit("Hermes status handler changed; review diagnostic privacy compatibility.")
    source = original.decode("utf-8")
    node = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_status"
    )
    segment = ast.get_source_segment(source, node)
    updated = segment.replace(
        "async def get_status(profile: Optional[str] = None):",
        "async def get_status(request: Request, profile: Optional[str] = None):",
    )
    lines = updated.splitlines(keepends=True)
    lines[node.body[0].end_lineno - node.lineno + 1 : node.body[0].end_lineno - node.lineno + 1] = [
        "    from plugins.dashboard_auth.telegram_owner.status import anonymous_status, response\n",
        "    public = await anonymous_status(request, _auth_gate_status())\n",
        "    if public is not None:\n",
        "        return public\n",
    ]
    updated = "".join(lines)
    if updated.count("        return status") != 1 or updated == segment:
        raise SystemExit("Expected status privacy hooks were not found.")
    updated = updated.replace("        return status", "        return response(status)")
    source = source.replace(segment, updated, 1)
    compile(source, str(path), "exec")
    path.write_text(source, encoding="utf-8")


def install(renderer: Path, assets: Path) -> None:
    original = renderer.read_bytes()
    routes = renderer.with_name("routes.py")
    routes_source = routes.read_bytes()
    if hashlib.sha256(original).hexdigest() != UPSTREAM_SHA256:
        raise SystemExit("Hermes login renderer changed; review theme compatibility.")
    if hashlib.sha256(routes_source).hexdigest() != UPSTREAM_ROUTES_SHA256:
        raise SystemExit("Hermes auth routes changed; review widget compatibility.")
    # npm ci validates the exact package against package-lock.json integrity.
    beauty = (assets / "dependencies" / "beauty.min.css").read_text(encoding="utf-8")
    login = (assets / "login.html").read_text(encoding="utf-8").replace("$STYLES", beauty)
    widget_script = (
        "<script>\n" + (assets / "telegram-widget.js").read_text(encoding="utf-8") + "\n</script>"
    )
    login = login.replace("$WIDGET_SCRIPT", widget_script)
    login = login.replace("$TEAM_MEMBERS", markup()).replace(
        "$TEAM_SCRIPT",
        "<script>\n" + (assets / "team.js").read_text(encoding="utf-8") + "\n</script>",
    )
    login = login.replace("{", "{{").replace("}", "}}")
    login = login.replace("$PROVIDER_BUTTONS", "{provider_buttons}").replace(
        "$PASSWORD_SCRIPT", "{password_script}"
    )
    empty = (assets / "unavailable.html").read_text(encoding="utf-8").replace("$STYLES", beauty)
    replacements = {"_LOGIN_HTML_TEMPLATE": login, "_EMPTY_HTML": empty}
    source = original.decode("utf-8")
    lines = source.splitlines(keepends=True)
    assignments = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in replacements
    ]
    if len(assignments) != len(replacements):
        raise SystemExit("Expected login templates were not found.")
    for node in reversed(assignments):
        name = node.targets[0].id
        lines[node.lineno - 1 : node.end_lineno] = [f"{name} = {replacements[name]!r}\n"]
    themed = "".join(lines)
    label = "f'Sign in with {html.escape(p.display_name)}</a>'"
    if themed.count(label) != 2:
        raise SystemExit("Expected OAuth fallback labels were not found.")
    themed = themed.replace(label, "f'Войти через {html.escape(p.display_name)}</a>'")
    compile(themed, str(renderer), "exec")
    renderer.write_text(themed, encoding="utf-8")
    logout = "    _at, rt = read_session_cookies(request)\n    # Best-effort revoke"
    updated_routes = routes_source.decode("utf-8")
    if updated_routes.count(logout) != 1:
        raise SystemExit("Expected logout hook was not found.")
    updated_routes = updated_routes.replace(
        logout,
        "    _at, rt = read_session_cookies(request)\n"
        "    await _council_revoke_access_cookie(_at)\n    # Best-effort revoke",
    )
    updated_routes += (
        "\n\nfrom plugins.dashboard_auth.telegram_owner.widget import router as _council_widget_router\n"
        "from plugins.dashboard_auth.telegram_owner.widget import revoke_access_cookie as _council_revoke_access_cookie\n"
        "router.include_router(_council_widget_router)\n"
        "from plugins.dashboard_auth.telegram_owner.team import router as _council_team_router\n"
        "router.include_router(_council_team_router)\n"
    )
    compile(updated_routes, str(routes), "exec")
    routes.write_text(updated_routes, encoding="utf-8")
    install_status(renderer.parents[1] / "web_routers/status.py")
    print("Council theme and official Telegram Login bridge installed.")


if __name__ == "__main__":
    install(Path(sys.argv[1]), Path(sys.argv[2]))
