#!/usr/bin/env python3
"""Decorate the pinned Hermes login renderer without replacing its auth logic."""

import ast
import hashlib
import json
import sys
from pathlib import Path

UPSTREAM_SHA256 = "bdd4cadc0b4725b7f133fe4a897e0b6a6c5620f4349614f2c9505e32d07a738d"


def install(renderer: Path, assets: Path) -> None:
    original = renderer.read_bytes()
    if hashlib.sha256(original).hexdigest() != UPSTREAM_SHA256:
        raise SystemExit(
            "Hermes login renderer changed; review theme compatibility before building."
        )
    vendor = assets / "vendor"
    receipt = json.loads((vendor / "beauty-css.json").read_text(encoding="utf-8"))
    beauty = (vendor / "beauty.min.css").read_bytes()
    if hashlib.sha256(beauty).hexdigest() != receipt["sha256"]:
        raise SystemExit("Vendored Beauty.css checksum does not match its receipt.")
    styles = beauty.decode("utf-8") + "\n" + (assets / "login.css").read_text(encoding="utf-8")
    login = (assets / "login.html").read_text(encoding="utf-8").replace("$STYLES", styles)
    # Hermes formats its HTML with str.format: escape literal CSS braces first.
    login = login.replace("{", "{{").replace("}", "}}")
    login = login.replace("$PROVIDER_BUTTONS", "{provider_buttons}").replace(
        "$PASSWORD_SCRIPT", "{password_script}"
    )
    empty = (assets / "unavailable.html").read_text(encoding="utf-8").replace("$STYLES", styles)
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
    # Translate only OAuth link labels. URL generation and verification stay upstream.
    label = "f'Sign in with {html.escape(p.display_name)}</a>'"
    if themed.count(label) != 2:
        raise SystemExit("Expected OAuth link labels were not found.")
    themed = themed.replace(label, "f'Войти через {html.escape(p.display_name)}</a>'")
    compile(themed, str(renderer), "exec")
    renderer.write_text(themed, encoding="utf-8")
    print("Agent Council login installed with pinned Beauty.css; auth handlers preserved.")


if __name__ == "__main__":
    install(Path(sys.argv[1]), Path(sys.argv[2]))
