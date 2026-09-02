import unittest

from PIL import ImageFont

from src.presentation.renderers.game_end import (
    IMG_W,
    fit_end_card_player_name,
    render_game_end_image,
)
from src.shared.paths import FONTS_DIR


class GameEndNameLayoutTests(unittest.TestCase):
    def setUp(self):
        self.font = ImageFont.truetype(str(FONTS_DIR / "NotoSansHans-Medium.otf"), 28)
        self.long_name = "迦勒底冠位御主藤丸立香"
        self.text_x = 80 + 24 + 80 + 20

    def test_long_name_keeps_end_suffix_and_stretches_canvas(self):
        lines, width = fit_end_card_player_name(self.long_name, self.font, self.text_x)
        joined = "".join(lines)
        self.assertIn("结束游戏", joined)
        self.assertIn(self.long_name, joined)
        self.assertGreater(width, IMG_W)

    def test_short_name_keeps_default_canvas(self):
        lines, width = fit_end_card_player_name("Alice", self.font, self.text_x)
        self.assertEqual(lines, ["Alice 结束游戏"])
        self.assertEqual(width, IMG_W)

    def test_render_long_name_does_not_clip_to_default_width(self):
        img = render_game_end_image(
            player_name=self.long_name,
            avatar_path=None,
            game_name="魔法使之夜",
            cover_path=None,
            end_time_str="2026-09-02 11:26",
            tip_text="风扇都没转热，主人就结束了？",
            duration_h=1 / 60,
        )
        self.assertGreater(img.size[0], IMG_W)
        self.assertEqual(img.size[1], 192)


if __name__ == "__main__":
    unittest.main()
