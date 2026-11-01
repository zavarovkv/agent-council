"""Read reviewed profile-local skill text without file, shell or skill-write tools."""

import json
import os
from pathlib import Path, PurePosixPath

from council_skills import MAX_BYTES, NAME, metadata


def regular_text(root, relative):
    path = root / relative
    if (
        root.resolve() != root
        or path.resolve() != path
        or not path.is_file()
        or path.stat().st_size > MAX_BYTES
    ):
        raise ValueError("Expected bounded regular skill text.")
    return path.read_text(encoding="utf-8")


def view(args):
    try:
        root = Path(os.environ["HERMES_HOME"]) / "skills"
        name = args.get("name", "")
        if set(args) - {"name", "path"}:
            raise ValueError("Invalid skill arguments.")
        if not name:
            if args.get("path"):
                raise ValueError("Select a skill first.")
            entries = []
            # Only explicitly installed top-level skills, never arbitrary profile files.
            for folder in sorted(root.iterdir()) if root.exists() else []:
                if not NAME.fullmatch(folder.name):
                    continue
                # Hermes also seeds categorized upstream skills at startup. Their
                # category directories (and old empty directories) are not skills.
                skill_file = folder / "SKILL.md"
                if not skill_file.exists() and not skill_file.is_symlink():
                    continue
                content = regular_text(root, folder.name + "/SKILL.md")
                if len(entries) >= 128:
                    raise ValueError("Invalid skill catalog.")
                entries.append(metadata(content, folder.name))
            return json.dumps({"skills": entries}, ensure_ascii=False)
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Invalid skill name.")
        relative = args.get("path", "SKILL.md")
        if not isinstance(relative, str):
            raise ValueError("Invalid reference path.")
        parts = PurePosixPath(relative).parts
        if relative != "SKILL.md" and (
            len(parts) < 2
            or parts[0] != "references"
            or str(PurePosixPath(relative)) != relative
            or any(part.startswith(".") for part in parts)
            or Path(relative).suffix != ".md"
        ):
            raise ValueError("Only skill instructions and Markdown references are readable.")
        metadata(regular_text(root, name + "/SKILL.md"), name)
        return json.dumps(
            {"name": name, "path": relative, "content": regular_text(root, name + "/" + relative)},
            ensure_ascii=False,
        )
    except (OSError, ValueError, TypeError, KeyError):
        return json.dumps({"error": "Reviewed skill text is unavailable for this profile."})
