"""Steam 游戏详情卡片渲染。"""

import io
import os
import re
from html import unescape

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ...shared.network import httpx_client_kwargs


CARD_WIDTH = 900
PADDING = 28


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


def _wrap(draw, value, font, max_width):
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
    return lines or ["暂无简介"]


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
    small = _font(font_path, 20)
    title_font = _font(font_path, 40)
    section_font = _font(font_path, 28)
    price_font = _font(font_path, 34)
    muted = (181, 201, 215)
    dark = (238, 244, 248)
    blue = (111, 190, 225)
    panel = (28, 49, 66)
    panel_inner = (18, 36, 50)
    outline = (62, 89, 108)

    header = await _download_image(game.get("header_image") or game.get("image"), proxy)
    header_height = 270
    if header:
        header = ImageOps.fit(header, (CARD_WIDTH - PADDING * 2, header_height), method=Image.Resampling.LANCZOS)

    price = game.get("price_overview") or {}
    if game.get("is_free"):
        price_text = "免费"
        discount_text = ""
    elif price:
        price_text = price.get("final_formatted") or f"{price.get('final', 0) / 100:.2f}"
        discount_text = f"-{price.get('discount_percent', 0)}%" if price.get("discount_percent") else ""
    else:
        price_text = "暂无价格"
        discount_text = ""

    itad_summary = itad_summary or {}
    if not price and itad_summary.get("current_price") is not None:
        itad_currency = itad_summary.get("currency") or ""
        price_text = f"{itad_summary['current_price']:g} {itad_currency}".strip()
        if itad_summary.get("cut"):
            discount_text = f"-{int(itad_summary['cut'])}%"
    itad_currency = itad_summary.get("currency") or ""
    itad_low = itad_summary.get("history_low")
    if itad_low is None:
        itad_low = itad_summary.get("lowest")
    itad_low_text = f"{itad_low:g} {itad_currency}".strip() if itad_low is not None else "暂无"

    review = game.get("review") or {}
    review_text = review.get("text") or review.get("review_score_desc") or "暂无评价"
    review_percent = review.get("percent") or review.get("review_score_percent")
    if review_percent is not None:
        review_text += f" {review_percent}%"

    genres = "、".join(x.get("description", "") for x in game.get("genres", [])) or "未分类"
    developers = "、".join(game.get("developers", [])) or "未知"
    release_date = (game.get("release_date") or {}).get("date") or "未知"

    itad_height = 92 if itad_summary else 0
    height = 40 + header_height + 30 + 64 + 82 + 94 + itad_height + 30
    image = Image.new("RGB", (CARD_WIDTH, height), (10, 23, 32))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12, 12, CARD_WIDTH - 12, height - 12), radius=20, fill=(22, 42, 57), outline=(65, 91, 109), width=2)
    if header:
        image.paste(header, (PADDING, 28))
    else:
        draw.rectangle((PADDING, 28, CARD_WIDTH - PADDING, 28 + header_height), fill=(34, 55, 75))
        draw.text((CARD_WIDTH // 2, 150), "STEAM", font=title_font, fill=(220, 235, 245), anchor="mm")

    y = 28 + header_height + 20
    title = game.get("name") or "未知游戏"
    title_lines = _wrap(draw, title, title_font, CARD_WIDTH - PADDING * 2)
    for line in title_lines[:2]:
        draw.text((PADDING, y), line, font=title_font, fill=dark)
        y += 48
    draw.text((PADDING, y), f"{genres}  |  开发商：{developers}  |  发行日期：{release_date}", font=small, fill=muted)
    y += 42

    draw.rounded_rectangle((PADDING, y, CARD_WIDTH // 2 - 10, y + 72), radius=12, fill=panel_inner, outline=outline)
    draw.text((PADDING + 18, y + 12), "当前价格", font=small, fill=dark)
    draw.text((PADDING + 18, y + 35), price_text, font=price_font, fill=blue)
    if discount_text:
        draw.rounded_rectangle((CARD_WIDTH // 2 - 120, y + 18, CARD_WIDTH // 2 - 28, y + 58), radius=8, fill=(202, 52, 49))
        draw.text((CARD_WIDTH // 2 - 74, y + 38), discount_text, font=small, fill="white", anchor="mm")

    left = CARD_WIDTH // 2 + 10
    draw.rounded_rectangle((left, y, CARD_WIDTH - PADDING, y + 72), radius=12, fill=panel_inner, outline=outline)
    draw.text((left + 18, y + 12), "Steam 评价", font=small, fill=dark)
    draw.text((left + 18, y + 36), review_text, font=price_font, fill=blue)
    y += 94

    if itad_summary:
        draw.rounded_rectangle((PADDING, y, CARD_WIDTH - PADDING, y + 76), radius=12, fill=panel_inner, outline=outline)
        draw.text((PADDING + 18, y + 12), "ITAD 历史最低价", font=small, fill=dark)
        draw.text((PADDING + 18, y + 38), itad_low_text, font=price_font, fill=blue)
        y += 94

    draw.rounded_rectangle((PADDING, y, CARD_WIDTH - PADDING, y + 52), radius=12, fill=panel)
    draw.text((PADDING + 18, y + 15), "数据来源：Steam · ITAD", font=small, fill=muted)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
