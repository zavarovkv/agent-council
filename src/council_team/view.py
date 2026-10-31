"""Build an allowlisted public snapshot without prompts, issue text or credentials."""

import html
import json
import sqlite3
import time
from contextlib import closing

from .catalog import ROLES

PROFILES = {"default": "qui-gon", "dexter": "dexter", "lando": "marketing", "padme": "padme"}
TITLES = {
    "qui-gon": "Ассистент и координатор",
    "dexter": "Продуктовый исследователь",
    "marketing": "Маркетинг и SEO",
    "padme": "Редактор",
}
AVATARS = {
    "qui-gon": "qui-gon.png",
    "dexter": "dexter.png",
    "lando": "lando.png",
    "padme": "padme.png",
}
ENGLISH = {
    "qui-gon": (
        "Assistant and coordinator",
        "Manages tasks through completion, delegates work, reviews results and clarifies decisions with the owner.",
    ),
    "dexter": (
        "Product researcher",
        "Studies competitors, markets, products, pricing and customer needs; verifies primary sources and separates facts from hypotheses.",
    ),
    "marketing": (
        "Marketing and SEO",
        "Positioning, audience research, SEO, content, distribution and growth experiments with measured results.",
    ),
    "padme": (
        "Editor",
        "Edits Russian texts, preserves the author's voice and prepares clear, natural copy for websites, publications and executives.",
    ),
}
PUBLIC_SLUGS = {slug: "lando" if slug == "marketing" else slug for slug in ROLES}
MODELS = {
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-6.1-terra": "GPT-6.1 Terra",
    "gpt-6-luna": "GPT-6 Luna",
}
EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


def regular(path, limit):
    return path.resolve() == path and path.is_file() and path.stat().st_size <= limit


def selected(path, read_config):
    try:
        if not regular(path, 1024 * 1024):
            return None
        config = read_config(path)
        model = config.get("model", {}).get("default")
        effort = config.get("agent", {}).get("reasoning_effort")
        if model not in MODELS or effort not in EFFORTS:
            return None
        return {"name": MODELS[model], "effort": effort}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def token_counts(home):
    totals = {slug: 0 for slug in ROLES}
    available = {slug: True for slug in ROLES}
    seen = set()
    deadline = time.monotonic() + 2
    databases = [(home / "state.db", "qui-gon", True)]
    databases += [
        (home / "profiles" / name / "state.db", slug, False)
        for name, slug in PROFILES.items()
        if name != "default"
    ]
    databases += [(home / ".council/linear" / slug / "state.db", slug, False) for slug in ROLES]
    for path, owner, shared in databases:
        try:
            if not regular(path, 2 * 1024**3):
                raise ValueError("No regular session database.")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                db.execute("PRAGMA query_only=ON")
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                # Cache/reasoning are already part of OpenAI input/output totals.
                rows = db.execute("SELECT id,profile_name,input_tokens,output_tokens FROM sessions")
                for identity, profile, incoming, outgoing in rows:
                    if identity in seen:
                        continue
                    seen.add(identity)
                    slug = PROFILES.get(profile) if shared else owner
                    if slug not in totals:
                        continue
                    if any(type(value) is not int or value < 0 for value in (incoming, outgoing)):
                        available[slug] = False
                    else:
                        totals[slug] += incoming + outgoing
        except (OSError, ValueError, sqlite3.Error):
            for slug in ROLES if shared else (owner,):
                available[slug] = False
    return {slug: totals[slug] if available[slug] else None for slug in ROLES}


def issue_counts(home, now):
    path = home / ".council/linear/bridge/public-team.json"
    try:
        if not regular(path, 8192):
            raise ValueError("Missing aggregate.")
        value = json.loads(path.read_text())
        timestamp = value["updated_at"]
        if (
            value["schema"] != 1
            or type(timestamp) not in {int, float}
            or not 0 <= now - timestamp <= 900
        ):
            raise ValueError("Stale aggregate.")
        counts, connected = value["counts"], value["connected"]
        if set(counts) != set(ROLES) or set(connected) != set(ROLES):
            raise ValueError("Unexpected public roles.")
        for slug in ROLES:
            if type(connected[slug]) is not bool or not (
                counts[slug] is None or type(counts[slug]) is int and 0 <= counts[slug] <= 10000
            ):
                raise ValueError("Invalid aggregate count.")
        return counts, connected
    except (OSError, ValueError, KeyError, TypeError):
        return {slug: None for slug in ROLES}, {slug: None for slug in ROLES}


def snapshot(home, read_config, *, now=None):
    now = time.time() if now is None else now
    counts, connected = issue_counts(home, now)
    tokens = token_counts(home)
    members = []
    for name, slug in PROFILES.items():
        root = home if name == "default" else home / "profiles" / name
        members.append(
            {
                "slug": PUBLIC_SLUGS[slug],
                "name": ROLES[slug]["name"],
                "title": TITLES[slug],
                "model": selected(root / "config.yaml", read_config),
                "linear_model": selected(
                    home / ".council/linear" / slug / "config.yaml", read_config
                ),
                "closed_tasks": counts[slug],
                "tokens": tokens[slug],
                "linear_connected": connected[slug],
            }
        )
    return {"schema": 1, "agents": members}


def markup():
    cards = []
    for slug, role in ROLES.items():
        cards.append(
            f'<article data-agent="{PUBLIC_SLUGS[slug]}">'
            f'<img data-avatar="{PUBLIC_SLUGS[slug]}" width="96" height="96" alt="{html.escape(role["name"])}" loading="lazy">'
            f"<h2>{html.escape(role['name'])}</h2><p>"
            f'<strong data-en="{html.escape(ENGLISH[slug][0])}">{TITLES[slug]}</strong></p>'
            f'<p data-en="{html.escape(ENGLISH[slug][1])}">{html.escape(role["specialty"])}</p>'
            '<p><span data-en="Model:">Модель:</span> <strong data-field="model">—</strong></p>'
            '<p><span data-en="Completed tasks:">Завершено задач:</span> <strong data-field="closed_tasks">—</strong> · '
            '<span data-en="Tokens:">Токены:</span> <strong data-field="tokens">—</strong></p>'
            '<p><small data-field="connection"></small></p></article>'
        )
    return "\n".join(cards)
