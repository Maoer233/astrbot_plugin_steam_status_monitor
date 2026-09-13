import tempfile
import unittest
from pathlib import Path

from src.application.services.steam_list import list_parent
from src.presentation.renderers.superpower import SuperpowerPicker


class SuperpowerPickerTests(unittest.TestCase):
    def test_same_steamid_and_day_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "abilities.txt"
            path.write_text("飞行\n隐身\n读心\n", encoding="utf-8")
            picker = SuperpowerPicker(path)
            first = picker.get("sid-1", today="2026-09-12")
            second = picker.get("sid-1", today="2026-09-12")
            self.assertEqual(first, second)
            self.assertIn(first, {"飞行", "隐身", "读心"})

    def test_clear_drops_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "abilities.txt"
            path.write_text("飞行\n", encoding="utf-8")
            picker = SuperpowerPicker(path)
            picker.get("sid-1", today="2026-09-12")
            picker.clear()
            self.assertEqual(picker.cache, {})


class ListParentTests(unittest.TestCase):
    def test_missing_sender_returns_none(self):
        class Event:
            def get_sender_id(self):
                raise RuntimeError("no sender")

        self.assertEqual(list_parent(Event()), (None, None))

    def test_sender_builds_qq_avatar_url(self):
        class Event:
            def get_sender_id(self):
                return "10001"

            def get_sender_name(self):
                return "Alice"

        self.assertEqual(
            list_parent(Event()),
            ("Alice", "https://q1.qlogo.cn/g?b=qq&nk=10001&s=640"),
        )


if __name__ == "__main__":
    unittest.main()
