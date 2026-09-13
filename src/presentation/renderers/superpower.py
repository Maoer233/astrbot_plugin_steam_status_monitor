import random
from datetime import date


class SuperpowerPicker:
    """按 SteamID + 日期稳定抽取当日超能力。"""

    def __init__(self, abilities_path):
        self._path = str(abilities_path)
        self._abilities = None
        self.cache = {}

    def get(self, steamid, today=None):
        today = today or date.today().isoformat()
        cache_key = (steamid, today)
        if cache_key in self.cache:
            return self.cache[cache_key]
        if self._abilities is None:
            with open(self._path, encoding="utf-8") as abilities_file:
                self._abilities = [line.strip() for line in abilities_file if line.strip()]
        superpower = random.Random(f"{steamid}-{today}").choice(self._abilities)
        self.cache[cache_key] = superpower
        return superpower

    def clear(self):
        self.cache.clear()
