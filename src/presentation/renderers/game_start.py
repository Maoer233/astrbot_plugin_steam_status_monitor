import os
import io
import time
import httpx
from PIL import Image, ImageDraw, ImageFont
import random
from .steam_cover import get_steam_library_cover_url

from ...shared.paths import FONTS_DIR, IMAGES_DIR
from ...shared.logging import logger
from ...shared.network import httpx_client_kwargs

BG_COLOR_TOP = (49, 80, 66)
BG_COLOR_BOTTOM = (28, 35, 44)
AVATAR_SIZE = 80

_cache_config = {"avatar":86400,"avatar_frame":604800,"cover_vertical":0}
COVER_W, COVER_H = 80, 120
IMG_W, IMG_H = 512, 192  # 16:6，画布高度减少三分之一


def get_avatar_path(data_dir, steamid, url, force_update=False, proxy=None):
    avatar_dir = os.path.join(data_dir, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    path = os.path.join(avatar_dir, f"{steamid}.jpg")
    refresh_interval = _cache_config.get("avatar", 86400)
    if refresh_interval > 0 and os.path.exists(path) and not force_update:
        if time.time() - os.path.getmtime(path) < refresh_interval:
            return path
    elif refresh_interval == 0 and os.path.exists(path):
        return path
    try:
        resp = httpx.get(url, timeout=10, proxy=proxy)
        if resp.status_code == 200:
            with open(path, "wb") as f:
                f.write(resp.content)
            return path
    except Exception:
        pass
    return path if os.path.exists(path) else None


def set_cache_config(config_dict):
    global _cache_config
    _cache_config.update(config_dict)


_frame_url_cache = {}  # {steamid: (url_or_None, timestamp)}

async def get_avatar_frame_url(steamid, proxy=None):
    import re, json, time
    cache_ttl = _cache_config.get("avatar_frame", 604800)
    if steamid in _frame_url_cache:
        cached_url, cached_time = _frame_url_cache[steamid]
        if cache_ttl == 0 or time.time() - cached_time < cache_ttl:
            return cached_url
    try:
        url = f"https://steamcommunity.com/profiles/{steamid}/?l=english"
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, **httpx_client_kwargs(proxy)) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                div_match = re.search(r'<div class="profile_avatar_frame">(.*?)</div>', resp.text, re.DOTALL)
                if div_match:
                    sources = re.findall(r'srcset="([^"]+)"', div_match.group(1))
                    if sources:
                        result = sources[-1]
                        print(f"[get_avatar_frame_url] 找到头像框: {result[:80]}...")
                        _frame_url_cache[steamid] = (result, time.time())
                        return result
                g_match = re.search(r'g_rgProfileData\s*=\s*({.+?});', resp.text, re.DOTALL)
                if g_match:
                    try:
                        raw = g_match.group(1).replace(r'\/', '/')
                        profile = json.loads(raw)
                        avatar_frame = profile.get('avatar_frame')
                        if avatar_frame and isinstance(avatar_frame, dict) and avatar_frame.get('url'):
                            result = avatar_frame['url']
                            print(f"[get_avatar_frame_url] 找到头像框(JSON): {result[:80]}...")
                            _frame_url_cache[steamid] = (result, time.time())
                            return result
                    except: pass
                _frame_url_cache[steamid] = (None, time.time())
            print(f"[get_avatar_frame_url] 无头像框: steamid={steamid}")
    except Exception as e:
        print(f"[get_avatar_frame_url] 异常: {e}")
    return None

async def get_avatar_frame_path(data_dir, steamid, url=None, proxy=None):
    import hashlib, time
    frame_dir = os.path.join(data_dir, "avatar_frames")
    os.makedirs(frame_dir, exist_ok=True)
    refresh_interval = _cache_config.get("avatar_frame", 604800)
    # 优先本地扫描：检查是否有该 steamid 的缓存文件
    if os.path.exists(frame_dir):
        for fname in os.listdir(frame_dir):
            if fname.startswith(f"{steamid}_") and fname.endswith(".png"):
                path = os.path.join(frame_dir, fname)
                if refresh_interval > 0 and time.time() - os.path.getmtime(path) < refresh_interval:
                    return path
                elif refresh_interval == 0:
                    return path
    # 本地无缓存 + 有URL → 下载
    if not url:
        return None
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    path = os.path.join(frame_dir, f"{steamid}_{url_hash}.png")
    if refresh_interval > 0 and os.path.exists(path) and time.time() - os.path.getmtime(path) < refresh_interval:
        return path
    elif refresh_interval == 0 and os.path.exists(path):
        return path
    try:
        async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                with open(path, "wb") as f:
                    f.write(resp.content)
                return path
    except Exception:
        pass
    return path if os.path.exists(path) else None

