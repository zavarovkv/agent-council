import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from council_ops.config import OperationError
from council_ops.profile_setup import configure


class ProfileSetupTests(unittest.TestCase):
    def test_failed_copy_restores_the_pre_stop_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Mock()
            runtime.settings = SimpleNamespace(data_dir=Path(directory))
            backup = Path(directory) / "checkpoint"
            with (
                patch("council_ops.profile_setup.verify_capabilities"),
                patch("council_ops.profile_setup.identity", return_value=(10000, 10000)),
                patch("council_ops.profile_setup.snapshot", return_value=backup) as snapshot,
                patch("council_ops.profile_setup.smoke", side_effect=OperationError("copy failed")),
                patch("council_ops.profile_setup.restore") as restore,
            ):
                with self.assertRaisesRegex(OperationError, "previous state restored"):
                    configure(
                        runtime,
                        capability="fixture",
                        script="fixture.py",
                        result_key="configured",
                        probe="pass",
                    )
            snapshot.assert_called_once_with(runtime, restart=False, automatic=True)
            restore.assert_called_once_with(runtime, backup, require_idle=False)
            runtime.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
