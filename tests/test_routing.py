import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_ops.config import OperationError
from council_ops.routing import configure


class RoutingTests(unittest.TestCase):
    def test_invalid_candidate_restores_the_previous_route_before_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            source = root / "config/nginx-council.conf"
            target = root / "council.conf"
            source.write_text("invalid candidate")
            target.write_text("previous valid route")
            target.chmod(0o644)
            runtime = Mock()
            runtime.settings.repository = root
            runtime.settings.env_file = root / "compose.env"
            runtime.settings.values = {"HERMES_PUBLIC_URL": "https://council.zavarov.com"}
            checks = []

            def run(command):
                checks.append((command, target.read_text()))
                if target.read_text() == "invalid candidate":
                    raise OperationError("Fixture syntax failure")

            runtime.run.side_effect = run
            with self.assertRaises(OperationError):
                configure(runtime, target=target)
            self.assertEqual(target.read_text(), "previous valid route")
            self.assertEqual(
                (root / "nginx-council.previous.conf").read_text(), "previous valid route"
            )
            self.assertEqual(checks[-1], (["systemctl", "reload", "nginx"], "previous valid route"))
            runtime.check_protected.assert_not_called()
