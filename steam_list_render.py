import os
import io
import asyncio
import logging
import httpx
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ============ 样式参考 nonebot-plugin-steam-info 的 draw_friends_status ============
WIDTH = 400
PARENT_AVATAR_SIZE = 72
MEMBER_AVATAR_SIZE = 50
MEMBER_AVATAR_X = 22          # 行内头像左上角 x
MEMBER_AVATAR_Y = 8           # 行内头像左上角 y
ROW_HEIGHT = 64
ROW_STEP = MEMBER_AVATAR_SIZE + 16  # 66，行与行之间留 2px 间隙
SECTION_HEADER_H = 64
SECTION_BOTTOM_PAD = 16

BG_COLOR = (30, 32, 36)                # 1e2024
SEARCH_BAR_BG = (67, 73, 83)           # 434953
SEARCH_TEXT_COLOR = (183, 204, 213)    # b7ccd5
SECTION_TITLE_COLOR = (197, 214, 212)  # c5d6d4
COUNT_COLOR = (103, 102, 92)           # 67665c
DIVIDER_COLOR = (51, 52, 57)           # 333439
PARENT_NAME_COLOR = (109, 207, 246)    # 6dcff6
PARENT_STATUS_COLOR = (76, 145, 172)   # 4c91ac

# 分组标题后人数计数的 x 坐标（与 nonebot-plugin-steam-info 硬编码一致），其余分组按标题宽度计算
SECTION_COUNT_X = {'在线好友': 115, '离线': 72}

# (名字色, 状态文字色)，与 nonebot-plugin-steam-info personastate_colors 对应
STATUS_COLORS = {
    'playing': ((227, 255, 194), (142, 190, 86)),   # e3ffc2 / 8ebe56
    'online':  ((109, 206, 245), (76, 145, 172)),   # 6dcef5 / 4c91ac
    'busy':    ((109, 206, 245), (76, 145, 172)),
    'snooze':  ((109, 206, 245), (76, 145, 172)),
    'away':    ((69, 119, 142), (54, 89, 105)),     # 45778e / 365969
    'offline': ((150, 150, 151), (101, 101, 101)),  # 969697 / 656565
    'error':   ((215, 110, 110), (180, 90, 90)),
}

RES_DIR = os.path.join(os.path.dirname(__file__), 'res')
FONTS_DIR = os.path.join(os.path.dirname(__file__), 'fonts')


def _res(name):
    path = os.path.join(RES_DIR, name)
    return path if os.path.exists(path) else None


def _load_image(path, size=None):
    if not path:
        return None
    try:
        img = Image.open(path).convert('RGBA')
        if size:
            img = img.resize(size, Image.BICUBIC)
        return img
    except Exception as e:
        logger.warning(f"[steam_list_render] 加载资源失败 {path}: {e}")
        return None


async def fetch_avatar(avatar_url, data_dir, sid, proxy=None):
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


def get_status_color(status):
    if status == 'playing':
        return (80, 220, 120)  # 绿色
    elif status == 'online':
        return (80, 180, 255)  # 蓝色
    elif status == 'away':
        return (178, 138, 255)  # 紫色（离开，与打盹统一）
    elif status == 'snooze':
        return (178, 138, 255)  # 紫色（打盹）
    elif status == 'busy':
        return (255, 100, 100)  # 红色
    elif status == 'offline':
        return (255, 255, 255)  # 白色
    else:
        return (180, 80, 80)


def get_name_color(status):
    if status == 'playing':
        return (227,255,194)
    elif status == 'online':
        return (80, 180, 255)
    elif status == 'away':
        return (178, 138, 255)
    elif status == 'snooze':
        return (178, 138, 255)
    elif status == 'busy':
        return (255, 100, 100)
    elif status == 'offline':
        return (220, 220, 220)
    else:
        return (255, 120, 120)


def get_status_text(status):
    if status == 'playing':
        return "正在游戏"
    elif status == 'online':
        return "在线"
    elif status == 'away':
        return "离开"
    elif status == 'snooze':
        return "打盹"
    elif status == 'busy':
        return "忙碌"
    elif status == 'offline':
        return "离线"
    else:
        return "异常"


def get_font_path(font_name):
    fonts_dir = os.path.join(os.path.dirname(__file__), 'fonts')
    font_path = os.path.join(fonts_dir, font_name)
    if os.path.exists(font_path):
        return font_path
    font_path2 = os.path.join(os.path.dirname(__file__), font_name)
    if os.path.exists(font_path2):
        return font_path2
    return font_name


