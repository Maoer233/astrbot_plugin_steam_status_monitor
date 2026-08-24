# -*- coding: utf-8 -*-
"""排行榜图片渲染模块
参考 steam_list_render.py 的实色卡片风格，不使用磨砂玻璃透明效果
"""
import os
import io
import asyncio
import httpx
import logging
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from .game_start import get_avatar_frame_url, get_avatar_frame_path
from ...shared.paths import FONTS_DIR

logger = logging.getLogger(__name__)

# Steam 深色主题色板（参考 steam_list_render）
BG_TOP = (44, 62, 80)        # #2c3e50
BG_BOTTOM = (24, 32, 44)     # #18202c
CARD_BG = (38, 44, 56)       # 实色卡片底（不透明）
CARD_RADIUS = 12
CARD_MARGIN = 18
CARD_GAP = 12
CARD_HEIGHT = 190            # 增高以容纳4行游戏表格
WIDTH = 680

# 排名边框色
RANK_BORDER = {
    1: (255, 215, 0),     # 金
    2: (192, 192, 192),   # 银
    3: (205, 127, 50),    # 铜
}
RANK_BORDER_DEFAULT = (58, 90, 120)

# 分段进度条色板（循环分配给各游戏）
SEGMENT_COLORS = [
    (102, 192, 244),   # Steam 蓝
    (164, 208, 7),     # 绿
    (237, 159, 39),    # 橙
    (175, 119, 221),   # 紫
    (212, 83, 126),    # 粉
    (94, 202, 165),    # 青绿
    (240, 153, 123),   # 珊瑚
    (136, 170, 255),   # 蓝紫
]
SEGMENT_COLOR_OTHER = (100, 110, 120)  # 其他游戏灰色

# 排名字体色
RANK_TEXT_COLOR = {
    1: (255, 215, 0),
    2: (192, 192, 192),
    3: (205, 127, 50),
}
RANK_TEXT_DEFAULT = (143, 152, 160)

AVATAR_SIZE = 72             # 增大头像
COVER_W = 150                # 横版封面宽度（右侧）
COVER_H = 70                 # 横版封面高度
MAX_PLAYERS = 25

# 游戏列表表格布局（相对卡片左上角）
TABLE_LEFT = 150             # 游戏名起始 x（头像右侧）
TABLE_TOP = 72               # 第一行 y
TABLE_ROW_H = 20             # 行高
TIME_COL_W = 70              # 时长列宽度（右对齐区域）


def _get_font(font_path, size):
    """安全加载字体"""
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        try:
            return ImageFont.truetype(os.path.join(str(FONTS_DIR), 'NotoSansHans-Regular.otf'), size)
        except Exception:
            return ImageFont.load_default()


def _get_bold_font(font_path, size):
    """获取加粗字体（Medium）"""
    bold_path = font_path.replace('Regular', 'Medium')
    if os.path.exists(bold_path):
        try:
            return ImageFont.truetype(bold_path, size)
        except Exception:
            pass
    return _get_font(font_path, size)


def _format_hours(minutes):
    """分钟转可读时长"""
    h = minutes / 60.0
    if h >= 1:
        return f"{h:.1f}h"
    return f"{int(minutes)}min"