async def get_sgdb_vertical_cover(game_name, sgdb_api_key=None, sgdb_game_name=None, appid=None, proxy=None):
    import httpx
    import urllib.parse
    if not sgdb_api_key:
        return None
    headers = {"Authorization": f"Bearer {sgdb_api_key}"}
    search_name = sgdb_game_name if sgdb_game_name else game_name
    search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{urllib.parse.quote(search_name)}"
    async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as client:
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
                # 主查查不到封面时也兜底
                if appid:
                    print(f"[SGDB主查兜底] appid={appid}，尝试通过appid查SGDB name")
                    game_url = f"https://www.steamgriddb.com/api/v2/games/steam/{appid}"
                    resp_game = await client.get(game_url, headers=headers)
                    data_game = resp_game.json()
                    if data_game.get("success") and data_game.get("data") and data_game["data"].get("name"):
                        sgdb_name = data_game["data"]["name"]
                        print(f"[SGDB主查兜底] appid={appid}，查到SGDB name={sgdb_name}，再次尝试查封面")
                        search_url2 = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{sgdb_name}"
                        resp2 = await client.get(search_url2, headers=headers)
                        data2 = resp2.json()
                        if data2.get("success") and data2.get("data"):
                            sgdb_game_id = data2["data"][0]["id"]
                            grid_url = f"https://www.steamgriddb.com/api/v2/grids/game/{sgdb_game_id}?dimensions=600x900&type=static&limit=1"
                            resp3 = await client.get(grid_url, headers=headers)
                            data3 = resp3.json()
                            if data3.get("success") and data3.get("data"):
                                print(f"[SGDB主查兜底] 成功获取到封面: {data3['data'][0]['url']}")
                                return data3["data"][0]["url"]
                        print(f"[SGDB主查兜底] 通过SGDB name未查到封面: {sgdb_name}")
                print(f"[SGDB主查兜底] 兜底流程未查到封面 appid={appid}")
                return None
            if data2.get("success") and data2.get("data"):
                # 遍历前3个封面，优先选静态
                for idx, grid in enumerate(data2["data"][:3]):
                    grid_type = grid.get("type")
                    grid_url = grid.get("url")
                    print(f"[SGDB遍历] idx={idx} type={grid_type} url={grid_url}")
                    if grid_type == "static":
                        print(f"[SGDB遍历] 选中静态封面: {grid_url}")
                        return grid_url
                # 如果没有静态，返回第一个可用封面
                if data2["data"]:
                    print(f"[SGDB遍历] 未找到静态，返回第一个封面: {data2['data'][0]['url']}")
                    return data2["data"][0]["url"]
            print(f"[SGDB主查] 成功获取到封面: {data2['data'][0]['url']}")
            return data2["data"][0]["url"]
        except Exception as e:
            print(f"[get_sgdb_vertical_cover] SGDB API异常: {e}")
            return None

async def get_cover_path(data_dir, gameid, game_name, force_update=False, sgdb_api_key=None, sgdb_game_name=None, appid=None, proxy=None, api_key=None):
    from PIL import Image as PILImage
    import httpx
    cover_dir = os.path.join(data_dir, "covers_v")
    os.makedirs(cover_dir, exist_ok=True)
    steam_path = os.path.join(cover_dir, f"{gameid}_library_capsule_2x.jpg")
    fallback_path = os.path.join(cover_dir, f"{gameid}.jpg")
    # 只在本地不存在时才云端获取
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
    missing_cover = str(IMAGES_DIR / "missingcover.jpg")
    if os.path.exists(missing_cover):
        return missing_cover
    return None