_font_cache = {}

def load_font(size, weight='regular', font_path=None):
    """加载字体：优先 MiSans（与 nonebot-plugin-steam-info 一致），缺失时回退 NotoSansHans / 传入 font_path / 系统字体"""
    key = (size, weight, font_path)
    if key in _font_cache:
        return _font_cache[key]
    if weight == 'bold':
        candidates = ['MiSans-Bold.ttf', 'NotoSansHans-Medium.otf', 'msyhbd.ttc']
    elif weight == 'light':
        candidates = ['MiSans-Light.ttf', 'NotoSansHans-Regular.otf', 'msyh.ttc']
    else:
        candidates = ['MiSans-Regular.ttf', 'NotoSansHans-Regular.otf', 'msyh.ttc']
    if font_path:
        candidates.append(font_path)
    font = ImageFont.load_default()
    for name in candidates:
        p = os.path.join(FONTS_DIR, name)
        if os.path.exists(p):
            name = p
        try:
            font = ImageFont.truetype(name, size)
            break
        except Exception:
            continue
    _font_cache[key] = font
    return font


def _fit_text(draw, text, font, max_w):
    """文本超出 max_w 时截断并追加省略号"""
    if max_w <= 0:
        return ''
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = '…'
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + ell, font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell


def draw_parent_status(parent_name, sub_text, fonts):
    """顶部横幅：bot 头像 + 名称 + 状态（参照 nonebot-plugin-steam-info draw_parent_status）"""
    canvas = _load_image(_res('parent_status.png'), (WIDTH, 120))
    if canvas is None:
        canvas = Image.new('RGBA', (WIDTH, 120), (39, 79, 96, 255))
    avatar = _load_image(_res('unknown_avatar.jpg'), (PARENT_AVATAR_SIZE, PARENT_AVATAR_SIZE))
    if avatar is not None:
        avatar_y = 120 - 16 - PARENT_AVATAR_SIZE
        canvas.paste(avatar, (16, avatar_y), avatar)
    draw = ImageDraw.Draw(canvas)
    text_x = 16 + PARENT_AVATAR_SIZE + 16
    name = _fit_text(draw, str(parent_name), fonts['parent_name'], WIDTH - text_x - 12)
    draw.text((text_x, 44), name, font=fonts['parent_name'], fill=PARENT_NAME_COLOR)
    sub = _fit_text(draw, str(sub_text), fonts['parent_sub'], WIDTH - text_x - 12)
    draw.text((text_x, 68), sub, font=fonts['parent_sub'], fill=PARENT_STATUS_COLOR)
    return canvas


def draw_friends_search(fonts):
    """“好友”搜索条（参照 nonebot-plugin-steam-info draw_friends_search）"""
    canvas = Image.new('RGB', (WIDTH, 50), SEARCH_BAR_BG)
    icon = _load_image(_res('friends_search.png'))
    if icon is not None:
        canvas.paste(icon, (WIDTH - icon.width, 0), icon)
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 10), '好友', SEARCH_TEXT_COLOR, font=fonts['search'])
    return canvas


