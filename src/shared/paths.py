from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
IMAGES_DIR = ASSETS_DIR / "images"
ABILITIES_PATH = ASSETS_DIR / "abilities.txt"
CONFIG_PATH = PROJECT_ROOT / "config.json"
PAGES_DIR = PROJECT_ROOT / "pages"


def asset_path(*parts: str) -> str:
    return str(ASSETS_DIR.joinpath(*parts))
