"""Load the two operator-authored skills shipped in the reviewed image."""

from pathlib import Path

AUDIENCES = frozenset({"contacts", "new-users"})


def load_skills(root):
    skills = {}
    for audience in sorted(AUDIENCES):
        path = Path(root) / audience / "SKILL.md"
        if path.is_symlink():
            raise ValueError("Refusing a symlinked secretary skill.")
        text = path.read_text(encoding="utf-8")
        # Metadata describes the skill; only its Markdown body becomes instructions.
        parts = text.split("---\n", 2)
        if len(parts) != 3 or parts[0] or not 0 < len(parts[2].strip()) <= 16000:
            raise ValueError("A secretary skill needs front matter and a nonempty Markdown body.")
        skills[audience] = parts[2].strip()
    return skills
