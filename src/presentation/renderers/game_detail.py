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


def _font(path, size):
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


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


def _value_text(value, currency=""):
    if value is None:
        return "暂无"
    if isinstance(value, str):
        return value
    return f"{value:g} {currency}".strip()


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


async def render_game_detail_image(game, font_path=None, proxy=None, itad_summary=None):
    """将 Steam appdetails 数据与可选的 ITAD 价格信息渲染为详情卡片。"""
    title_font = _font(font_path, 25)
    english_font = _font(font_path, 15)
    body_font = _font(font_path, 16)
    small_font = _font(font_path, 14)
    price_font = _font(font_path, 34)
    tag_font = _font(font_path, 13)

    itad_summary = itad_summary or {}
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
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), STEAM_PAGE)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, CARD_WIDTH - 1, CARD_HEIGHT - 1), radius=10, fill=STEAM_CARD, outline=STEAM_BORDER, width=2)

    left_x, right_x = PADDING, 344
    left_width, right_width = 300, 454
    y = 22
    draw.text((left_x, y), title, font=title_font, fill=STEAM_WHITE)
    if english_title and english_title != title:
        title_width = draw.textbbox((0, 0), title, font=title_font)[2]
        draw.text((left_x + min(title_width + 10, left_width - 80), y + 7), english_title, font=english_font, fill=STEAM_MUTED)
    y += 44
    draw.text((left_x, y), review_text, font=body_font, fill=STEAM_BLUE)
    if review_percent_text:
        review_width = draw.textbbox((0, 0), review_text, font=body_font)[2]
        draw.text((left_x + review_width + 10, y), review_percent_text, font=body_font, fill=STEAM_BLUE_SOFT)
    y += 32
    draw.text((left_x, y), "发行日期", font=small_font, fill=STEAM_MUTED)
    draw.text((left_x + 68, y), release_date, font=small_font, fill=STEAM_TEXT)
    draw.line((left_x, y + 30, left_x + left_width, y + 30), fill=STEAM_BORDER)

    y += 52
    draw.text((left_x, y), current_text, font=price_font, fill=STEAM_WHITE)
    cursor = left_x + draw.textbbox((0, 0), current_text, font=price_font)[2] + 10
    cursor += _discount_tag(draw, cursor, y + 8, discount_text, tag_font)
    if regular_text:
        draw.text((cursor + 10, y + 14), regular_text, font=small_font, fill=STEAM_STRIKE)
        regular_width = draw.textbbox((0, 0), regular_text, font=small_font)[2]
        draw.line((cursor + 10, y + 22, cursor + 10 + regular_width, y + 22), fill=STEAM_STRIKE)
    y += 48
    draw.text((left_x, y), "史低", font=small_font, fill=STEAM_MUTED)
    draw.text((left_x + 44, y), history_text, font=small_font, fill=STEAM_TEXT)
    draw.line((left_x, y + 28, left_x + left_width, y + 28), fill=STEAM_BORDER)

    region_rows = []
    region_price = itad_summary.get("region_price") or itad_summary.get("cn_price")
    region_ua = itad_summary.get("ua_price") or itad_summary.get("region_ua")
    if region_price is not None:
        region_rows.append(("国区", region_price))
    if region_ua is not None:
        region_rows.append(("UA", region_ua))
    if region_rows:
        ry = CARD_HEIGHT - 112
        draw.rounded_rectangle((left_x, ry, left_x + left_width, CARD_HEIGHT - 22), radius=8, fill=(28, 45, 61), outline=STEAM_BORDER)
        for label, value in region_rows:
            draw.text((left_x + 12, ry + 12), label, font=tag_font, fill=STEAM_BLUE_SOFT)
            draw.text((left_x + 56, ry + 12), _value_text(value, currency), font=small_font, fill=STEAM_TEXT)
            ry += 28

    if cover:
        cover = ImageOps.fit(cover, (right_width, 260), method=Image.Resampling.LANCZOS)
        image.paste(cover, (right_x, 22))
    else:
        draw.rectangle((right_x, 22, right_x + right_width, 282), fill=(31, 48, 65), outline=STEAM_BORDER)
        draw.text((right_x + right_width / 2, 152), "STEAM", font=title_font, fill=STEAM_BLUE_SOFT, anchor="mm")

    y = 302
    for line in _wrap(draw, description, body_font, right_width, max_lines=4):
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
    appid = game.get("steam_appid") or game.get("store_appid")
    if appid:
        draw.text((right_x, y + 23), f"Steam 商店：store.steampowered.com/app/{appid}", font=small_font, fill=STEAM_BLUE)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