def draw_friend_status(user, avatar, fonts, avatar_frame_paths=None):
    """渲染单行玩家状态（参照 nonebot-plugin-steam-info draw_friend_status，64px 高）"""
    avatar = avatar.convert('RGBA').resize((MEMBER_AVATAR_SIZE, MEMBER_AVATAR_SIZE), Image.BICUBIC)
    canvas = Image.new('RGB', (WIDTH, ROW_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    name_color, status_color = STATUS_COLORS.get(user.get('status'), STATUS_COLORS['error'])
    # 不渲染 SteamID：名字缺失或等于 SteamID 时显示“未知玩家”
    raw_name = str(user.get('name') or '')
    display_name = raw_name if raw_name and raw_name != str(user.get('sid')) else '未知玩家'

    # 右侧附加信息：时长（不渲染群名 / SteamID / 下次轮询时间）
    right2_parts = []
    if user.get('status') == 'playing' and user.get('play_str'):
        right2_parts.append(str(user['play_str']))
    right2 = ' | '.join(right2_parts)

    # 第一行：玩家名（忙碌/打盹追加图标）
    icon_reserve = 30 if user.get('status') in ('busy', 'snooze') else 0
    name_x = MEMBER_AVATAR_X + MEMBER_AVATAR_SIZE + 18
    max_name_w = WIDTH - name_x - 12 - icon_reserve
    name_text = _fit_text(draw, display_name, fonts['name'], max_name_w)
    draw.text((name_x, 12), name_text, font=fonts['name'], fill=name_color)
    name_w = int(draw.textlength(name_text, font=fonts['name']))
    icon = None
    if user.get('status') == 'busy':
        icon = _load_image(_res('busy.png'))
    elif user.get('status') == 'snooze':
        icon = _load_image(_res('zzz_online.png'))
    if icon is not None:
        # busy 图标 +4、打盹图标 +8（与 nonebot-plugin-steam-info draw_friend_status 一致）
        icon_x = MEMBER_AVATAR_X + MEMBER_AVATAR_SIZE + 16 + name_w + (4 if user.get('status') == 'busy' else 8)
        canvas.paste(icon, (icon_x, 18), icon)

    # 第二行：状态 / 游戏名
    status = user.get('status')
    if status == 'playing':
        status_text = str(user.get('game') or '未知游戏')
    elif status == 'offline':
        status_text = str(user.get('play_str') or '离线')
    elif status == 'error':
        status_text = str(user.get('play_str') or '获取失败')
    else:
        status_text = {'online': '在线', 'away': '离开', 'snooze': '打盹', 'busy': '忙碌'}.get(status, '在线')
    status_x = MEMBER_AVATAR_X + MEMBER_AVATAR_SIZE + 16
    if right2:
        right_w = int(draw.textlength(right2, font=fonts['extra']))
        draw.text((WIDTH - right_w - 12, 36), right2, font=fonts['extra'], fill=COUNT_COLOR)
        status_text = _fit_text(draw, status_text, fonts['status'], WIDTH - status_x - right_w - 12 - 12)
    draw.text((status_x, 36), status_text, font=fonts['status'], fill=status_color)

    # 头像 + 头像框
    canvas.paste(avatar, (MEMBER_AVATAR_X, MEMBER_AVATAR_Y), avatar)
    if avatar_frame_paths and str(user.get('sid')) in avatar_frame_paths:
        try:
            frame = Image.open(avatar_frame_paths[str(user['sid'])]).convert('RGBA')
            frame_size = MEMBER_AVATAR_SIZE + 8
            frame = frame.resize((frame_size, frame_size), Image.BICUBIC)
            fx = MEMBER_AVATAR_X - (frame_size - MEMBER_AVATAR_SIZE) // 2
            fy = MEMBER_AVATAR_Y - (frame_size - MEMBER_AVATAR_SIZE) // 2
            canvas.paste(frame, (fx, fy), frame)
        except Exception as e:
            logger.warning(f"[steam_list_render] 头像框渲染失败: {e}")
    return canvas


def draw_section(title, rows, fonts, show_count=False):
    """渲染一个分组：标题（+人数）+ N 行玩家（参照 nonebot draw_*_friends_status）"""
    canvas = Image.new('RGB', (WIDTH, SECTION_HEADER_H + ROW_STEP * len(rows) + SECTION_BOTTOM_PAD), BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    draw.text((22, 22), title, font=fonts['title'], fill=SECTION_TITLE_COLOR)
    if show_count:
        x = SECTION_COUNT_X.get(title, 22 + int(draw.textlength(title, font=fonts['title'])) + 8)
        draw.text((x, 25), f"({len(rows)})", font=fonts['count'], fill=COUNT_COLOR)
    for i, row in enumerate(rows):
        canvas.paste(row, (0, SECTION_HEADER_H + ROW_STEP * i))
    return canvas


async def _render_steam_style(data_dir, user_list, font_path=None, proxy=None,
                              avatar_frame_paths=None, covers=None,
                              parent_name=None, parent_sub=None):
    """渲染 Steam 玩家状态列表图片（steam风格）

    user_list 元素字段：sid/name/status/avatar_url/game/gameid/play_str/lastlogoff。
    不渲染群名、玩家 SteamID 与下次轮询时间，不展示游戏封面（covers 参数保留兼容）。
    """
    fonts = {
        'name':        load_font(20, 'bold', font_path),
        'title':       load_font(22, 'regular', font_path),
        'search':      load_font(20, 'regular', font_path),
        'status':      load_font(18, 'regular', font_path),
        'count':       load_font(18, 'regular', font_path),
        'parent_name': load_font(20, 'bold', font_path),
        'parent_sub':  load_font(18, 'light', font_path),
        'extra':       load_font(14, 'light', font_path),
    }
    logger.info(f"[Font] render_steam_list_image 传入字体路径: {font_path}，实际使用 MiSans（插件内置）")

    user_list = user_list or []
    # 分组（参照 nonebot-plugin-steam-info draw_friends_status 的 section 划分）
    gaming = [u for u in user_list if u.get('status') == 'playing']
    online = [u for u in user_list if u.get('status') in ('online', 'busy', 'snooze', 'away')]
    # 按 nonebot-plugin-steam-info 的 1, 2, 4, 5, 6, 3 顺序：在线 > 忙碌 > 打盹 > 离开
    online.sort(key=lambda u: {'online': 0, 'busy': 1, 'snooze': 2, 'away': 3}.get(u.get('status'), 9))
    offline = [u for u in user_list if u.get('status') == 'offline']
    error = [u for u in user_list if u.get('status') not in ('playing', 'online', 'busy', 'snooze', 'away', 'offline')]

    # 头像批量下载（沿用本地缓存），失败时使用默认头像
    tasks = [fetch_avatar(u.get('avatar_url'), data_dir, str(u.get('sid', '')), proxy=proxy) for u in user_list]
    avatars = await asyncio.gather(*tasks)
    default_avatar = _load_image(_res('unknown_avatar.jpg'))
    av_map = {
        str(u.get('sid')): (av or default_avatar) if (av or default_avatar) is not None else Image.new('RGBA', (1, 1))
        for u, av in zip(user_list, avatars)
    }

    def make_rows(group):
        return [draw_friend_status(u, av_map[str(u.get('sid'))], fonts, avatar_frame_paths) for u in group]

    # nonebot-plugin-steam-info 的“家长状态”条：bot 身份 + 监控信息）
    if parent_name is None:
        parent_name = 'Steam 状态监控'
    if parent_sub is None:
        parent_sub = f"监控中 · {len(user_list)} 位玩家"
    banner = draw_parent_status(parent_name, parent_sub, fonts)
    search_bar = draw_friends_search(fonts)

    sections = []
    if gaming:
        sections.append(draw_section('游戏中', make_rows(gaming), fonts))
    if online:
        sections.append(draw_section('在线好友', make_rows(online), fonts, show_count=True))
    if offline:
        sections.append(draw_section('离线', make_rows(offline), fonts, show_count=True))
    if error:
        sections.append(draw_section('异常', make_rows(error), fonts, show_count=True))

    # 拼合图片（参照 nonebot-plugin-steam-info draw_friends_status）
    height = banner.height + search_bar.height + sum(s.height for s in sections)
    canvas = Image.new('RGB', (WIDTH, height), BG_COLOR)
    canvas.paste(banner.convert('RGB'), (0, 0))
    canvas.paste(search_bar, (0, banner.height))
    y = banner.height + search_bar.height
    for i, section in enumerate(sections):
        canvas.paste(section, (0, y))
        y += section.height
        if i != len(sections) - 1:
            ImageDraw.Draw(canvas).rectangle([0, y - 1, WIDTH, y], fill=DIVIDER_COLOR)

    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    return buf.getvalue()


# ============ 旧版卡片风格（enable_steam_style 关闭时使用） ============

STEAM_BG_TOP = (44, 62, 80)
STEAM_BG_BOTTOM = (24, 32, 44)
CARD_BG = (38, 44, 56, 230)
CARD_RADIUS = 12
COVER_LIST_W, COVER_LIST_H = 50, 75
AVATAR_SIZE = 72
AVATAR_RADIUS = 12
CARD_HEIGHT = 110
CARD_MARGIN = 18
CARD_GAP = 12
FONT_PATH_BOLD = "msyhbd.ttc"
FONT_PATH = "msyh.ttc"

# 状态色渐变参数
GRADIENT_ALPHA_START = 77  # 30% of 255
GRADIENT_STOP_FRAC = 0.70  # 70% 处完全透明


def make_status_gradient(card_w, card_h, status_color, status):
    """生成卡片状态色左到右渐变 α 叠加层；离线不叠加；圆角裁剪匹配 CARD_RADIUS"""
    if status == 'offline':
        return None
    overlay = Image.new('RGBA', (card_w, card_h), (0, 0, 0, 0))
    r, g, b = status_color
    stop_x = int(card_w * GRADIENT_STOP_FRAC)
    for x in range(stop_x):
        ratio = 1.0 - (x / stop_x)
        alpha = int(GRADIENT_ALPHA_START * ratio)
        if alpha <= 0:
            continue
        for y in range(card_h):
            overlay.putpixel((x, y), (r, g, b, alpha))
    # 圆角裁剪：mask 与渐变原有 alpha 合并，保留渐变值且裁剪为圆角
    alpha = overlay.getchannel('A')
    mask = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_w-1, card_h-1), radius=CARD_RADIUS, fill=255)
    masked_alpha = Image.composite(alpha, Image.new('L', (card_w, card_h), 0), mask)
    overlay.putalpha(masked_alpha)
    return overlay


async def _render_card_style(data_dir, user_list, font_path=None, proxy=None,
                             avatar_frame_paths=None, covers=None):
    """旧版卡片风格渲染（原 render_steam_list_image 实现）"""
    # 字体
    if font_path is None:
        font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'NotoSansHans-Regular.otf')
    logger.info(f"[Font] render_steam_list_image 使用字体路径: {font_path}")
    try:
        font_title = ImageFont.truetype(font_path, 28)
        font_name = ImageFont.truetype(font_path, 22)
        font_game = ImageFont.truetype(font_path, 18)
        # 加粗用 Medium
        font_bold_path = font_path.replace('Regular', 'Medium')
        if os.path.exists(font_bold_path):
            font_status = ImageFont.truetype(font_bold_path, 16)
        else:
            font_status = ImageFont.truetype(font_path, 16)
        font_small = ImageFont.truetype(font_path, 14)
    except Exception as e:
        logger.warning(f"[Font] 加载字体失败: {e}")
        font_title = font_name = font_game = font_status = font_small = ImageFont.load_default()

    n = len(user_list)
    width = 600
    height = CARD_MARGIN + n * (CARD_HEIGHT + CARD_GAP) + CARD_MARGIN + 50
    img = Image.new('RGBA', (width, height), STEAM_BG_TOP)
    draw = ImageDraw.Draw(img)
    # 渐变背景
    for y in range(height):
        ratio = y / (height-1)
        r = int(STEAM_BG_TOP[0]*(1-ratio) + STEAM_BG_BOTTOM[0]*ratio)
        g = int(STEAM_BG_TOP[1]*(1-ratio) + STEAM_BG_BOTTOM[1]*ratio)
        b = int(STEAM_BG_TOP[2]*(1-ratio) + STEAM_BG_BOTTOM[2]*ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    # 标题
    title = "Steam 玩家状态列表"
    title_bbox = draw.textbbox((0,0), title, font=font_title)
    draw.text(((width-title_bbox[2]+title_bbox[0])//2, 12), title, font=font_title, fill=(255,255,255))
    # 卡片
    tasks = [fetch_avatar(u['avatar_url'], data_dir, u['sid'], proxy=proxy) for u in user_list]
    avatars = await asyncio.gather(*tasks)
    for idx, user in enumerate(user_list):
        top = CARD_MARGIN + idx * (CARD_HEIGHT + CARD_GAP) + 50
        left = CARD_MARGIN
        # 卡片底
        card = Image.new('RGBA', (width-2*CARD_MARGIN, CARD_HEIGHT), (0,0,0,0))
        card_draw = ImageDraw.Draw(card)
        card_draw.rounded_rectangle((0,0,width-2*CARD_MARGIN,CARD_HEIGHT), radius=CARD_RADIUS, fill=CARD_BG)
        # 叠加状态色渐变
        gradient = make_status_gradient(width-2*CARD_MARGIN, CARD_HEIGHT, get_status_color(user['status']), user['status'])
        if gradient is not None:
            card = Image.alpha_composite(card, gradient)
            card_draw = ImageDraw.Draw(card)
        # 头像（正方形+小圆角）
        avatar = avatars[idx]
        if avatar:
            avatar = avatar.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
            mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0,0,AVATAR_SIZE,AVATAR_SIZE), radius=AVATAR_RADIUS, fill=255)
            card.paste(avatar, (18, (CARD_HEIGHT-AVATAR_SIZE)//2), mask)
            # 头像框
            if avatar_frame_paths and user["sid"] in avatar_frame_paths:
                try:
                    frame_path = avatar_frame_paths[user["sid"]]
                    frame_size = AVATAR_SIZE + 12
                    frame_offset = (frame_size - AVATAR_SIZE) // 2
                    frame_img = Image.open(frame_path).convert("RGBA").resize((frame_size, frame_size), Image.LANCZOS)
                    frame_x = 18 - frame_offset
                    frame_y = (CARD_HEIGHT-AVATAR_SIZE)//2 - frame_offset
                    card.alpha_composite(frame_img, (frame_x, frame_y))
                except Exception as e:
                    print(f"[steam_list_render] 头像框渲染失败: {e}")
        # 顺序：玩家名（游戏时浅绿色），在线状态/游戏名（深绿色），上次在线/已游玩时间
        name_x = 18+AVATAR_SIZE+18
        name_y = 18
        # 玩家名颜色
        if user['status'] == 'playing':
            name_color = (227,255,194)
        else:
            name_color = get_name_color(user['status'])
        card_draw.text((name_x, name_y), user['name'], font=font_name, fill=name_color)
        # 在线状态/游戏名
        status_y = name_y + 28
        info_y = status_y  # 默认值，online/away/snooze/busy/offline无play_str时回退
        if user['status'] == 'playing':
            # 游戏名深绿色
            card_draw.text((name_x, status_y), f"正在玩：{user['game']}", font=font_game, fill=(131,175,80))
            # 已游玩时间
            info_y = status_y + 26
            card_draw.text((name_x, info_y), f"时长：{user['play_str']}", font=font_small, fill=(180,220,180))
        elif user['status'] in ('online', 'away', 'snooze', 'busy'):
            # 其它在线状态
            card_draw.text((name_x, status_y), get_status_text(user['status']), font=font_game, fill=get_status_color(user['status']))
            # 不显示时长
        elif user['status'] == 'offline' and user['play_str']:
            # 离线状态白色
            card_draw.text((name_x, status_y), "离线", font=font_game, fill=(255,255,255))
            info_y = status_y + 26
            card_draw.text((name_x, info_y), user['play_str'], font=font_small, fill=(180,180,180))
        elif user['status'] == 'error':
            card_draw.text((name_x, status_y), "异常", font=font_game, fill=(255,120,120))
            info_y = status_y + 26
            card_draw.text((name_x, info_y), user['play_str'], font=font_small, fill=(255,120,120))
        # 群号 + SteamID（alllist专用）
        sid_y = info_y + 22 if (user.get('play_str') or user['status'] != 'error') else status_y + 24
        if user.get('group_id'):
            card_draw.text((name_x, sid_y), f"群: {user['group_id']} | {user['sid']}", font=font_small, fill=(120,140,160))
        # 下次轮询时间
        if user.get('poll_str'):
            poll_y = sid_y + 18
            card_draw.text((name_x, poll_y), user['poll_str'], font=font_small, fill=(100,120,140))
        # 游戏竖版封面（右侧）
        if covers and user.get('sid') in covers:
            try:
                cover_img = Image.open(covers[user['sid']]).convert('RGBA')
                cw, ch = COVER_LIST_W, COVER_LIST_H
                cover_img = cover_img.resize((cw, ch), Image.LANCZOS)
                cx = width-2*CARD_MARGIN - cw - 18
                cy = (CARD_HEIGHT - ch) // 2
                # 画框边框
                card_draw.rounded_rectangle((cx-2, cy-2, cx+cw+2, cy+ch+2), radius=4, outline=(255,255,255,180), width=2)
                card.alpha_composite(cover_img, (cx, cy))
            except Exception as e:
                print(f"[steam_list_render] 封面渲染失败: {e}")
        img.alpha_composite(card, (left, top))
    # 统计
    stat_str = f"在线: {sum(1 for u in user_list if u['status'] in ('playing','online','away','snooze','busy'))} / 总数: {len(user_list)}"
    draw.text((width-220, height-36), stat_str, font=font_small, fill=(180,220,255))
    # 输出
    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


async def render_steam_list_image(data_dir, user_list, font_path=None, proxy=None,
                                  avatar_frame_paths=None, covers=None,
                                  parent_name=None, parent_sub=None, steam_style=True):
    """渲染 Steam 玩家状态列表图片。

    steam_style=True（默认，对应配置项 enable_steam_style）：使用 steam 风格；
    steam_style=False：使用旧版卡片风格（含封面/群号/SteamID/下次轮询显示）。
    """
    if steam_style:
        return await _render_steam_style(
            data_dir, user_list, font_path=font_path, proxy=proxy,
            avatar_frame_paths=avatar_frame_paths, covers=covers,
            parent_name=parent_name, parent_sub=parent_sub,
        )
    return await _render_card_style(
        data_dir, user_list, font_path=font_path, proxy=proxy,
        avatar_frame_paths=avatar_frame_paths, covers=covers,
    )
