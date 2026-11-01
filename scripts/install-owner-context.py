#!/usr/bin/env python3
"""Narrow compatibility extension for the pinned Hermes session protocol.

Upstream has no SDK for registering a local-control session surface. Keep these
five hooks explicit, hash guarded and covered against the real installed SDK.
Never use this installer on a running gateway or mutate persistent profiles here.
"""

import hashlib
import sys
from pathlib import Path

HASHES = {
    "hermes_state_local_migration.py": "0f9bb24b924d41b9d0bdaeeeb4257232c14800754e529f6a2e36908acc9ea123",
    "gateway/session_policy.py": "f8daa799f7b2c16fb936863cfdac04b2b01959184121cec70dd220d8678a7b74",
    "gateway/session_policy_refresh.py": "6a013790d4765a85a7412c755725c4644cb2e2273001e7287b34491dc9237771",
    "hermes_state_mutation_prepared.py": "1f46b883d5705b8934cad50103fe0af0b6434ef078ae488d1c2c930a682c1edc",
    "gateway/run_turn_runner.py": "04f76f4857a8ba251b014d615b814f3920fef8212ad24e69b28fbbc4fdf2df39",
}

REPLACEMENTS = {
    "hermes_state_local_migration.py": [
        (
            "'desktop', 'oneshot', 'tool', 'acp'}",
            "'desktop', 'oneshot', 'tool', 'acp', 'telegram_guest'}",
        ),
    ],
    "gateway/session_policy.py": [
        ("'oneshot': 'cli'}", "'oneshot': 'cli', 'telegram_guest': 'telegram'}"),
        (
            "'acp' if source == 'acp' else 'cli'",
            "'telegram' if source == 'telegram_guest' else ('acp' if source == 'acp' else 'cli')",
        ),
    ],
    "gateway/session_policy_refresh.py": [
        (
            "        base = follow_bot_chat(authority, session_id, old, live, picked)",
            "        base = follow_bot_chat(authority, session_id, old, live, picked)\n"
            "        from council_owner.migration import adopt_guest_policy\n"
            "        base = adopt_guest_policy(authority, session_id, base, picked)",
        ),
    ],
    "hermes_state_mutation_prepared.py": [
        (
            "        return _commit_policy(db, conn, session_id, {'snapshot': snapshot, 'policy': new})[1]",
            "        from council_owner.migration import invalidate_adopted_prompt\n"
            "        invalidate_adopted_prompt(conn, session_id, snapshot['target'], old, new)\n"
            "        return _commit_policy(db, conn, session_id, {'snapshot': snapshot, 'policy': new})[1]",
        ),
    ],
    "gateway/run_turn_runner.py": [
        (
            "        combined_ephemeral = self._combined_ephemeral_prompt()",
            "        combined_ephemeral = self._combined_ephemeral_prompt()\n"
            "        from council_owner.context import audience_prompt\n"
            "        combined_ephemeral = audience_prompt(policy, combined_ephemeral)",
        ),
    ],
}


def install(root):
    prepared = {}
    for relative, changes in REPLACEMENTS.items():
        path = root / relative
        original = path.read_bytes()
        if hashlib.sha256(original).hexdigest() != HASHES[relative]:
            raise SystemExit(f"Hermes {relative} changed; review owner-context compatibility.")
        source = original.decode()
        for before, after in changes:
            expected = 2 if before == "'acp' if source == 'acp' else 'cli'" else 1
            if source.count(before) != expected:
                raise SystemExit(f"Unexpected owner-context hook in {relative}.")
            source = source.replace(before, after)
        compile(source, str(path), "exec")
        prepared[path] = source
    # Validate every source before changing any file; image build fails closed.
    for path, source in prepared.items():
        path.write_text(source)


if __name__ == "__main__":
    install(Path(sys.argv[1]))
