# filepath: c:\Users\Maoer\Desktop\AstrBotLauncher-0.1.5.6\AstrBot\data\plugins\steam_status_monitor_V2\game_end_render.py
import os
import io
import time
import asyncio
import httpx
from PIL import Image, ImageDraw, ImageFont
from .game_start_render import get_avatar_frame_url, get_avatar_frame_path, _cache_config, get_horizontal_cover_path
from .steam_cover import get_steam_library_cover_url

# 更深的蓝紫色到黑色渐变
BG_COLOR_TOP = (24, 18, 48)   # 顶部深蓝紫
BG_COLOR_BOTTOM = (8, 8, 16)  # 底部接近黑色
AVATAR_SIZE = 80
COVER_W, COVER_H = 80, 120
IMG_W, IMG_H = 512, 192

# 星星素材路径（假定与本文件同目录）
STAR_BG_PATH = os.path.join(os.path.dirname(__file__), "随机散布的小星星767x809xp.png")


async def get_sgdb_vertical_cover(game_name, sgdb_api_key=None, sgdb_game_name=None, appid=None, proxy=None):
    import httpx
    if not sgdb_api_key:
        return None
    headers = {"Authorization": f"Bearer {sgdb_api_key}"}
    search_name = sgdb_game_name if sgdb_game_name else game_name
    search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{search_name}"
    async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
        try:
            resp = await client.get(search_url, headers=headers)
            data = resp.json()
            if not data.get("success") or not data.get("data"):
                # 兜底：用 appid 查询 SGDB 游戏名
                if appid:
                    print(f"[SGDB兜底] appid={appid}，尝试通过appid查SGDB name")
                    game_url = f"https://www.steamgriddb.com/api/v2/games/steam/{appid}"
                    resp_game = await client.get(game_url, headers=headers)
                    data_game = resp_game.json()
                    if data_game.get("success") and data_game.get("data") and data_game["data"].get("name"):
                        sgdb_name = data_game["data"]["name"]
                        print(f"[SGDB兜底] appid={appid}，查到SGDB name={sgdb_name}，再次尝试查封面")
                        search_url2 = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{sgdb_name}"
                        resp2 = await client.get(search_url2, headers=headers)
                        data2 = resp2.json()
                        if data2.get("success") and data2.get("data"):
                            sgdb_game_id = data2["data"][0]["id"]
                            grid_url = f"https://www.steamgriddb.com/api/v2/grids/game/{sgdb_game_id}?dimensions=600x900&type=static&limit=1"
                            resp3 = await client.get(grid_url, headers=headers)
                            data3 = resp3.json()
                            if data3.get("success") and data3.get("data"):
                                print(f"[SGDB兜底] 成功获取到封面: {data3['data'][0]['url']}")
                                return data3["data"][0]["url"]
                        print(f"[SGDB兜底] 通过SGDB name未查到封面: {sgdb_name}")
                print(f"[SGDB兜底] 兜底流程未查到封面 appid={appid}")
                return None
            sgdb_game_id = data["data"][0]["id"]
            grid_url = f"https://www.steamgriddb.com/api/v2/grids/game/{sgdb_game_id}?dimensions=600x900&type=static&limit=1"
            resp2 = await client.get(grid_url, headers=headers)
            data2 = resp2.json()
            if not data2.get("success") or not data2.get("data"):
                print(f"[SGDB主查] 查到游戏但未查到封面 sgdb_game_id={sgdb_game_id}")
                return None
            print(f"[SGDB主查] 成功获取到封面: {data2['data'][0]['url']}")
            return data2["data"][0]["url"]
        except Exception as e:
            print(f"[get_sgdb_vertical_cover] SGDB API异常: {e}")
            return None

