import os
import random
import datetime

def load_abilities(filepath):
    """加载能力列表，每行为一个能力"""
    with open(filepath, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def get_daily_superpower(steamid, abilities):
    """根据steamid和当天日期，稳定生成一个超能力"""
    today = datetime.date.today().isoformat()
    seed = f"{steamid}-{today}"
    rnd = random.Random(seed)
    return rnd.choice(abilities)
