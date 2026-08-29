import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ModularStructureTests(unittest.TestCase):
    def test_root_main_is_a_thin_compatibility_entry(self):
        entry_path = PROJECT_ROOT / "main.py"
        tree = ast.parse(entry_path.read_text(encoding="utf-8"))

        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]

        self.assertEqual(["Main"], [node.name for node in classes])
        self.assertEqual("SteamStatusMonitorV3", classes[0].bases[0].id)
        self.assertTrue(
            all(isinstance(node, (ast.Expr, ast.Pass)) for node in classes[0].body)
        )
        self.assertTrue(
            any(
                node.module == "src.plugin.steam_status_monitor"
                and node.level == 1
                and any(alias.name == "SteamStatusMonitorV3" for alias in node.names)
                for node in imports
            )
        )

    def test_layered_modules_and_assets_exist(self):
        expected_paths = (
            "src/plugin/steam_status_monitor.py",
            "src/infrastructure/clients/steam.py",
            "src/infrastructure/persistence/plugin_data.py",
            "src/application/services/achievement_monitor.py",
            "src/domain/ranking/push_scopes.py",
            "src/presentation/web/admin_api.py",
            "src/presentation/renderers/game_start.py",
            "src/shared/paths.py",
            "assets/abilities.txt",
            "assets/fonts/NotoSansHans-Regular.otf",
            "assets/images/missingcover.jpg",
        )

        missing = [path for path in expected_paths if not (PROJECT_ROOT / path).is_file()]
        self.assertEqual([], missing)

    def test_plugin_uses_extracted_infrastructure_mixins(self):
        implementation_path = PROJECT_ROOT / "src/plugin/steam_status_monitor.py"
        tree = ast.parse(implementation_path.read_text(encoding="utf-8"))
        plugin_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SteamStatusMonitorV3"
        )

        bases = {base.id for base in plugin_class.bases if isinstance(base, ast.Name)}
        self.assertEqual(
            {
                "StateBackedMonitorMixin",
                "PersistenceMixin",
                "SteamClientMixin",
                "Star",
            },
            bases,
        )

    def test_cross_group_commands_require_admin_permission(self):
        """读取全部群数据的指令必须显式限制为管理员权限。"""

        implementation_path = PROJECT_ROOT / "src/plugin/steam_status_monitor.py"
        tree = ast.parse(implementation_path.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for function_name in ("steam_allrank", "steam_alllist"):
            decorators = [
                ast.unparse(item) for item in functions[function_name].decorator_list
            ]
            self.assertIn(
                "filter.permission_type(filter.PermissionType.ADMIN)", decorators
            )

    def test_push_group_commands_have_one_definition_each(self):
        implementation_path = PROJECT_ROOT / "src/plugin/steam_status_monitor.py"
        tree = ast.parse(implementation_path.read_text(encoding="utf-8"))
        plugin_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SteamStatusMonitorV3"
        )
        method_names = [
            node.name
            for node in plugin_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(1, method_names.count("steam_push_group"))
        self.assertEqual(1, method_names.count("steam_delpush_group"))


if __name__ == "__main__":
    unittest.main()
