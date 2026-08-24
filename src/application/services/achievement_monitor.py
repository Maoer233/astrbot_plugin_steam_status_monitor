import json
import os
import asyncio
import httpx
import io
import aiohttp
from PIL import Image, ImageDraw, ImageFont
from typing import Set, Optional, Dict, Any

from ...shared.paths import FONTS_DIR
from ...shared.logging import logger
from ...shared.network import aiohttp_connector, httpx_client_kwargs


class AchievementMonitor:
    def __init__(self, data_dir: str, steam_api_base: str = "https://api.steampowered.com", proxy: str = None):
        self.data_dir = data_dir
        self.steam_api_base = (steam_api_base or "https://api.steampowered.com").rstrip("/")
        self.proxy = proxy
        self.initial_achievements = {}  # {(group_id, steamid, appid): set_of_achievement_names}
        os.makedirs(data_dir, exist_ok=True)
        self.achievements_file = os.path.join(data_dir, "achievements_cache.json")
        self._load_achievements_cache()
        self.details_cache = {}  # (group_id, appid) -> details 缓存
        self._load_blacklist()
    
    def _blacklist_path(self):
        return os.path.join(self.data_dir, "achievement_blacklist.json")

    def _load_blacklist(self):
        path = self._blacklist_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.achievement_blacklist = set(json.load(f))
            except Exception:
                self.achievement_blacklist = set()
        else:
            self.achievement_blacklist = set()

    def _save_blacklist(self):
        path = self._blacklist_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(list(self.achievement_blacklist), f, ensure_ascii=False)
        except Exception:
            pass

    def _load_achievements_cache(self):
        """加载成就缓存"""
        try:
            if os.path.exists(self.achievements_file):
                with open(self.achievements_file, 'r', encoding='utf-8') as f:
                    self.initial_achievements = json.load(f)
        except Exception as e:
            print(f"加载成就缓存失败: {e}")
            self.initial_achievements = {}
    
    def _save_achievements_cache(self):
        """保存成就缓存"""
        try:
            with open(self.achievements_file, 'w', encoding='utf-8') as f:
                json.dump(self.initial_achievements, f, ensure_ascii=False)
        except Exception as e:
            print(f"保存成就缓存失败: {e}")
    
    async def get_player_achievements(self, api_key: str, group_id: str, steamid: str, appid: int) -> Optional[Set[str]]:
        """
        获取指定玩家在指定游戏中的已解锁成就 apiname 集合，失败自动尝试多语言（中文、英文），每种语言最多重试3次
        """
        # 黑名单机制
        if hasattr(self, 'achievement_blacklist') and str(appid) in self.achievement_blacklist:
            return None
        url = f"{self.steam_api_base}/ISteamUserStats/GetPlayerAchievements/v1/"
        lang_list = ["schinese", "english", "en"]
        all_failed = True
        for lang in lang_list:
            params = {
                "key": api_key,
                "steamid": steamid,
                "appid": appid,
                "l": lang
            }
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                        response = await client.get(url, params=params)
                        if response.status_code == 200:
                            data = response.json()
                            if "playerstats" in data and "achievements" in data["playerstats"]:
                                achievements = data["playerstats"]["achievements"]
                                unlocked = {
                                    ach["apiname"] for ach in achievements 
                                    if ach.get("achieved", 0) == 1
                                }
                                # 检查是否有描述字段且不全为空
                                has_desc = any(ach.get("description") for ach in achievements)
                                if has_desc:
                                    all_failed = False
                                    return unlocked
                                # 否则继续尝试下一个语言
                        elif response.status_code == 401:
                            print(f"无权限获取玩家 {steamid} 的游戏 {appid} 成就数据 (隐私设置)")
                            return None
                        else:
                            print(f"获取成就数据失败: HTTP {response.status_code} (第{attempt+1}次, lang={lang})")
                except Exception as e:
                    print(f"请求异常: {e} (第{attempt+1}次, lang={lang})")
        # 如果全部失败，加入黑名单
        if all_failed:
            print(f"游戏 {appid} 已加入成就黑名单（无成就或API异常）")
            self.achievement_blacklist.add(str(appid))
            self._save_blacklist()
        return None

    async def get_achievement_details(self, group_id: str, appid: int, lang: str = "schinese", api_key: str = "", steamid: str = "") -> Dict[str, Any]:
        """
        获取游戏全部成就的详细信息（apiname -> {name, description, icon, percent}）
        icon/icongray 字段自动拼接为标准URL（如不是完整URL）
        若 description 为空，自动尝试多语言
        """
        # 黑名单机制
        if hasattr(self, 'achievement_blacklist') and str(appid) in self.achievement_blacklist:
            return {}
        # 优先用缓存
        cache_key = (group_id, appid)
        if cache_key in self.details_cache:
            return self.details_cache[cache_key]
        lang_list = [lang, "schinese", "english", "en"]
        url_stats = f"{self.steam_api_base}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid={appid}"
        details = {}
        for try_lang in lang_list:
            url = f"{self.steam_api_base}/ISteamUserStats/GetSchemaForGame/v2/?appid={appid}&key={api_key}&l={try_lang}"
            try:
                async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                    # 成就元数据
                    resp = await client.get(url)
                    if resp.status_code == 400:
                        print(f"获取成就schema失败: HTTP 400，通常为appid错误或该游戏无成就，appid={appid}，尝试降级用GetPlayerAchievements")
                        if not api_key or not steamid:
                            print("降级拉取成就详情失败：未传递api_key或steamid参数")
                            return {}
                        # 降级多语言重试
                        for lang2 in lang_list:
                            params = {
                                "key": api_key,
                                "steamid": steamid,
                                "appid": appid,
                                "l": lang2
                            }
                            resp2 = await client.get(
                                f"{self.steam_api_base}/ISteamUserStats/GetPlayerAchievements/v1/",
                                params=params,
                            )
                            if resp2.status_code == 200:
                                try:
                                    data = resp2.json()
                                    if "playerstats" in data and "achievements" in data["playerstats"]:
                                        for ach in data["playerstats"]["achievements"]:
                                            details[ach["apiname"]] = {
                                                "name": ach.get("name", ach["apiname"]),
                                                "description": ach.get("description", ""),
                                                "icon": None,
                                                "icon_gray": None,
                                                "percent": None
                                            }
                                        # 检查是否有描述
                                        if any(a.get("description") for a in data["playerstats"]["achievements"]):
                                            break
                                except Exception as e:
                                    print(f"降级解析GetPlayerAchievements json失败: {e} resp.text={resp2.text[:200]}")
                            else:
                                print(f"降级GetPlayerAchievements失败: HTTP {resp2.status_code}")
                        return details
                    if resp.status_code != 200:
                        print(f"获取成就schema失败: HTTP {resp.status_code} url={url}")
                        continue
                    try:
                        schema = resp.json()
                    except Exception as e:
                        print(f"解析成就schema json失败: {e} resp.text={resp.text[:200]}")
                        continue
                    achievements = {}
                    if "game" in schema and "availableGameStats" in schema["game"]:
                        for ach in schema["game"]["availableGameStats"].get("achievements", []):
                            def to_icon_url(val):
                                if not val:
                                    return None
                                if val.startswith("http://") or val.startswith("https://"):
                                    return val
                                return f"https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/{appid}/{val}.jpg"
                            achievements[ach["name"]] = {
                                "name": ach.get("displayName", ach["name"]),
                                "description": ach.get("description", ""),
                                "icon": to_icon_url(ach.get("icon")),
                                "icon_gray": to_icon_url(ach.get("icongray"))
                            }
                    resp2 = await client.get(url_stats)
                    if resp2.status_code != 200:
                        print(f"获取成就解锁率失败: HTTP {resp2.status_code} url={url_stats}")
                        percents = {}
                    else:
                        try:
                            stats = resp2.json()
                        except Exception as e:
                            print(f"解析成就解锁率json失败: {e} resp.text={resp2.text[:200]}")
                            stats = {}
                        percents = {}
                        if "achievementpercentages" in stats and "achievements" in stats["achievementpercentages"]:
                            for ach in stats["achievementpercentages"]["achievements"]:
                                percents[ach["name"]] = ach.get("percent")
                    for apiname, ach in achievements.items():
                        details[apiname] = {
                            "name": ach["name"],
                            "description": ach["description"],
                            "icon": ach["icon"],
                            "icon_gray": ach["icon_gray"],
                            "percent": percents.get(apiname)
                        }
                    # 检查是否有描述
                    if any(a.get("description") for a in achievements.values()):
                        break
            except Exception as e:
                print(f"获取成就详细信息异常: {e}")
        # 获取成功后写入缓存
        self.details_cache[cache_key] = details
        return details
    
    async def check_new_achievements(self, api_key: str, group_id: str, steamid: str, appid: int, player_name: str, game_name: str) -> Set[str]:
        key = (group_id, steamid, appid)
        current_achievements = await self.get_player_achievements(api_key, group_id, steamid, appid)
        if current_achievements is None:
            return set()
        initial_achievements = self.initial_achievements.get(str(key), set())
        new_achievements = current_achievements - set(initial_achievements)
        self.initial_achievements[str(key)] = list(current_achievements)
        self._save_achievements_cache()
        return new_achievements
    
    def clear_game_achievements(self, group_id: str, steamid: str, appid: str):
        key = (group_id, steamid, appid)
        if str(key) in self.initial_achievements:
            del self.initial_achievements[str(key)]
            self._save_achievements_cache()

    def render_achievement_message(self, achievement_details: dict, new_achievements: set, player_name: str = "") -> str:
        lines = []
        trophy = "🏆"
        for apiname in new_achievements:
            detail = achievement_details.get(apiname)
            if not detail:
                continue
            icon_url = detail.get("icon")
            percent = detail.get("percent")
            try:
                percent_val = float(percent) if percent is not None else None
            except (ValueError, TypeError):
                percent_val = None
            percent_str = f"{percent_val:.1f}%" if percent_val is not None else "未知"
            name = detail.get("name", apiname)
            desc = detail.get("description", "")
            lines.append(
                f"{trophy}{player_name}解锁了成就\n"
                f"| ![{name}]({icon_url}) | <div align='left'>**{name}**<br>{desc}<br>全球解锁率：{percent_str}</div> |\n"
                f"|:---:|:---|\n"
            )
        return "\n".join(lines)
    
    def _wrap_text(self, text, font, max_width):
        """自动换行，返回行列表"""
        if not text:
            return [""]
        lines = []
        line = ""
        dummy_img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(dummy_img)
        for char in text:
            bbox = draw.textbbox((0, 0), line + char, font=font)
            width = bbox[2] - bbox[0]
            if width <= max_width:
                line += char
            else:
                if line:
                    lines.append(line)
                line = char
        if line:
            lines.append(line)
        return lines

    async def render_achievement_image(self, achievement_details: dict, new_achievements: set, player_name: str = "", steamid: str = None, appid: int = None, unlocked_set: set = None, font_path=None) -> bytes:
        # 风格化：圆角卡片、icon透明、自动换行、无表情符号、官方风格进度条
        width = 420
        padding_v = 18
        padding_h = 18
        card_gap = 14
        card_radius = 9  # 圆角减半
        card_inner_bg = (38, 44, 56, 220)
        card_base_bg = (35, 38, 46, 255)
        progress_color = (49, 52, 62, 255)
        icon_size = 64
        icon_margin_right = 16
        text_margin_top = 10
        max_text_width = width - padding_h * 2 - icon_size - icon_margin_right - 18

        # 字体路径
        fonts_dir = str(FONTS_DIR)
        # 优先使用传入 font_path
        font_regular = font_path or os.path.join(fonts_dir, 'NotoSansHans-Regular.otf')
        font_medium = font_regular.replace('Regular', 'Medium') if 'Regular' in font_regular else os.path.join(fonts_dir, 'NotoSansHans-Medium.otf')
        # 修正：确保字体路径为绝对路径
        if not os.path.isabs(font_regular):
            font_regular = os.path.join(fonts_dir, os.path.basename(font_regular))
        if not os.path.isabs(font_medium):
            font_medium = os.path.join(fonts_dir, os.path.basename(font_medium))
        if not os.path.exists(font_regular):
            font_regular = os.path.join(fonts_dir, 'NotoSansHans-Regular.otf')
        if not os.path.exists(font_medium):
            font_medium = os.path.join(fonts_dir, 'NotoSansHans-Medium.otf')
        try:
            font_title = ImageFont.truetype(font_medium, 20)
            font_game = ImageFont.truetype(font_regular, 15)
            font_name = ImageFont.truetype(font_medium, 16)
            font_desc = ImageFont.truetype(font_regular, 13)
            font_percent = ImageFont.truetype(font_regular, 12)
            font_game_small = ImageFont.truetype(font_regular, 12)
            font_time = ImageFont.truetype(font_regular, 10)
        except Exception:
            font_title = font_game = font_name = font_desc = font_percent = font_game_small = font_time = ImageFont.load_default()

        # 1. 统计全成就进度（总进度，和本次解锁无关）
        if unlocked_set is None:
            unlocked_set = set()
            if steamid is not None and appid is not None:
                unlocked_set = await self.get_player_achievements(os.environ.get('STEAM_API_KEY', ''), steamid, appid) or set()
        unlocked_achievements = len(unlocked_set)
        total_achievements = len(achievement_details)
        progress_percent = int(unlocked_achievements / total_achievements * 100) if total_achievements else 0

        # 标题与游戏名
        title_text = f"{player_name} 解锁新成就"
        game_name = ""
        for detail in achievement_details.values():
            if detail and detail.get("name"):
                game_name = detail.get("game_name", "") or detail.get("game", "") or ""
                break
        if not game_name:
            game_name = next((d.get("game_name") for d in achievement_details.values() if d and d.get("game_name")), "")
        if not game_name:
            game_name = "未知游戏"

        # 获取当前时间字符串
        import datetime
        now_str = datetime.datetime.now().strftime("%m-%d %H:%M")

        # 预留高度：标题+游戏名+全成就进度条
        dummy_img = Image.new("RGB", (10, 10))
        dummy_draw = ImageDraw.Draw(dummy_img)
        title_bbox = dummy_draw.textbbox((0, 0), title_text, font=font_title)
        title_h = title_bbox[3] - title_bbox[1]
        # 游戏名字体更小
        game_bbox = dummy_draw.textbbox((0, 0), game_name, font=font_game_small)
        game_h = game_bbox[3] - game_bbox[1]
        # 时间字体更小
        time_bbox = dummy_draw.textbbox((0, 0), now_str, font=font_time)
        time_w = time_bbox[2] - time_bbox[0]
        time_h = time_bbox[3] - time_bbox[1]
        progress_bar_h = 12
        progress_bar_margin = 8
        # 增加玩家名和游戏名之间的间距
        title_game_gap = 8
        header_h = title_h + title_game_gap + game_h + progress_bar_h + progress_bar_margin * 3

        # 预处理每个成就卡片的文本和高度
        card_heights = []
        card_texts = []
        percents = []
        for apiname in new_achievements:
            detail = achievement_details.get(apiname)
            if not detail:
                # 占位，防止后续索引错位
                card_heights.append(80)
                card_texts.append(([''], [''], '未知'))
                percents.append(0)
                continue
            name = detail.get("name", apiname)
            desc = detail.get("description", "")
            percent = detail.get("percent")
            try:
                percent_val = float(percent) if percent is not None else None
            except (ValueError, TypeError):
                percent_val = None
            percent_str = f"{percent_val:.1f}%" if percent_val is not None else "未知"
            percent_num = percent_val if percent_val is not None else 0
            # 自动换行
            name_lines = self._wrap_text(name, font_name, max_text_width)
            desc_lines = self._wrap_text(desc, font_desc, max_text_width)
            # 估算卡片高度
            card_h = max(icon_size + 24, len(name_lines)*22 + len(desc_lines)*18 + 60)
            card_heights.append(card_h)
            card_texts.append((name_lines, desc_lines, percent_str))
            percents.append(percent_num)
        total_height = int(padding_v + header_h + padding_v + sum(card_heights) + card_gap * (len(card_heights) - 1) + padding_v)

        img = Image.new('RGBA', (width, total_height), (20, 26, 33, 255))
        draw = ImageDraw.Draw(img)

        # 标题区域
        # 玩家名解锁新成就（大字）
        draw.text((padding_h, padding_v), title_text, fill=(255, 255, 255), font=font_title)
        # 游戏名（更小更淡，换行在下方，增加间距）
        draw.text((padding_h, padding_v + title_h + title_game_gap), game_name, fill=(160, 160, 160), font=font_game_small)
        # 当前时间（右上角，更小更淡）
        draw.text((width - padding_h - time_w, padding_v), now_str, fill=(168, 168, 168), font=font_time)

        # 全成就进度条
        bar_x = padding_h
        bar_y = padding_v + title_h + title_game_gap + game_h + progress_bar_margin
        bar_w = width - padding_h * 2
        bar_h = progress_bar_h
        bar_radius = bar_h // 2
        # 底色
        draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=bar_radius, fill=(60, 62, 70, 180))
        # 高亮色
        progress_fill = (26, 159, 255, 255)
        fill_w = int(bar_w * progress_percent / 100)
        if fill_w > 0:
            draw.rounded_rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), radius=bar_radius, fill=progress_fill)
        # 文字
        progress_text = f"{unlocked_achievements}/{total_achievements} ({progress_percent}%)"
        progress_text_bbox = draw.textbbox((0, 0), progress_text, font=font_percent)
        progress_text_w = progress_text_bbox[2] - progress_text_bbox[0]
        draw.text((bar_x + bar_w - progress_text_w - 6, bar_y - 2), progress_text, fill=(142, 207, 255), font=font_percent)

        y = padding_v + header_h + padding_v

        connector = aiohttp_connector()
        async with aiohttp.ClientSession(connector=connector) as session:
            idx = 0
            for apiname in new_achievements:
                detail = achievement_details.get(apiname)
                if not detail:
                    y += card_heights[idx] + card_gap
                    idx += 1
                    continue
                name_lines, desc_lines, percent_str = card_texts[idx]
                percent_num = percents[idx]
                card_h = card_heights[idx]
                card_x0 = padding_h
                card_x1 = width - padding_h
                card_y0 = int(y)
                card_y1 = int(y + card_h)
                card_w = card_x1 - card_x0
                card_hh = card_y1 - card_y0

                card_bg = Image.new("RGBA", (card_w, card_hh), card_base_bg)

                card = Image.new("RGBA", (card_w, card_hh), (0, 0, 0, 0))
                mask = Image.new("L", (card_w, card_hh), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rounded_rectangle((0, 0, card_w, card_hh), radius=card_radius, fill=255)
                card.paste(card_bg, (0, 0), mask)

                # 如果全球解锁率低于10%，添加淡金色描边
                if percent_num < 10:
                    border_draw = ImageDraw.Draw(card)
                    gold_color = (255, 215, 128, 255)  # 淡金色
                    border_width = 3
                    border_rect = (border_width//2, border_width//2, card_w - border_width//2 - 1, card_hh - border_width//2 - 1)
                    border_draw.rounded_rectangle(border_rect, radius=card_radius, outline=gold_color, width=border_width)

                # 进度条（卡片底部，横向，圆角）
                bar_margin_x = 18
                bar_margin_y = 12
                bar_height = 8
                bar_radius2 = bar_height // 2
                bar_x0 = bar_margin_x
                bar_x1 = card_w - bar_margin_x
                bar_y1 = card_hh - bar_margin_y
                bar_y0 = bar_y1 - bar_height
                card_draw = ImageDraw.Draw(card)
                card_draw.rounded_rectangle((bar_x0, bar_y0, bar_x1, bar_y1), radius=bar_radius2, fill=(60, 62, 70, 180))
                if percent_num > 0:
                    fill_w = int((bar_x1 - bar_x0) * percent_num / 100)
                    if fill_w > 0:
                        card_draw.rounded_rectangle((bar_x0, bar_y0, bar_x0 + fill_w, bar_y1), radius=bar_radius2, fill=(26, 159, 255, 255))

                # 半透明前景
                card_fg = Image.new("RGBA", (card_w, card_hh), card_inner_bg)
                card.paste(card_fg, (0, 0), mask)

                img.alpha_composite(card, (card_x0, card_y0))

                # icon
                icon_url = detail.get("icon")
                icon_img = None
                if icon_url:
                    try:
                        async with session.get(icon_url, proxy=self.proxy) as response:
                            if response.status == 200:
                                icon_data = await response.read()
                                icon_img = Image.open(io.BytesIO(icon_data)).convert("RGBA")
                                icon_img = icon_img.resize((icon_size, icon_size), Image.LANCZOS)
                                mask_icon = Image.new("L", (icon_size, icon_size), 0)
                                ImageDraw.Draw(mask_icon).rounded_rectangle((0, 0, icon_size, icon_size), 12, fill=255)
                                icon_img.putalpha(mask_icon)
                    except Exception:
                        pass
                icon_x = card_x0 + 12
                icon_y = card_y0 + (card_h - icon_size) // 2
                if icon_img:
                    if percent_num < 10:
                        # 更小更集中的金色发光，不遮挡图标
                        glow_size = 10  # 范围更小
                        canvas_size = icon_size + 2 * glow_size
                        icon_canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
                        glow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
                        glow_draw = ImageDraw.Draw(glow)
                        for r in range(canvas_size//2, icon_size//2, -1):
                            alpha = int(120 * (canvas_size//2 - r) / glow_size)
                            color = (255, 220, 60, max(0, alpha))
                            glow_draw.ellipse([
                                canvas_size//2 - r, canvas_size//2 - r,
                                canvas_size//2 + r, canvas_size//2 + r
                            ], outline=None, fill=color)
                        icon_canvas = Image.alpha_composite(icon_canvas, glow)
                        # 图标始终在最上层
                        icon_canvas.paste(icon_img, (glow_size, glow_size), icon_img)
                        img.alpha_composite(icon_canvas, (icon_x - glow_size, icon_y - glow_size))
                    else:
                        img.alpha_composite(icon_img, (icon_x, icon_y))

                # 右侧文本
                text_x = icon_x + icon_size + icon_margin_right
                text_y = card_y0 + text_margin_top
                for i, line in enumerate(name_lines):
                    draw.text((text_x, text_y + i * 22), line, fill=(255, 255, 255), font=font_name)
                desc_y = text_y + len(name_lines) * 22 + 2
                for i, line in enumerate(desc_lines):
                    draw.text((text_x, desc_y + i * 18), line, fill=(187, 187, 187), font=font_desc)
                percent_y = desc_y + len(desc_lines) * 18 + 6

                # 进度条行
                percent_label = "全球解锁率："
                percent_label_font = font_percent
                percent_value_str = percent_str
                percent_value_font = font_percent
                percent_label_bbox = draw.textbbox((0, 0), percent_label, font=percent_label_font)
                label_w = percent_label_bbox[2] - percent_label_bbox[0]
                bar_x = text_x + label_w + 4
                bar_y = percent_y + 4
                bar_height = 10
                bar_length = card_x1 - bar_x - 48
                bar_radius3 = bar_height // 2
                # 发光效果：全球解锁率<10%时，label和value发光
                if percent_num < 10:
                    # 绘制发光背景
                    glow_color = (255, 220, 60, 120)
                    glow_radius = 16
                    # label发光
                    for r in range(glow_radius, 0, -4):
                        draw.text((text_x, percent_y), percent_label, fill=(255, 220, 60, int(60 * r / glow_radius)), font=percent_label_font)
                    # value发光
                    percent_value_bbox = draw.textbbox((0, 0), percent_value_str, font=percent_value_font)
                    value_w = percent_value_bbox[2] - percent_value_bbox[0]
                    value_x = bar_x + bar_length + 8
                    value_y = percent_y
                    for r in range(glow_radius, 0, -4):
                        draw.text((value_x, value_y), percent_value_str, fill=(255, 220, 60, int(60 * r / glow_radius)), font=percent_value_font)
                # 正常文字
                draw.text((text_x, percent_y), percent_label, fill=(142, 207, 255) if percent_num >= 10 else (255, 220, 60), font=percent_label_font)
                draw.rounded_rectangle(
                    (bar_x, bar_y, bar_x + bar_length, bar_y + bar_height),
                    radius=bar_radius3,
                    fill=(60, 62, 70, 180)
                )
                if percent_num > 0:
                    fill_w = int(bar_length * percent_num / 100)
                    if fill_w > 0:
                        draw.rounded_rectangle(
                            (bar_x, bar_y, bar_x + fill_w, bar_y + bar_height),
                            radius=bar_radius3,
                            fill=(26, 159, 255, 255)
                        )
                percent_value_bbox = draw.textbbox((0, 0), percent_value_str, font=percent_value_font)
                value_w = percent_value_bbox[2] - percent_value_bbox[0]
                value_x = bar_x + bar_length + 8
                value_y = percent_y
                draw.text((value_x, value_y), percent_value_str, fill=(142, 207, 255) if percent_num >= 10 else (255, 220, 60), font=percent_value_font)

                y += card_h + card_gap
                idx += 1

        img = img.convert("RGB")
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        return img_byte_arr.getvalue()
