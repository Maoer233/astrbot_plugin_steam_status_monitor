from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
FONT_MANIFEST_PATH = FONTS_DIR / "manifest.json"
IMAGES_DIR = ASSETS_DIR / "images"
ABILITIES_PATH = ASSETS_DIR / "abilities.txt"
CONFIG_PATH = PROJECT_ROOT / "config.json"
PAGES_DIR = PROJECT_ROOT / "pages"

REQUIRED_FONT_FILES = (
    "NotoSansHans-Regular.otf",
    "NotoSansHans-Medium.otf",
    "MiSans-Regular.ttf",
    "MiSans-Bold.ttf",
    "MiSans-Light.ttf",
)


def asset_path(*parts: str) -> str:
    return str(ASSETS_DIR.joinpath(*parts))
