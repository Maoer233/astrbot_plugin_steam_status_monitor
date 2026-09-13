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
            "src/plugin/runtime_config.py",
            "src/infrastructure/clients/steam.py",
            "src/infrastructure/persistence/plugin_data.py",
            "src/application/services/achievement_monitor.py",
            "src/application/services/session_service.py",
            "src/application/services/ranking.py",
            "src/application/services/price_query.py",
            "src/application/services/monitor_control.py",
            "src/application/services/player_status_view.py",
            "src/application/services/rank_view.py",
            "src/domain/monitoring/game_filter.py",
            "src/domain/monitoring/session.py",
            "src/domain/ranking/push_scopes.py",
            "src/presentation/commands/__init__.py",
            "src/presentation/commands/monitor.py",
            "src/presentation/commands/store.py",
            "src/presentation/commands/rank.py",
            "src/presentation/commands/ops.py",
            "src/presentation/web/admin_api.py",
            "src/presentation/renderers/game_start.py",
            "src/presentation/renderers/image_crop.py",
            "src/presentation/renderers/superpower.py",
            "src/shared/paths.py",
            "assets/abilities.txt",
            "assets/fonts/manifest.json",
            "assets/fonts/.gitkeep",
            "src/shared/fonts.py",
            "src/infrastructure/fonts/pack_service.py",
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
                "QQMenuManagementMixin",
                "PollingTrackingMixin",
                "StatusChangeTrackingMixin",
                "SessionQuitMixin",
                "NotificationTrackingMixin",
                "AchievementTrackingMixin",
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
        plugin_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SteamStatusMonitorV3"
        )
        functions = {
            node.name: node
            for node in plugin_class.body
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

    def test_composition_root_does_not_keep_passthrough_helpers(self):
        text = (PROJECT_ROOT / "src/plugin/steam_status_monitor.py").read_text(encoding="utf-8")
        forbidden = (
            "def crop_image_auto",
            "def get_today_superpower",
            "async def get_game_online_count",
            "def _steam_parent",
            "def _get_rank_data",
            "def _record_playtime",
            "def _should_skip_game",
            "async def _daily_rank_push",
            "async def _translate_game_query",
        )
        hits = [token for token in forbidden if token in text]
        self.assertEqual([], hits)

    def test_command_modules_do_not_own_core_rules(self):
        commands_dir = PROJECT_ROOT / "src/presentation/commands"
        forbidden = ("play_records[", "ITAD_CLIENT", "httpx")
        hits = []
        for path in commands_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    hits.append(f"{path.name}: {token}")
        self.assertEqual([], hits)


if __name__ == "__main__":
    unittest.main()