def get_horizontal_cover_path(data_dir, gameid, appid=None, proxy=None):
    """获取Steam横版header_image封面本地路径，永久缓存（无过期时间）"""
    import httpx
    cover_dir = os.path.join(data_dir, "covers_h")
    os.makedirs(cover_dir, exist_ok=True)
    path = os.path.join(cover_dir, f"{gameid}.jpg")
    if os.path.exists(path):
        return path
    if not appid:
        return None
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese"
        resp = httpx.get(url, timeout=10, proxy=proxy)
        if resp.status_code == 200:
            data = resp.json()
            info = data.get(str(appid), {}).get("data", {})
            header_img = info.get("header_image")
            if header_img:
                img_resp = httpx.get(header_img, timeout=10, proxy=proxy)
                if img_resp.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(img_resp.content)
                    print(f"[get_horizontal_cover_path] 下载成功: {gameid} -> {path}")
                    return path
    except Exception as e:
        print(f"[get_horizontal_cover_path] 获取横版封面异常: {e}")
    return path if os.path.exists(path) else None

def text_wrap(text, font, max_width):
    """自动换行，返回行列表"""
    lines = []
    if not text:
        return [""]
    line = ""
    # 创建临时画布用于测量
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

def get_chinese_length(text):
    """估算中文字符长度（1中文=2英文）"""
    length = 0
    for c in text:
        if '\u4e00' <= c <= '\u9fff':
            length += 1
        else:
            length += 0.5
    return int(length + 0.5)

def pad_game_name(game_name, min_cn_len=10):
    """游戏名后方补空格，渲染满10个中文字符宽度"""
    cur_len = get_chinese_length(game_name)
    pad_len = max(0, min_cn_len - cur_len)
    return game_name + "　" * pad_len + "   "  # 中文全角空格+3半角空格

def render_gradient_bg(img_w, img_h, color_top, color_bottom):
    """生成竖向渐变背景"""
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

async def get_playtime_hours(api_key, steamid, appid, retry_times=3, proxy=None):
    """通过 Steam Web API 获取某玩家某游戏的总游玩小时数（异步实现，失败自动重试）"""
    import asyncio
    url = (
        f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
        f"?key={api_key}&steamid={steamid}&include_appinfo=0&include_played_free_games=1&appids_filter[0]={appid}"
    )
    for attempt in range(retry_times):
        try:
            async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"[get_playtime_hours] API返回: {data}")
                    games = data.get("response", {}).get("games", [])
                    for g in games:
                        if str(g.get("appid")) == str(appid):
                            playtime_min = g.get("playtime_forever", 0)
                            return round(playtime_min / 60, 1)
                    print(f"[get_playtime_hours] 未找到目标游戏: steamid={steamid} appid={appid} games={games}")
                else:
                    print(f"[get_playtime_hours] HTTP状态码异常: {resp.status_code} url={url}")
        except Exception as e:
            print(f"[get_playtime_hours] 获取游玩时间异常: {e} url={url}")
        if attempt < retry_times - 1:
            await asyncio.sleep(1)
    return 0.0

def get_font_path(font_name):
    fonts_dir = str(FONTS_DIR)
    font_path = os.path.join(fonts_dir, font_name)
    if os.path.exists(font_path):
        return font_path
    font_path2 = os.path.join(os.path.dirname(__file__), font_name)
    if os.path.exists(font_path2):
        return font_path2
    return font_name

