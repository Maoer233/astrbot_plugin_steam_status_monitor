"""Steam 游戏详情卡片渲染。"""

import io
import os
import re
from html import unescape

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ...shared.network import httpx_client_kwargs


CARD_WIDTH = 820
CARD_HEIGHT = 560
PADDING = 22


STEAM_PAGE = (27, 40, 56)
STEAM_CARD = (22, 32, 45)
STEAM_BORDER = (42, 71, 94)
STEAM_BLUE = (102, 192, 244)
STEAM_BLUE_SOFT = (172, 219, 245)
STEAM_TEXT = (199, 213, 224)
STEAM_WHITE = (255, 255, 255)
STEAM_MUTED = (143, 152, 160)
STEAM_STRIKE = (115, 136, 149)
STEAM_GREEN = (164, 208, 7)
STEAM_GREEN_BG = (76, 107, 34)
STEAM_GREEN_TOP = (92, 126, 16)

# 货币符号（ITAD currency 代码 → 符号）
CURRENCY_SYMBOL = {
    "CNY": "¥", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥",
    "KRW": "₩", "RUB": "₽", "UAH": "₴", "TRY": "₺", "PLN": "zł",
    "BRL": "R$", "INR": "₹",
}

# 地区代码 → 显示名。未列出的地区直接显示其国家代码。纯文字绘制，避免字体缺字形成豆腐块。
COUNTRY_LABEL = {
    "CN": "国区",
    "RU": "俄区",
    "UA": "乌区",
    "US": "美区",
    "JP": "日区",
    "KR": "韩区",
    "TR": "土区",
    "GB": "英区",
    "DE": "德区",
    "PL": "波区",
}


def _font(path, size):
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit_font(draw, value, path, max_size, min_size, max_width):
    """在指定宽度内为标题选择合适字号。"""
    value = str(value or "")
    for size in range(max_size, min_size - 1, -1):
        font = _font(path, size)
        if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
            return font
    return _font(path, min_size)


def _plain_text(value):
    value = re.sub(r"<img[^>]*>", "", value or "", flags=re.I)
    value = re.sub(r"<script[^>]*>.*?</script>", "", value or "", flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", "", value or "")
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _wrap(draw, value, font, max_width, max_lines=None):
    value = value or "暂无简介"
    lines = []
    current = ""
    for char in value:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1]
        lines[-1] = (last or "暂无简介") + "…"
    return lines or ["暂无简介"]


def _currency_symbol(currency):
    return CURRENCY_SYMBOL.get((currency or "").upper(), "")


def _country_display(code):
    code = (code or "").upper()
    return COUNTRY_LABEL.get(code, code)


def _value_text(value, currency="", symbol=True):
    if value is None:
        return "暂无"
    if isinstance(value, str):
        return value
    amount = f"{value:g}"
    sym = _currency_symbol(currency) if symbol else ""
    if sym:
        return f"{sym}{amount}"
    return f"{amount} {currency}".strip()


def _discount_tag(draw, x, y, text, font, small=False):
    if not text:
        return 0
    width = max(38 if small else 46, draw.textbbox((0, 0), text, font=font)[2] + 14)
    height = 24 if small else 30
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=3,
        fill=STEAM_GREEN_BG,
        outline=STEAM_GREEN_TOP,
    )
    draw.text((x + width / 2, y + height / 2), text, font=font, fill=(210, 243, 76), anchor="mm")
    return width


async def _download_image(url, proxy=None):
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(proxy)) as client:
            response = await client.get(url)
            response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except Exception:
        return None