def _truncate_text(draw, text, font, max_width):
    """截断过长文本，加省略号"""
    # 防御性清洗：确保 text 是字符串
    if isinstance(text, (tuple, list)):
        text = text[0] if text else ""
    text = str(text) if text is not None else ""
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text
    for i in range(len(text) - 1, 0, -1):
        truncated = text[:i] + "…"
        bbox = draw.textbbox((0, 0), truncated, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return truncated
    return "…"


async def _fetch_avatar(avatar_url, data_dir, sid, proxy=None):
    """获取玩家头像（复用缓存）"""
    if not avatar_url:
        return None
    avatar_dir = os.path.join(data_dir, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    path = os.path.join(avatar_dir, f"{sid}.jpg")
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    try:
        async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
            resp = await client.get(avatar_url)
            if resp.status_code == 200:
                with open(path, "wb") as f:
                    f.write(resp.content)
                return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception:
        pass
    return None


def _draw_segment_bar(draw, x, y, w, h, games, colors, max_total=None):
    """绘制分段彩色进度条
    max_total: Top1玩家的总时长作为100%基准；None则用玩家自己的总时长
    返回 (fill_w, player_total) 供调用方绘制百分比文字
    """
    player_total = sum(g["minutes"] for g in games)
    if player_total <= 0:
        return 0, 0
    if max_total is None or max_total <= 0:
        max_total = player_total
    fill_ratio = min(player_total / max_total, 1.0)
    fill_w = int(w * fill_ratio)
    if fill_w <= 0:
        return 0, player_total
    cur_x = x
    for i, (game, color) in enumerate(zip(games, colors)):
        seg_w = max(1, int(fill_w * game["minutes"] / player_total))
        if i == len(games) - 1:
            seg_w = x + fill_w - cur_x
        draw.rounded_rectangle(
            (cur_x, y, cur_x + seg_w, y + h),
            radius=h // 2,
            fill=color + (255,)
        )
        cur_x += seg_w
    return fill_w, player_total


async def render_rank_image(data_dir, rank_data, period_label, font_path=None, proxy=None, cover_fetcher=None, avatar_frame_paths=None):
    """渲染排行榜图片
    Args:
        data_dir: 数据目录
        rank_data: [{sid, name, total_minutes, games:[{name,minutes}]}] 已排序
        period_label: 时间范围标签，如"今日" / "最近7天"
        font_path: 字体路径
        proxy: 代理
        cover_fetcher: async callable(gameid) -> 本地封面路径
    Returns:
        PNG 图片 bytes
    """
    if font_path is None:
        font_path = os.path.join(str(FONTS_DIR), 'NotoSansHans-Regular.otf')

    font_title = _get_bold_font(font_path, 28)       # 标题加粗放大
    font_subtitle = _get_font(font_path, 14)
    font_rank = _get_bold_font(font_path, 26)        # 排名序号加粗
    font_name = _get_bold_font(font_path, 20)        # 玩家名加粗放大
    font_time = _get_font(font_path, 16)
    font_game = _get_font(font_path, 14)             # 游戏列表表格
    font_small = _get_font(font_path, 11)

    rank_data = rank_data[:MAX_PLAYERS]
    n = len(rank_data)

    if n == 0:
        height = 200
        img = Image.new('RGBA', (WIDTH, height), BG_TOP)
        draw = ImageDraw.Draw(img)
        for y in range(height):
            ratio = y / max(1, height - 1)
            r = int(BG_TOP[0] * (1 - ratio) + BG_BOTTOM[0] * ratio)
            g = int(BG_TOP[1] * (1 - ratio) + BG_BOTTOM[1] * ratio)
            b = int(BG_TOP[2] * (1 - ratio) + BG_BOTTOM[2] * ratio)
            draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
        title = f"{period_label}游戏时长排行榜"
        bbox = draw.textbbox((0, 0), title, font=font_title)
        draw.text(((WIDTH - bbox[2] + bbox[0]) // 2, 40), title, font=font_title, fill=(255, 255, 255))
        draw.text((WIDTH // 2 - 60, 90), "暂无游玩记录", font=font_subtitle, fill=(143, 152, 160))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format='PNG')
        return buf.getvalue()

    header_h = 80
    card_area_top = header_h + CARD_MARGIN
    total_min = sum(p["total_minutes"] for p in rank_data)
    max_total_minutes = rank_data[0]["total_minutes"] if rank_data else 0  # Top1总时长作为进度条100%基准
    # 预计算每个玩家卡片高度（游戏<=1种则降低高度）
    card_heights = []
    for p in rank_data:
        gc = len(p.get("games", []))
        if gc <= 1:
            card_heights.append(155)
        elif gc == 2:
            card_heights.append(170)
        else:
            card_heights.append(CARD_HEIGHT)
    total_cards_h = sum(card_heights) + max(0, n - 1) * CARD_GAP
    height = card_area_top + total_cards_h + 30
    # 预计算每个卡片Y偏移
    card_offsets = [card_area_top]
    for ch in card_heights[:-1]:
        card_offsets.append(card_offsets[-1] + ch + CARD_GAP)

    # 1. 渐变背景
    img = Image.new('RGBA', (WIDTH, height), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / max(1, height - 1)
        r = int(BG_TOP[0] * (1 - ratio) + BG_BOTTOM[0] * ratio)
        g = int(BG_TOP[1] * (1 - ratio) + BG_BOTTOM[1] * ratio)
        b = int(BG_TOP[2] * (1 - ratio) + BG_BOTTOM[2] * ratio)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

    # 2. 标题栏（加粗放大）
    title = f"{period_label}游戏时长排行榜"
    bbox = draw.textbbox((0, 0), title, font=font_title)
    draw.text(((WIDTH - bbox[2] + bbox[0]) // 2, 14), title, font=font_title, fill=(255, 255, 255))
    today_str = datetime.now().strftime("%Y/%m/%d")
    subtitle = f"{today_str} · 共 {n} 位玩家 · 合计 {_format_hours(total_min)}"
    bbox2 = draw.textbbox((0, 0), subtitle, font=font_subtitle)
    draw.text(((WIDTH - bbox2[2] + bbox2[0]) // 2, 50), subtitle, font=font_subtitle, fill=(143, 152, 160))

    # 3. 并发获取头像
    avatar_tasks = [_fetch_avatar(p.get("avatar_url"), data_dir, p["sid"], proxy=proxy) for p in rank_data]
    avatars = await asyncio.gather(*avatar_tasks, return_exceptions=True)

    # 4. 获取每个玩家主玩游戏封面（取时长最高的游戏）
    cover_tasks = []
    for p in rank_data:
        top_game_id = p.get("top_game_id")
        if cover_fetcher and top_game_id:
            cover_tasks.append(cover_fetcher(top_game_id))
        else:
            cover_tasks.append(_async_none())
    covers = await asyncio.gather(*cover_tasks, return_exceptions=True)

    # 5. 绘制每个玩家卡片
    for idx, player in enumerate(rank_data):
        rank = idx + 1
        cx = CARD_MARGIN
        cy = card_offsets[idx]
        cw = WIDTH - 2 * CARD_MARGIN
        ch = card_heights[idx]

        border_color = RANK_BORDER.get(rank, RANK_BORDER_DEFAULT)

        # 实色卡片底（参考 list_render，不透明）
        draw.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=CARD_RADIUS, fill=CARD_BG + (255,))
        # 排名边框
        border_w = 2 if rank <= 3 else 1
        draw.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=CARD_RADIUS, outline=border_color, width=border_w)

        # 排名序号（加粗）
        rank_color = RANK_TEXT_COLOR.get(rank, RANK_TEXT_DEFAULT)
        draw.text((cx + 16, cy + 18), f"#{rank}", font=font_rank, fill=rank_color)

        # 头像（增大至72px）
        avatar_x = cx + 60
        avatar_y = cy + 14
        avatar = avatars[idx] if not isinstance(avatars[idx], Exception) else None
        if avatar and not isinstance(avatar, Exception):
            try:
                avatar = avatar.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
                mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, AVATAR_SIZE, AVATAR_SIZE), radius=12, fill=255)
                img.paste(avatar, (avatar_x, avatar_y), mask)
                # 头像框
                if avatar_frame_paths and player.get("sid") in avatar_frame_paths:
                    try:
                        frame_path = avatar_frame_paths[player["sid"]]
                        frame_size = AVATAR_SIZE + 12
                        frame_offset = (frame_size - AVATAR_SIZE) // 2
                        frame_img = Image.open(frame_path).convert("RGBA").resize((frame_size, frame_size), Image.LANCZOS)
                        frame_img_rgba = frame_img.copy()
                        img.alpha_composite(frame_img_rgba, (avatar_x - frame_offset, avatar_y - frame_offset))
                    except Exception as e:
                        print(f"[rank_render] 头像框渲染失败: {e}")
            except Exception:
                draw.rounded_rectangle((avatar_x, avatar_y, avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE), radius=12, fill=(60, 70, 85))
        else:
            draw.rounded_rectangle((avatar_x, avatar_y, avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE), radius=12, fill=(60, 70, 85))

        # 玩家名（加粗放大，截断过长名称）
        name_x = cx + TABLE_LEFT
        player_name = _truncate_text(draw, player["name"], font_name, 200)
        draw.text((name_x, cy + 16), player_name, font=font_name, fill=(255, 255, 255))

        # 总时长（金色醒目）
        draw.text((name_x, cy + 44), f"总时长 {_format_hours(player['total_minutes'])}", font=font_time, fill=(255, 200, 60))

        # 横版封面（右侧）
        cover_path = covers[idx] if not isinstance(covers[idx], Exception) else None
        cover_x = cx + cw - COVER_W - 16
        cover_y = cy + 14
        if cover_path and os.path.exists(cover_path):
            try:
                cover_img = Image.open(cover_path).convert("RGBA")
                cover_img = cover_img.resize((COVER_W, COVER_H), Image.LANCZOS)
                cmask = Image.new("L", (COVER_W, COVER_H), 0)
                ImageDraw.Draw(cmask).rounded_rectangle((0, 0, COVER_W, COVER_H), radius=6, fill=255)
                img.paste(cover_img, (cover_x, cover_y), cmask)
            except Exception:
                draw.rounded_rectangle((cover_x, cover_y, cover_x + COVER_W, cover_y + COVER_H), radius=6, fill=(40, 50, 65))
                draw.text((cover_x + 40, cover_y + 28), "No Cover", font=font_small, fill=(100, 110, 120))
        else:
            draw.rounded_rectangle((cover_x, cover_y, cover_x + COVER_W, cover_y + COVER_H), radius=6, fill=(40, 50, 65))
            draw.text((cover_x + 40, cover_y + 28), "No Cover", font=font_small, fill=(100, 110, 120))

        # 游戏明细表格（最多4行：前3个游戏 + 其他合计）
        games = player["games"]
        if len(games) <= 4:
            shown = games
            other_min = 0
        else:
            shown = games[:3]
            other_min = sum(g["minutes"] for g in games[3:])

        # 构建显示项（游戏名、时长、对应进度条颜色）
        display_items = []
        for i, g in enumerate(shown):
            color = SEGMENT_COLORS[i % len(SEGMENT_COLORS)]
            display_items.append((g["name"], _format_hours(g["minutes"]), color))
        if other_min > 0:
            other_count = len(games) - 3
            display_items.append((f"其他{other_count}款", _format_hours(other_min), SEGMENT_COLOR_OTHER))

        # 表格区域：左边界 = 头像右侧，右边界 = 封面左侧
        tbl_left = cx + TABLE_LEFT
        tbl_right = cover_x - 16
        name_max_w = (tbl_right - tbl_left) - TIME_COL_W - 8

        for row_i, (gname, gtime, gcolor) in enumerate(display_items):
            row_y = cy + TABLE_TOP + row_i * TABLE_ROW_H
            # 游戏名左对齐（截断 + 对应颜色）
            truncated = _truncate_text(draw, gname, font_game, name_max_w)
            draw.text((tbl_left, row_y), truncated, font=font_game, fill=gcolor)
            # 时长右对齐（同色）
            time_bbox = draw.textbbox((0, 0), gtime, font=font_game)
            time_x = tbl_right - (time_bbox[2] - time_bbox[0])
            draw.text((time_x, row_y), gtime, font=font_game, fill=gcolor)

        # 分段彩色进度条（底部）
        bar_y = cy + ch - 20
        bar_x = tbl_left
        bar_w = tbl_right - tbl_left
        bar_h = 7
        bar_games = [g for g in shown]
        if other_min > 0:
            bar_games.append({"name": "其他", "minutes": other_min})
        bar_colors = [SEGMENT_COLORS[i % len(SEGMENT_COLORS)] for i in range(len(shown))]
        if other_min > 0:
            bar_colors.append(SEGMENT_COLOR_OTHER)
        # 100%灰色背景条（作为满格基准）
        draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=bar_h//2, fill=(50, 55, 65, 255))
        # 实际填充进度条
        fill_w, _ = _draw_segment_bar(draw, bar_x, bar_y, bar_w, bar_h, bar_games, bar_colors, max_total=max_total_minutes)
        # 百分比（Top1=100%）
        if max_total_minutes > 0:
            pct = round(player["total_minutes"] / max_total_minutes * 100)
            pct_text = f"{pct}%"
            try:
                font_pct = ImageFont.truetype(font_path, 13)
            except Exception:
                font_pct = font_small
            draw.text((bar_x + fill_w + 8, bar_y - 2), pct_text, font=font_pct, fill=(180, 190, 200))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format='PNG')
    return buf.getvalue()


async def _async_none():
    """异步返回None的辅助函数"""
    return None
