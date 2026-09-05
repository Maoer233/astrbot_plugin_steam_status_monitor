# 价格折算工具：将 ITAD/Steam 返回的各地区货币统一折算为人民币(CNY)，用于同币种比较与显示。
# 汇率表为 open.er-api.com 与 frankfurter.app 双源交叉核验值（差异 <0.4%，UTC 2026-08-30）；
# 可直接修改 RATES 维护。
import re

RATES = {
    "CNY": 1.0,     # 人民币
    "USD": 6.7459,  # 美元
    "EUR": 7.8301,  # 欧元
    "JPY": 0.0422,  # 日元
    "KRW": 0.0049,  # 韩元
    "RUB": 0.0785,  # 俄罗斯卢布
    "UAH": 0.1514,  # 乌克兰格里夫纳
    "TRY": 0.1398,  # 土耳其里拉
    "GBP": 9.1420,  # 英镑
    "PLN": 1.8036,  # 波兰兹罗提
    "BRL": 1.3063,  # 巴西雷亚尔
    "INR": 0.0705,  # 印度卢比
    "HKD": 0.8582,  # 港元
}


def extract_price_query(raw_msg: str, prefix: str) -> str:
    """从完整消息中剥掉 /steam price（或 px）前缀，保留含空格的游戏名。"""
    return re.sub(
        rf"^[/.。／]*\s*(?:steam\s+)?{re.escape(prefix)}\s*",
        "",
        str(raw_msg or "").strip(),
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def to_cny(price, currency, rates=None):
    """将指定货币金额折算为 CNY；无汇率或非数值时原样返回。"""
    if price is None or not currency:
        return price
    rate = (rates or RATES).get(str(currency).upper())
    if rate:
        try:
            return round(float(price) * rate, 2)
        except (TypeError, ValueError):
            return price
    return price


def summary_to_cny(summary, rates=None):
    """将 ITAD price summary 的金额字段统一折算为 CNY，返回新 dict。
    仅当币种有汇率时才折算并置 currency=CNY；否则保留原币种与金额，避免错标。"""
    out = dict(summary or {})
    currency = str((out.get("currency") or "")).upper()
    if not currency:
        return out
    rate_tbl = rates or RATES
    if currency in rate_tbl:
        for field in ("current_price", "current_regular", "history_low", "lowest"):
            if out.get(field) is not None:
                out[field] = to_cny(out[field], currency, rate_tbl)
        out["currency"] = "CNY"
    return out