def get_avatar_path(data_dir, steamid, url, force_update=False, proxy=None):
    avatar_dir = os.path.join(data_dir, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    path = os.path.join(avatar_dir, f"{steamid}.jpg")
    refresh_interval = 24 * 3600
    print(f"[game_end_render] get_avatar_path: url={url}, path={path}, exists={os.path.exists(path)}")
    if os.path.exists(path) and not force_update:
        if time.time() - os.path.getmtime(path) < refresh_interval:
            print(f"[game_end_render] 使用本地头像: {path}, size={os.path.getsize(path)}")
            return path
    try:
        import httpx
        resp = httpx.get(url, timeout=10, proxy=proxy)
        if resp.status_code == 200:
            with open(path, "wb") as f:
                f.write(resp.content)
            print(f"[game_end_render] 下载头像成功: {path}, size={os.path.getsize(path)}")
            return path
        else:
            print(f"[game_end_render] 头像下载失败: HTTP {resp.status_code} url={url}")
    except Exception as e:
        import traceback
        print(f"[game_end_render] 头像下载异常: {e}\n{traceback.format_exc()}")
    return path if os.path.exists(path) else None

# 渐变背景函数补充
def render_gradient_bg(img_w, img_h, color_top, color_bottom):
    base = Image.new("RGB", (img_w, img_h), color_top)
    top_r, top_g, top_b = color_top
    bot_r, bot_g, bot_b = color_bottom
    for y in range(img_h):
        ratio = y / (img_h - 1)
        r = int(top_r * (1 - ratio) + bot_r * ratio)
        g = int(top_g * (1 - ratio) + bot_g * ratio)
        b = int(top_b * (1 - ratio) + bot_b * ratio)
        for x in range(img_w):
            base.putpixel((x, y), (r, g, b))
    return base

# get_cover_path 改为 async def 并 await get_sgdb_vertical_cover
async def get_cover_path(data_dir, gameid, game_name, force_update=False, sgdb_api_key=None, sgdb_game_name=None, appid=None, proxy=None, api_key=None):
    from PIL import Image as PILImage
    import httpx
    cover_dir = os.path.join(data_dir, "covers_v")
    os.makedirs(cover_dir, exist_ok=True)
    steam_path = os.path.join(cover_dir, f"{gameid}_library_capsule_2x.jpg")
    fallback_path = os.path.join(cover_dir, f"{gameid}.jpg")
    cover_refresh = _cache_config.get("cover_vertical", 0)

    def is_cache_valid(path):
        if force_update or not os.path.exists(path):
            return False
        return cover_refresh == 0 or time.time() - os.path.getmtime(path) < cover_refresh

    if api_key and is_cache_valid(steam_path):
        return steam_path

    steam_appid = appid or gameid
    if api_key:
        url = await get_steam_library_cover_url(steam_appid, api_key, proxy=proxy)
        if url:
            try:
                async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
                    resp = await client.get(url)
                if resp.status_code == 200:
                    with open(steam_path, "wb") as f:
                        f.write(resp.content)
                    print(f"[get_cover_path] Steam library_capsule_2x 下载成功: {gameid} -> {steam_path}")
                    return steam_path
            except Exception as e:
                print(f"[get_cover_path] Steam library_capsule_2x 下载异常: {e} url={url}")

    if is_cache_valid(fallback_path):
        return fallback_path

    url = await get_sgdb_vertical_cover(game_name, sgdb_api_key, sgdb_game_name=sgdb_game_name, appid=steam_appid, proxy=proxy)
    if url:
        try:
            async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                with open(fallback_path, "wb") as f:
                    f.write(resp.content)
                print(f"[get_cover_path] SteamGridDB 下载成功: {gameid} -> {fallback_path}")
                return fallback_path
        except Exception as e:
            print(f"[get_cover_path] SteamGridDB 下载异常: {e} url={url}")
    # 新增：SGDB未收录或下载失败时，使用missingcover.jpg
    print(f"[get_cover_path] SGDB未收录或下载失败: {gameid} {game_name}，使用默认封面")
    missing_cover = os.path.join(os.path.dirname(__file__), "missingcover.jpg")
    if os.path.exists(missing_cover):
        return missing_cover
    return None

def draw_duration_bar(draw, x, y, width, height, duration_h):
    pad = 1
    # 先画底色和描边
    draw.rounded_rectangle([x-pad, y-pad, x+width+pad, y+height+pad], radius=(height+pad)//2, fill=(0,0,0,180))
    draw.rounded_rectangle([x, y, x + width, y + height], radius=height//2, outline=(0,0,0,255), width=1)
    draw.rounded_rectangle([x-2, y-2, x + width+2, y + height+2], radius=(height+4)//2, outline=(255,255,255,220), width=1)
    bar_colors = [
        (80, 200, 120),    # 1小时 绿色
        (255, 220, 80),    # 3小时 黄色
        (255, 160, 80),    # 5小时 橙色
        (255, 80, 80),     # 7小时 红色
        (200, 80, 160),    # 9小时 紫红色
        (120, 80, 200)     # 12小时 深紫色
    ]
    seg_limits = [1, 3, 5, 7, 9, 12]
    seg_starts = [0] + seg_limits[:-1]
    seg_texts = [None, "2X", "3X", "4X", "5X", "6X"]
    if duration_h > 12:
        # 彩色渐变条
        for i in range(width):
            ratio = i / max(width-1, 1)
            # 渐变色：红橙黄绿青蓝紫
            from colorsys import hsv_to_rgb
            rgb = hsv_to_rgb(ratio, 0.8, 1.0)
            color = tuple(int(c*255) for c in rgb)
            draw.line([(x+i, y), (x+i, y+height)], fill=color, width=1)
        # 叠加MAX文字
        try:
            font = ImageFont.truetype("msyhbd.ttc", height+8)
        except:
            font = ImageFont.load_default()
        text = "MAX"
        text_bbox = draw.textbbox((0,0), text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        center_x = x + width // 2 - text_w // 2
        center_y = y + height // 2 - text_h // 2 - 5
        draw.text((center_x, center_y), text, font=font, fill=(255,255,255,255), stroke_width=2, stroke_fill=(0,0,0,180))
    else:
        # 普通分段条
        for i, (seg_start, seg_end, color) in enumerate(zip(seg_starts, seg_limits, bar_colors)):
            seg_val = min(max(duration_h - seg_start, 0), seg_end - seg_start)
            seg_ratio = seg_val / (seg_end - seg_start) if seg_end > seg_start else 0
            seg_w = int(width * seg_ratio)
            if seg_w > 0:
                draw.rounded_rectangle([x, y, x + seg_w, y + height], radius=height//2, fill=color)
        for i, (seg_start, seg_end, color) in enumerate(zip(seg_starts, seg_limits, bar_colors)):
            if (seg_texts[i] and duration_h > seg_start):
                text = seg_texts[i]
                try:
                    font = ImageFont.truetype("msyhbd.ttc", height+6)
                except:
                    font = ImageFont.load_default()
                text_bbox = draw.textbbox((0,0), text, font=font)
                text_w = text_bbox[2] - text_bbox[0]
                text_h = text_bbox[3] - text_bbox[1]
                center_x = x + width // 2 - text_w // 2
                center_y = y + height // 2 - text_h // 2 - 5
                draw.text((center_x, center_y), text, font=font, fill=color, stroke_width=2, stroke_fill=(0,0,0,180))

def get_font_path(font_name):
    fonts_dir = os.path.join(os.path.dirname(__file__), 'fonts')
    font_path = os.path.join(fonts_dir, font_name)
    if (os.path.exists(font_path)):
        return font_path
    font_path2 = os.path.join(os.path.dirname(__file__), font_name)
    if (os.path.exists(font_path2)):
        return font_path2
    return font_name

def text_wrap(text, font, max_width):
    lines = []
    if not text:
        return [""]
    line = ""
    dummy_img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy_img)
    for char in text:
        bbox = draw.textbbox((0, 0), line + char, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            line += char
        else:
            lines.append(line)
            line = char
    if line:
        lines.append(line)
    return lines

def render_game_end_image(player_name, avatar_path, game_name, cover_path, end_time_str, tip_text, duration_h, font_path=None, avatar_frame_path=None, horizontal_cover_path=None):
    # 字体
    fonts_dir = os.path.join(os.path.dirname(__file__), 'fonts')
    font_regular = os.path.join(fonts_dir, 'NotoSansHans-Regular.otf')
    font_medium = os.path.join(fonts_dir, 'NotoSansHans-Medium.otf')
    if not os.path.exists(font_regular):
        font_regular = os.path.join(os.path.dirname(__file__), 'NotoSansHans-Regular.otf')
    if not os.path.exists(font_medium):
        font_medium = os.path.join(os.path.dirname(__file__), 'NotoSansHans-Medium.otf')
    try:
        font_title = ImageFont.truetype(font_medium, 28)
        font_game = ImageFont.truetype(font_regular, 22)
        font_tip = ImageFont.truetype(font_regular, 16)
        font_luck = ImageFont.truetype(font_regular, 14)
        font_time = ImageFont.truetype(font_regular, 8)
    except:
        font_title = font_game = font_tip = font_luck = font_time = ImageFont.load_default()

    img = render_gradient_bg(IMG_W, IMG_H, BG_COLOR_TOP, BG_COLOR_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # 1. 背景星星横向平铺（等比例缩放高度，透明度30%）
    try:
        star_bg = Image.open(STAR_BG_PATH).convert("RGBA")
        star_w, star_h = star_bg.size
        scale = IMG_H / star_h
        new_w = int(star_w * scale)
        new_h = IMG_H
        star_bg_resized = star_bg.resize((new_w, new_h), Image.LANCZOS)
        # 设置透明度30%
        alpha = star_bg_resized.split()[-1].point(lambda p: int(p * 0.3))
        star_bg_resized.putalpha(alpha)
        for x in range(0, IMG_W, new_w):
            img.alpha_composite(star_bg_resized, (x, 0))
    except Exception as e:
        print(f"[game_end_render] 星星背景加载失败: {e}")

    # 2. 封面图左侧，等比例缩放高度，宽度自适应，不裁剪，左贴右留空
    cover_area_h = IMG_H
    new_w = COVER_W
    if cover_path and os.path.exists(cover_path):
        try:
            cover_src = Image.open(cover_path).convert("RGBA")
            scale = cover_area_h / cover_src.height
            new_w = int(cover_src.width * scale)
            new_h = cover_area_h
            cover_resized = cover_src.resize((new_w, new_h), Image.LANCZOS)
            # 修正：如果new_w大于画布宽度，限制最大宽度为画布宽度，防止超出
            if new_w > IMG_W:
                cover_resized = cover_resized.crop((0, 0, IMG_W, new_h))
                new_w = IMG_W
            img.paste(cover_resized, (0, 0), cover_resized)
            # 竖版封面缺失（missingcover）时，叠加横版header_image
            if os.path.basename(cover_path) == "missingcover.jpg" and horizontal_cover_path and os.path.exists(horizontal_cover_path):
                try:
                    h_cover = Image.open(horizontal_cover_path).convert("RGBA")
                    h_scale = new_w / h_cover.width
                    h_new_w = new_w
                    h_new_h = int(h_cover.height * h_scale)
                    h_cover_resized = h_cover.resize((h_new_w, h_new_h), Image.LANCZOS)
                    h_offset_y = (cover_area_h - h_new_h) // 2
                    img.paste(h_cover_resized, (0, h_offset_y), h_cover_resized)
                    print(f"[game_end_render] 横版封面叠加成功: {horizontal_cover_path} ({h_new_w}x{h_new_h})")
                except Exception as e:
                    print(f"[game_end_render] 横版封面叠加失败: {e}")
        except Exception as e:
            print(f"[game_end_render] 封面加载失败: {e}")
            new_w = COVER_W  # 渲染失败时使用默认宽度

    # 3. 头像（仅圆角，无柔光特效）
    avatar_x = new_w + 24
    avatar_y = 16
    if avatar_path and os.path.exists(avatar_path):
        try:
            print(f"[game_end_render] 尝试打开头像: {avatar_path}")
            avatar = Image.open(avatar_path).convert("RGBA").resize((AVATAR_SIZE, AVATAR_SIZE))
            # 圆角遮罩
            mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.rounded_rectangle((0, 0, AVATAR_SIZE, AVATAR_SIZE), radius=AVATAR_SIZE//5, fill=255)
            avatar_rgba = avatar.copy()
            avatar_rgba.putalpha(mask)
            img.alpha_composite(avatar_rgba, (avatar_x, avatar_y))
            if avatar_frame_path and os.path.exists(avatar_frame_path):
                try:
                    frame_size = AVATAR_SIZE + 12
                    frame_offset = (frame_size - AVATAR_SIZE) // 2
                    frame_img = Image.open(avatar_frame_path).convert("RGBA").resize((frame_size, frame_size), Image.LANCZOS)
                    img.alpha_composite(frame_img, (avatar_x - frame_offset, avatar_y - frame_offset))
                except Exception as e:
                    print(f"[game_end_render] 头像框渲染失败: {e}")
        except Exception as e:
            import traceback
            print(f"[game_end_render] 头像加载失败: {e}\n{traceback.format_exc()}")

    # 今日人品（0~100），显示在头像正下方，字体更小，每个steamid每天固定
    import random, datetime, hashlib
    today = datetime.date.today().isoformat()
    luck_seed = f"{player_name}_{today}".encode("utf-8")
    today_luck = int(hashlib.md5(luck_seed).hexdigest(), 16) % 101
    luck_text = f"今日人品：{today_luck}"
    luck_font_y = avatar_y + AVATAR_SIZE + 8
    draw.text((avatar_x, luck_font_y), luck_text, font=font_luck, fill=(200,220,255,220), stroke_width=1, stroke_fill=(0,0,0,255))

    # 当前时间叠加在最上方右上角，字号更小
    try:
        from datetime import datetime
        t = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
        time_str = t.strftime("%H:%M")
    except Exception:
        time_str = end_time_str[-5:]
    bbox = draw.textbbox((0,0), time_str, font=font_time, stroke_width=2)
    time_x = IMG_W - bbox[2] + bbox[0] - 18  # 右上角，留边距
    time_y = 6
    draw.text((time_x, time_y), time_str, font=font_time, fill=(255,255,255,220), stroke_width=2, stroke_fill=(0,0,0,255))

    # 4. 玩家名，顶部居左，自适应字号防止出界
    title_text = f"{player_name} 结束游戏"
    # 计算最大宽度（头像右侧到画布右侧，留24px边距）
    max_title_w = IMG_W - (avatar_x + AVATAR_SIZE + 20) - 24
    title_font_size = 28
    for size in range(28, 15, -2):
        try:
            font_title_tmp = ImageFont.truetype(font_medium, size)
        except:
            font_title_tmp = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), title_text, font=font_title_tmp)
        if bbox[2] - bbox[0] <= max_title_w:
            title_font_size = size
            break
    try:
        font_title = ImageFont.truetype(font_medium, title_font_size)
    except:
        font_title = ImageFont.load_default()
    draw.text((avatar_x + AVATAR_SIZE + 20, 16), title_text, font=font_title, fill=(180,160,255,255), stroke_width=2, stroke_fill=(0,0,0,255))

    # 5. 游戏名，头像右侧居左，第二行，自动换行
    game_name_y = 16 + font_title.size + 8
    max_game_name_w = IMG_W - (avatar_x + AVATAR_SIZE + 20) - 24
    game_name_lines = text_wrap(game_name, font_game, max_game_name_w)
    max_lines = 2
    for idx, line in enumerate(game_name_lines[:max_lines]):
        draw.text((avatar_x + AVATAR_SIZE + 20, game_name_y + idx * (font_game.size + 2)), line, font=font_game, fill=(220,220,255,255), stroke_width=2, stroke_fill=(0,0,0,255))

    # 6. 空几行（间隔）
    tip_y = game_name_y + font_game.size + 28

    # 7. 进度条和时长文本，放在头像列的底部，与今日人品同列
    bar_x = avatar_x
    bar_y = IMG_H - 24
    if duration_h < 1:
        min_text = f"已玩{int(duration_h*60)}分钟："
    else:
        min_text = f"已玩{duration_h:.1f}小时："
    # 文字略抬高，进度条略降低
    draw.text((bar_x, bar_y-2), min_text, font=font_tip, fill=(180, 220, 255, 220), stroke_width=1, stroke_fill=(0,0,0,255))
    min_text_bbox = draw.textbbox((bar_x, bar_y-2), min_text, font=font_tip)
    bar_start_x = min_text_bbox[2] + 6
    bar_w = IMG_W - bar_start_x - 18  # 进度条延伸到画布结尾，右侧留18px
    bar_h = 6
    if bar_w > 0:
        draw_duration_bar(draw, bar_start_x, bar_y+6, bar_w, bar_h, duration_h)
    else:
        print(f"[game_end_render] 跳过进度条渲染，bar_w={bar_w}")

    # 8. 友好提示词，玩家名列底部，且与进度条有间隔
    tip_y = bar_y - font_tip.size - 8
    draw.text((bar_x, tip_y), tip_text, font=font_tip, fill=(200,180,255,200), stroke_width=1, stroke_fill=(0,0,0,255))
    return img.convert("RGB")

# render_game_end 里 await get_cover_path
async def render_game_end(data_dir, steamid, player_name, avatar_url, gameid, game_name, end_time_str, tip_text, duration_h, sgdb_api_key=None, font_path=None, sgdb_game_name=None, appid=None, proxy=None, api_key=None):
    avatar_path = get_avatar_path(data_dir, steamid, avatar_url, proxy=proxy)
    cover_path = await get_cover_path(data_dir, gameid, game_name, sgdb_api_key=sgdb_api_key, sgdb_game_name=sgdb_game_name, appid=appid, proxy=proxy, api_key=api_key)
    # 获取横版封面（竖版缺失时叠加用）
    horizontal_cover_path = get_horizontal_cover_path(data_dir, gameid, appid=appid, proxy=proxy)
    avatar_frame_path = await get_avatar_frame_path(data_dir, steamid, proxy=proxy)
    if not avatar_frame_path:
        avatar_frame_url = await get_avatar_frame_url(steamid, proxy=proxy)
        avatar_frame_path = await get_avatar_frame_path(data_dir, steamid, avatar_frame_url, proxy=proxy) if avatar_frame_url else None
    img = render_game_end_image(player_name, avatar_path, game_name, cover_path, end_time_str, tip_text, duration_h, font_path=font_path, avatar_frame_path=avatar_frame_path, horizontal_cover_path=horizontal_cover_path)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()