def render_game_start_image(player_name, avatar_path, game_name, cover_path, playtime_hours=None, superpower=None, online_count=None, font_path=None, playtime_unowned=False, avatar_frame_path=None, horizontal_cover_path=None, version=None):
    # 字体
    fonts_dir = str(FONTS_DIR)
    font_regular = os.path.join(fonts_dir, 'NotoSansHans-Regular.otf')
    font_medium = os.path.join(fonts_dir, 'NotoSansHans-Medium.otf')
    if not os.path.exists(font_regular):
        font_regular = os.path.join(os.path.dirname(__file__), 'NotoSansHans-Regular.otf')
    if not os.path.exists(font_medium):
        font_medium = os.path.join(os.path.dirname(__file__), 'NotoSansHans-Medium.otf')
    try:
        font_bold = ImageFont.truetype(font_medium, 28)
        font = ImageFont.truetype(font_regular, 22)
        font_small = ImageFont.truetype(font_regular, 16)
    except:
        font_bold = font = font_small = ImageFont.load_default()

    img_w = IMG_W
    img_h = IMG_H
    img = render_gradient_bg(img_w, img_h, BG_COLOR_TOP, BG_COLOR_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # 1. 封面图贴左，等比例缩放高度，宽度自适应，左贴右留空，不裁剪
    cover_area_h = IMG_H
    new_w = COVER_W  # 默认宽度，防止后续变量未定义
    if cover_path and os.path.exists(cover_path):
        try:
            cover_src = Image.open(cover_path).convert("RGBA")
            scale = cover_area_h / cover_src.height
            new_w = int(cover_src.width * scale)
            new_h = cover_area_h
            cover_resized = cover_src.resize((new_w, new_h), Image.LANCZOS)
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
                    print(f"[render_game_start_image] 横版封面叠加成功: {horizontal_cover_path} ({h_new_w}x{h_new_h})")
                except Exception as e:
                    print(f"[render_game_start_image] 横版封面叠加失败: {e}")
        except Exception as e:
            print(f"[render_game_start_image] 封面渲染失败: {e}")
            new_w = COVER_W  # 渲染失败时使用默认宽度

    # 2. 头像位置参数（不再渲染头像）
    avatar_size = AVATAR_SIZE
    avatar_margin = 24
    cover_right = int(new_w)
    avatar_x = cover_right + avatar_margin
    # avatar_y 的赋值和渲染放到后面

    # 3. 文本：头像右侧，整体垂直居中，左右留白，无背景
    text_x = avatar_x + avatar_size + avatar_margin
    text_area_w = img_w - text_x - avatar_margin
    game_name_padded = pad_game_name(game_name, min_cn_len=10)
    game_name_lines = text_wrap(game_name_padded, font, text_area_w)
    line_height = 36
    # 只为游戏时长多加一行
    block_height = line_height * (2 + len(game_name_lines)) + 10 + font_small.size + 4
    text_y = (img_h - block_height) // 2

    # 将头像Y坐标与玩家名对齐，并下移10像素
    avatar_y = text_y + 10

    # 头像渲染（只保留一次）
    if avatar_path and os.path.exists(avatar_path):
        try:
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
                    print(f"[render_game_start_image] 头像框渲染失败: {e}")
            # 超能力文本渲染（头像下方居中两行）
            if superpower:
                try:
                    font_power_title = ImageFont.truetype(font_regular, 16)
                    font_power = ImageFont.truetype(font_regular, 18)
                except:
                    font_power_title = font_power = ImageFont.load_default()
                power_x = avatar_x + AVATAR_SIZE // 2
                power_y = avatar_y + AVATAR_SIZE + 8
                title_text = "今天的超能力"
                ability_text = superpower
                title_bbox = draw.textbbox((0, 0), title_text, font=font_power_title)
                title_w = title_bbox[2] - title_bbox[0]
                title_h = title_bbox[3] - title_bbox[1]
                ability_bbox = draw.textbbox((0, 0), ability_text, font=font_power)
                ability_w = ability_bbox[2] - ability_bbox[0]
                ability_h = ability_bbox[3] - ability_bbox[1]
                title_color = (255, 255, 255, 128)
                ability_color = (120, 180, 255, 128)
                draw.text(
                    (avatar_x + (AVATAR_SIZE - title_w) // 2, power_y),
                    title_text, font=font_power_title, fill=title_color
                )
                draw.text(
                    (avatar_x + (AVATAR_SIZE - ability_w) // 2, power_y + title_h + 2),
                    ability_text, font=font_power, fill=ability_color
                )
        except Exception as e:
            print(f"[render_game_start_image] 头像/超能力渲染失败: {e}")

    # 新增：右上角显示在线人数，提前计算宽度
    online_text = None
    online_text_w = 0
    if online_count is not None:
        try:
            font_online = ImageFont.truetype(font_regular, 14)
        except:
            font_online = ImageFont.load_default()
        online_text = f"\u25CF玩家人数{online_count}"
        text_bbox = draw.textbbox((0, 0), online_text, font=font_online)
        online_text_w = text_bbox[2] - text_bbox[0] + 10  # 加右侧边距

    # 玩家名自适应字号，防止出界和与在线人数重叠
    max_playername_w = IMG_W - (text_x + 8) - online_text_w - 24
    player_font_size = 28
    for size in range(28, 15, -2):
        try:
            font_bold_tmp = ImageFont.truetype(font_medium, size)
        except:
            font_bold_tmp = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), player_name, font_bold_tmp)
        if bbox[2] - bbox[0] <= max_playername_w:
            player_font_size = size
            break
    try:
        font_bold_final = ImageFont.truetype(font_medium, player_font_size)
    except:
        font_bold_final = ImageFont.load_default()
    draw.text((text_x + 8, text_y), player_name, font=font_bold_final, fill=(255,255,255,255))

    # “正在玩”
    draw.text((text_x + 8, text_y + line_height), "正在玩", font=font, fill=(200,255,200,255))
    # 游戏名多行（亮绿色 129,173,81）
    for idx, line in enumerate(game_name_lines):
        draw.text((text_x + 8, text_y + line_height*2 + idx*line_height), line, font=font, fill=(129,173,81,255))
    # 游戏时长（紧跟在最后一行游戏名下方，无多余空行）
    if playtime_hours is not None:
        if playtime_unowned:
            playtime_str = "游戏时间 缺省"
        else:
            playtime_str = f"游戏时间 {playtime_hours} 小时"
        y_time = text_y + line_height*2 + len(game_name_lines)*line_height + 4  # 仅加4像素间距
        draw.text(
            (text_x + 8, y_time),
            playtime_str, font=font_small, fill=(120,180,255,255)
        )
        print(f"[render_game_start_image] 渲染游戏时长: {playtime_str}")
    else:
        print("[render_game_start_image] 未获取到游戏时长，playtime_hours=None")

    # 在线人数渲染（放在最后，确保不会被玩家名遮挡）
    if online_text:
        draw.text((IMG_W - online_text_w, 10), online_text, font=font_online, fill=(120,180,255,180))

    # 右下角版本号水印
    if version:
        try:
            font_version = ImageFont.truetype(font_regular, 8)
        except:
            font_version = ImageFont.load_default()
        v_text = f"v{version}"
        v_bbox = draw.textbbox((0, 0), v_text, font=font_version)
        v_w = v_bbox[2] - v_bbox[0]
        v_h = v_bbox[3] - v_bbox[1]
        draw.text((IMG_W - v_w - 6, IMG_H - v_h - 4), v_text, font=font_version, fill=(33, 46, 49, 120))

    return img.convert("RGB")

async def render_game_start(data_dir, steamid, player_name, avatar_url, gameid, game_name, api_key=None, superpower=None, online_count=None, sgdb_api_key=None, font_path=None, sgdb_game_name=None, appid=None, proxy=None, version=None):
    print(f"[render_game_start] superpower参数: {superpower}")
    avatar_path = get_avatar_path(data_dir, steamid, avatar_url, proxy=proxy)
    cover_path = await get_cover_path(data_dir, gameid, game_name, sgdb_api_key=sgdb_api_key, sgdb_game_name=sgdb_game_name, appid=appid, proxy=proxy, api_key=api_key)
    # 获取横版封面（竖版缺失时叠加用）
    horizontal_cover_path = get_horizontal_cover_path(data_dir, gameid, appid=appid, proxy=proxy)
    playtime_hours = None
    playtime_unowned = False
    if api_key:
        playtime_hours = await get_playtime_hours(api_key, steamid, gameid, proxy=proxy)
        playtime_unowned = (playtime_hours == 0.0)
    avatar_frame_path = await get_avatar_frame_path(data_dir, steamid, proxy=proxy)
    if not avatar_frame_path:
        avatar_frame_url = await get_avatar_frame_url(steamid, proxy=proxy)
        avatar_frame_path = await get_avatar_frame_path(data_dir, steamid, avatar_frame_url, proxy=proxy) if avatar_frame_url else None
    img = render_game_start_image(player_name, avatar_path, game_name, cover_path, playtime_hours, superpower, online_count, font_path=font_path, playtime_unowned=playtime_unowned, avatar_frame_path=avatar_frame_path, horizontal_cover_path=horizontal_cover_path, version=version)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()
