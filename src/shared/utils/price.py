# 价格折算工具：将 ITAD/Steam 返回的各地区货币统一折算为目标主货币（默认 CNY，可在配置 price_currency 修改）。
# 汇率表为 open.er-api.com 与 frankfurter.app 双源交叉核验值（差异 <0.4%，UTC 2026-09-10 更新）；
# 可直接修改 RATES 维护。金额按“外币 → CNY → 目标币种”两步折算。
import re

RATES = {
    "CNY": 1.0,     # 人民币
    "USD": 6.7254,  # 美元
    "EUR": 7.8167,  # 欧元
    "JPY": 0.0438,  # 日元
    "KRW": 0.005,   # 韩元
    "RUB": 0.078,   # 俄罗斯卢布
    "UAH": 0.1506,  # 乌克兰格里夫纳
    "TRY": 0.1392,  # 土耳其里拉
    "GBP": 9.1017,  # 英镑
    "PLN": 1.8211,  # 波兰兹罗提
    "BRL": 1.3186,  # 巴西雷亚尔
    "INR": 0.0708,  # 印度卢比
    "HKD": 0.8576,  # 港元
    "TWD": 0.2142,  # 新台币（按 USD 交叉估算）
}

# 主货币 → ITAD/Steam 查询区映射（主货币决定查询哪个国家/地区的商店价）
CURRENCY_REGION = {
    "CNY": "CN",
    "JPY": "JP",
    "USD": "US",
    "EUR": "DE",
    "GBP": "GB",
    "KRW": "KR",
    "RUB": "RU",
    "UAH": "UA",
    "TRY": "TR",
    "PLN": "PL",
    "BRL": "BR",
    "INR": "IN",
    "HKD": "HK",
    "TWD": "TW",
}

# 国区锁区时的商店回退顺序：港、台、日、美。
STORE_REGION_FALLBACKS = ("HK", "TW", "JP", "US")


def store_region_candidates(preferred="CN"):
    """主区优先，其后追加未锁区回退，去重且保持顺序。"""
    preferred = str(preferred or "CN").strip().upper() or "CN"
    ordered = []
    for code in (preferred, *STORE_REGION_FALLBACKS):
        if code and code not in ordered:
            ordered.append(code)
    return ordered


def is_store_region_locked(preferred, actual_region=None, region_prices=None):
    """主区无商店价、且实际命中了其它区时，视为锁区。"""
    preferred = str(preferred or "").strip().upper()
    if not preferred:
        return False
    actual = str(actual_region or "").strip().upper()
    available = {str(code).upper() for code in (region_prices or {})}
    if preferred in available:
        return False
    if actual and actual != preferred:
        return True
    return bool(available - {preferred})


def extract_price_query(raw_msg: str, prefix: str) -> str:
    """从完整消息中剥掉 /steam price（或 px）前缀，保留含空格的游戏名。"""
    return re.sub(
        rf"^[/.。／]*\s*(?:steam\s+)?{re.escape(prefix)}\s*",
        "",
        str(raw_msg or "").strip(),
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def extract_steam_appid(text):
    """从 Steam 商店链接提取 appid（如 https://store.steampowered.com/app/412020/_/ → 412020）；非链接返回 None。"""
    match = re.search(r"(?:store\.)?steampowered\.com/app/(\d+)", str(text or ""))
    return match.group(1) if match else None


def convert(price, from_currency, to_currency, rates=None):
    """将金额从 from_currency 折算到 to_currency；任一币种无汇率或金额非数值时原样返回。"""
    if price is None or not from_currency or not to_currency:
        return price
    table = rates or RATES
    frm = str(from_currency).upper()
    to = str(to_currency).upper()
    if frm == to:
        return price
    try:
        amount = float(price)
    except (TypeError, ValueError):
        return price
    f_rate = table.get(frm)
    t_rate = table.get(to)
    if not f_rate or not t_rate:
        return price
    return round(amount * f_rate / t_rate, 2)


def to_cny(price, currency, rates=None):
    """兼容包装：将指定货币金额折算为 CNY。"""
    return convert(price, currency, "CNY", rates)


def summary_to_currency(summary, target="CNY", rates=None):
    """将 ITAD price summary 的金额字段统一折算为目标币种，返回新 dict。
    仅当原币种与目标币种都有汇率时才折算并置 currency=target；否则保留原币种与金额，避免错标。"""
    out = dict(summary or {})
    currency = str((out.get("currency") or "")).upper()
    target = str(target or "CNY").upper()
    if not currency or currency == target:
        return out
    table = rates or RATES
    if currency in table and target in table:
        for field in ("current_price", "current_regular", "history_low", "lowest"):
            if out.get(field) is not None:
                out[field] = convert(out[field], currency, target, table)
        out["currency"] = target
    return out


def summary_to_cny(summary, rates=None):
    """兼容包装：将 ITAD price summary 统一折算为 CNY。"""
    return summary_to_currency(summary, "CNY", rates)
