"""Authorization tests run without Hermes, network access or real credentials."""

import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telegram_auth.owner import OwnerDenied, OwnerPolicy, parse_telegram_ids, telegram_id


class TelegramOwnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pin = Path(self.directory.name) / "telegram-owner.json"
        self.policy = OwnerPolicy(self.pin, "@zavarov")

    def test_verified_owner_is_pinned_with_private_permissions(self):
        self.assertEqual(
            self.policy.authorize({"id": 12345, "preferred_username": "zavarov"}), 12345
        )
        self.assertEqual(json.loads(self.pin.read_text())["telegram_id"], 12345)
        self.assertEqual(os.stat(self.pin).st_mode & 0o777, 0o600)

    def test_different_username_cannot_bootstrap_access(self):
        with self.assertRaises(OwnerDenied):
            self.policy.authorize({"id": 99999, "preferred_username": "someone_else"})
        self.assertFalse(self.pin.exists())

    def test_username_without_verified_telegram_id_is_rejected(self):
        with self.assertRaises(OwnerDenied):
            self.policy.authorize({"preferred_username": "zavarov"})
        self.assertFalse(self.pin.exists())

    def test_reassigned_username_cannot_access_pinned_account(self):
        self.policy.authorize({"id": 12345, "preferred_username": "zavarov"})
        with self.assertRaises(OwnerDenied):
            self.policy.authorize({"id": 99999, "preferred_username": "zavarov"})

    def test_owner_can_change_username_after_account_is_pinned(self):
        self.policy.authorize({"id": 12345, "preferred_username": "zavarov"})
        self.assertEqual(
            self.policy.authorize({"id": 12345, "preferred_username": "new_name"}), 12345
        )

    def test_existing_pin_survives_a_new_provider_instance(self):
        self.policy.authorize({"id": 12345, "preferred_username": "zavarov"})
        restarted = OwnerPolicy(self.pin, "zavarov")
        with self.assertRaises(OwnerDenied):
            restarted.authorize({"id": 99999, "preferred_username": "zavarov"})

    def test_corrupt_identity_file_denies_login(self):
        self.pin.write_text("{broken", encoding="utf-8")
        with self.assertRaises(OwnerDenied):
            self.policy.authorize({"id": 12345, "preferred_username": "zavarov"})

    def test_explicit_account_id_does_not_depend_on_username(self):
        policy = OwnerPolicy(self.pin, "", "12345")
        self.assertEqual(policy.authorize({"id": "12345"}), 12345)
        with self.assertRaises(OwnerDenied):
            policy.authorize({"id": 99999, "preferred_username": "zavarov"})

    def test_concurrent_logins_publish_one_complete_pin(self):
        claims = {"id": 12345, "preferred_username": "zavarov"}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.policy.authorize, [claims] * 8))
        self.assertEqual(results, [12345] * 8)
        self.assertEqual(json.loads(self.pin.read_text())["telegram_id"], 12345)

    def test_missing_owner_configuration_is_rejected(self):
        with self.assertRaises(OwnerDenied):
            OwnerPolicy(self.pin, "")

    def test_invalid_account_ids_are_rejected(self):
        for value in (None, True, False, 0, -1, 1.5, "1.5", "-1", "", "１２３", 2**63):
            with self.subTest(value=value), self.assertRaises(OwnerDenied):
                telegram_id(value)

    def test_invited_account_can_enter_without_pinning_the_owner(self):
        policy = OwnerPolicy(self.pin, "zavarov", additional_ids="67890, 54321")
        self.assertEqual(policy.authorize({"id": 67890, "preferred_username": "guest_one"}), 67890)
        self.assertFalse(self.pin.exists())
        self.assertEqual(policy.authorize({"id": 12345, "preferred_username": "zavarov"}), 12345)
        self.assertEqual(json.loads(self.pin.read_text())["telegram_id"], 12345)
        with self.assertRaises(OwnerDenied):
            policy.authorize({"id": 99999, "preferred_username": "guest_two"})

    def test_invited_account_does_not_replace_an_existing_owner(self):
        self.policy.authorize({"id": 12345, "preferred_username": "zavarov"})
        policy = OwnerPolicy(self.pin, "zavarov", additional_ids="67890")
        self.assertEqual(policy.authorize({"id": 67890}), 67890)
        self.assertEqual(json.loads(self.pin.read_text())["telegram_id"], 12345)
        self.assertEqual(policy.authorize({"id": 12345}), 12345)

    def test_removed_invitation_is_denied_after_reloading_settings(self):
        invited = OwnerPolicy(self.pin, "", "12345", additional_ids="67890,54321")
        self.assertEqual(invited.authorize({"id": 67890}), 67890)
        updated = OwnerPolicy(self.pin, "", "12345", additional_ids="54321")
        with self.assertRaises(OwnerDenied):
            updated.authorize({"id": 67890})
        self.assertEqual(updated.authorize({"id": 54321}), 54321)
        self.assertEqual(updated.authorize({"id": 12345}), 12345)

    def test_access_list_is_strict_and_normalizes_duplicates(self):
        self.assertEqual(parse_telegram_ids("67890, 54321,67890"), frozenset({54321, 67890}))
        self.assertEqual(parse_telegram_ids("  "), frozenset())
        for value in (None, "0", "-1", "1.5", "12345,", ",12345", "12345,,67890", "@user"):
            with self.subTest(value=value), self.assertRaises(OwnerDenied):
                parse_telegram_ids(value)


if __name__ == "__main__":
    unittest.main()
