"""Load operator-authored reply skills shipped in the reviewed image."""

from dataclasses import dataclass
from pathlib import Path

AUDIENCES = frozenset({"spouse", "contacts", "new-users"})


@dataclass(frozen=True)
class Instructions:
    policy: str
    skills: dict[str, str]


def load_instructions(root):
    path = Path(root) / "POLICY.md"
    if path.is_symlink():
        raise ValueError("Refusing a symlinked secretary policy.")
    policy = path.read_text(encoding="utf-8").strip()
    if not 0 < len(policy) <= 16000:
        raise ValueError("A nonempty secretary policy is required.")
    return Instructions(policy, load_skills(Path(root) / "skills"))


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
