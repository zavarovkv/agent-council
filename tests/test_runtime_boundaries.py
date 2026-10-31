"""Import boundaries guard against accidentally adding private execution to guest code."""

import ast
import sys
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"


class RuntimeBoundaryTests(unittest.TestCase):
    def check_imports(self, package, external):
        for path in (SOURCE / package).glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    modules = [node.module]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    continue
                for module in modules:
                    if module.split(".")[0] in sys.stdlib_module_names:
                        continue
                    with self.subTest(module=module, file=path.name):
                        self.assertIn(module, external.get(path.name, set()))

    def test_worker_has_no_telegram_host_operations_or_agent_executor_imports(self):
        self.check_imports(
            "secretary_worker",
            {
                "model.py": {"agent.auxiliary_client", "hermes_cli.config"},
            },
        )

    def test_gateway_uses_the_worker_only_through_rpc(self):
        self.check_imports(
            "telegram_secretary",
            {
                "plugin.py": {"telegram", "telegram.ext"},
                "bridge.py": {"telegram"},
                "public.py": {"telegram"},
                "guest.py": {"telegram"},
                "menus.py": {"telegram", "hermes_cli.commands_platforms"},
            },
        )

    def test_linear_bridge_cannot_import_the_personal_agent_or_host_operations(self):
        self.check_imports("linear_agent", {})
        self.check_imports("linear_tools", {"__init__.py": {"gateway.session_context"}})