async def render_game_detail_image(
    game, font_path=None, proxy=None, itad_summary=None, region_prices=None
):
    """将 Steam appdetails 数据与可选的 ITAD 价格信息渲染为详情卡片。"""
    title_font = _font(font_path, 25)
    english_font = _font(font_path, 15)
    body_font = _font(font_path, 16)
    small_font = _font(font_path, 14)
    price_font = _font(font_path, 34)
    tag_font = _font(font_path, 13)

    itad_summary = itad_summary or {}
    region_prices = region_prices or {}
    price = game.get("price_overview") or {}
    currency = itad_summary.get("currency") or price.get("currency") or ""
    if game.get("is_free"):
        current_text, discount_text, regular_text = "免费", "", ""
    elif price:
        current_text = price.get("final_formatted") or _value_text(price.get("final", 0) / 100, currency)
        discount = price.get("discount_percent") or 0
        discount_text = f"-{discount}%" if discount else ""
        regular_text = price.get("initial_formatted") if discount else ""
    elif itad_summary.get("current_price") is not None:
        current_text = _value_text(itad_summary.get("current_price"), currency)
        discount = itad_summary.get("cut") or 0
        discount_text = f"-{int(discount)}%" if discount else ""
        regular_text = _value_text(itad_summary.get("current_regular"), currency) if itad_summary.get("current_regular") is not None else ""
    else:
        current_text, discount_text, regular_text = "暂无价格", "", ""

    history_low = itad_summary.get("history_low")
    if history_low is None:
        history_low = itad_summary.get("lowest")
    history_text = _value_text(history_low, currency)

    review = game.get("review") or {}
    review_text = review.get("text") or review.get("review_score_desc") or "暂无评价"
    review_percent = review.get("percent") or review.get("review_score_percent")
    review_percent_text = f"{review_percent}%" if review_percent is not None else ""

    title = game.get("name") or "未知游戏"
    english_title = game.get("english_name") or game.get("original_name") or ""
    release_date = (game.get("release_date") or {}).get("date") or "未知"
    genres = [x.get("description", "") for x in game.get("genres", []) if x.get("description")]
    categories = [x.get("description", "") for x in game.get("categories", []) if x.get("description")]
    tags = list(dict.fromkeys(genres + categories))[:8]
    developers = "、".join(game.get("developers", [])) or "未知"
    publishers = "、".join(game.get("publishers", [])) or "未知"
    description = _plain_text(game.get("short_description") or game.get("about_the_game"))

    cover = await _download_image(game.get("header_image") or game.get("image"), proxy)
    left_x, right_x = PADDING, 344
    left_width, right_width = 300, 454
    cover_top = 22
    cover_height = max(1, round(right_width * cover.height / cover.width)) if cover else 220
    measure_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    description_lines = _wrap(measure_draw, description, body_font, right_width)
    tag_rows = []
    tag_x = 0
    for tag in tags:
        tag_width = measure_draw.textbbox((0, 0), tag, font=tag_font)[2] + 18
        if tag_x and tag_x + tag_width > right_width:
            tag_rows.append(tag_x)
            tag_x = 0
        tag_x += tag_width + 6
    if tags:
        tag_rows.append(tag_x)
    tag_height = len(tag_rows) * 28

    section_gap = 8
    region_rows = []
    for code, region_summary in (region_prices or {}).items():
        region_summary = region_summary or {}
        region_price = region_summary.get("current_price")
        if region_price is None:
            continue
        label = _country_display(code)
        region_rows.append({
            "label": label,
            "price": region_price,
            "regular": region_summary.get("current_regular"),
            "currency": region_summary.get("currency") or currency,
            "cut": region_summary.get("cut"),
        })
    region_height = max(86, 26 + len(region_rows) * 36 + 16)
    section_heights = (150, 150, region_height)

    right_content_bottom = cover_top + cover_height + 20 + len(description_lines) * 24 + 8 + tag_height + 40 + 46
    left_content_bottom = PADDING + sum(section_heights) + 2 * section_gap
    card_height = max(left_content_bottom + PADDING, right_content_bottom + PADDING)
    image = Image.new("RGB", (CARD_WIDTH, card_height), STEAM_PAGE)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, CARD_WIDTH - 1, card_height - 1), radius=10, fill=STEAM_CARD, outline=STEAM_BORDER, width=2)
    section_top = [
        PADDING,
        PADDING + section_heights[0] + section_gap,
        PADDING + section_heights[0] + section_gap + section_heights[1] + section_gap,
    ]
    for top in section_top[1:]:
        separator_y = top - section_gap // 2
        draw.line((left_x, separator_y, left_x + left_width, separator_y), fill=STEAM_BORDER, width=1)
    draw.rounded_rectangle(
        (left_x, section_top[2], left_x + left_width, section_top[2] + section_heights[2]),
        radius=8,
        fill=(28, 45, 61),
        outline=STEAM_BORDER,
    )

    title_font = _fit_font(draw, title, font_path, 25, 12, left_width - 24)
    title_y = section_top[0] + 12
    draw.text((left_x + 12, title_y), title, font=title_font, fill=STEAM_WHITE)
    if english_title and english_title != title:
        title_width = draw.textbbox((0, 0), title, font=title_font)[2]
        remaining_width = max(40, left_width - 24 - title_width - 10)
        english_font = _fit_font(draw, english_title, font_path, 15, 10, remaining_width)
        draw.text((left_x + 12 + title_width + 10, title_y + 7), english_title, font=english_font, fill=STEAM_MUTED)
    review_y = section_top[0] + 70
    draw.text((left_x + 12, review_y), review_text, font=body_font, fill=STEAM_BLUE)
    if review_percent_text:
        review_width = draw.textbbox((0, 0), review_text, font=body_font)[2]
        draw.text((left_x + 12 + review_width + 10, review_y), review_percent_text, font=body_font, fill=STEAM_BLUE_SOFT)
    draw.text((left_x + 12, section_top[0] + 108), "发行日期", font=small_font, fill=STEAM_MUTED)
    draw.text((left_x + 80, section_top[0] + 108), release_date, font=small_font, fill=STEAM_TEXT)

    price_y = section_top[1] + 28
    draw.text((left_x + 12, price_y), current_text, font=price_font, fill=STEAM_WHITE)
    cursor = left_x + 12 + draw.textbbox((0, 0), current_text, font=price_font)[2] + 10
    cursor += _discount_tag(draw, cursor, price_y + 8, discount_text, tag_font)
    if regular_text:
        draw.text((cursor + 10, price_y + 14), regular_text, font=small_font, fill=STEAM_STRIKE)
        regular_width = draw.textbbox((0, 0), regular_text, font=small_font)[2]
        draw.line((cursor + 10, price_y + 22, cursor + 10 + regular_width, price_y + 22), fill=STEAM_STRIKE)
    draw.text((left_x + 12, section_top[1] + 102), "史低", font=small_font, fill=STEAM_MUTED)
    draw.text((left_x + 56, section_top[1] + 102), history_text, font=small_font, fill=STEAM_TEXT)

    for index, row in enumerate(region_rows):
        row_y = section_top[2] + 22 + index * 36
        x = left_x + 12
        label_text = row["label"]
        draw.text((x, row_y), label_text, font=tag_font, fill=STEAM_BLUE_SOFT)
        x += draw.textbbox((0, 0), label_text, font=tag_font)[2] + 12
        price_text = _value_text(row["price"], row["currency"])
        draw.text((x, row_y), price_text, font=small_font, fill=STEAM_WHITE)
        x += draw.textbbox((0, 0), price_text, font=small_font)[2] + 10
        if row["regular"]:
            regular_text = _value_text(row["regular"], row["currency"])
            draw.text((x, row_y + 1), regular_text, font=small_font, fill=STEAM_STRIKE)
            regular_width = draw.textbbox((0, 0), regular_text, font=small_font)[2]
            draw.line((x, row_y + 11, x + regular_width, row_y + 11), fill=STEAM_STRIKE)
            x += regular_width + 12
        if row["cut"]:
            x += _discount_tag(draw, x, row_y - 4, f"-{int(row['cut'])}%", tag_font, small=True)

    # 地区差价提示：找出最贵/最便宜地区（已统一 CNY），显示“XX 更贵，多花 X.XX 元呢！(+X.XX%)”
    if len(region_rows) >= 2:
        prices = [row["price"] for row in region_rows]
        cheapest = min(prices)
        if cheapest and max(prices) > cheapest:
            expensive_idx = prices.index(max(prices))
            expensive = region_rows[expensive_idx]
            diff = round(max(prices) - cheapest, 2)
            percent = diff / cheapest * 100
            note = f"{expensive['label']}更贵，多花{diff:.2f}元呢！(+{percent:.2f}%)"
            note_y = section_top[2] + region_height - 24
            draw.text((left_x + 12, note_y), note, font=small_font, fill=(255, 178, 44))

    cover_box = (right_width, cover_height)
    cover_left = right_x
    if cover:
        cover = ImageOps.contain(cover, cover_box, method=Image.Resampling.LANCZOS)
        cover_x = cover_left + (right_width - cover.width) // 2
        cover_y = cover_top + (cover_height - cover.height) // 2
        draw.rectangle(
            (cover_left, cover_top, cover_left + right_width, cover_top + cover_height),
            fill=(31, 48, 65),
        )
        image.paste(cover, (cover_x, cover_y))
        draw.rectangle(
            (cover_left, cover_top, cover_left + right_width - 1, cover_top + cover_height - 1),
            outline=STEAM_BORDER,
        )
    else:
        draw.rectangle(
            (cover_left, cover_top, cover_left + right_width, cover_top + cover_height),
            fill=(31, 48, 65),
            outline=STEAM_BORDER,
        )
        draw.text((right_x + right_width / 2, cover_top + cover_height / 2), "STEAM", font=title_font, fill=STEAM_BLUE_SOFT, anchor="mm")

    y = cover_top + cover_height + 20
    for line in description_lines:
        draw.text((right_x, y), line, font=body_font, fill=STEAM_TEXT)
        y += 24
    y += 8
    tag_x = right_x
    for tag in tags:
        tag_width = draw.textbbox((0, 0), tag, font=tag_font)[2] + 18
        if tag_x + tag_width > right_x + right_width:
            tag_x, y = right_x, y + 28
        draw.rounded_rectangle((tag_x, y, tag_x + tag_width, y + 23), radius=3, fill=(31, 54, 72), outline=(55, 91, 116))
        draw.text((tag_x + tag_width / 2, y + 11), tag, font=tag_font, fill=STEAM_BLUE_SOFT, anchor="mm")
        tag_x += tag_width + 6
    y += 40
    draw.text((right_x, y), f"开发商：{developers}", font=small_font, fill=STEAM_MUTED)
    y += 23
    draw.text((right_x, y), f"发行商：{publishers}", font=small_font, fill=STEAM_MUTED)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
