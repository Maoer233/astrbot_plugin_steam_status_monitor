import time
import httpx
from astrbot.api.message_components import Plain, Image

async def handle_openbox(self, event, steamid: str):
    '''查询并格式化展示指定SteamID的全部API返回信息（中文字段名，头像图片附加，位置ID合并，状态字段直观显示）'''
    url = (
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        f"?key={self.API_KEY}&steamids={steamid}"
    )
    field_map = {
        "steamid": "SteamID",
        "personaname": "昵称",
        "profileurl": "个人资料链接",
        "avatar": "头像",
        "personastate": "在线状态",
        "lastlogoff": "上次离线时间",
        "gameid": "当前游戏ID",
        "gameextrainfo": "当前游戏名",
        "communityvisibilitystate": "社区可见性",
        "profilestate": "资料状态",
        "timecreated": "账号创建时间",
        "realname": "真实姓名",
        "primaryclanid": "主要群组ID",
        "personastateflags": "状态标志",
        "commentpermission": "评论权限"
    }
    personastate_map = {
        0: "离线",
        1: "在线",
        2: "忙碌",
        3: "离开",
        4: "打盹",
        5: "想交易",
        6: "想游戏"
    }
    communityvisibilitystate_map = {
        1: "私密",
        3: "公开"
    }
    profilestate_map = {
        0: "未激活",
        1: "激活"
    }
    commentpermission_map = {
        0: "禁止评论",
        1: "允许好友评论",
        2: "所有人可评论"
    }
    try:
        proxy = getattr(self, 'proxy', None)
        async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                yield event.plain_result(f"API请求失败: HTTP {resp.status_code}")
                return
            data = resp.json()
            players = data.get('response', {}).get('players', [])
            if not players:
                yield event.plain_result("未查到该SteamID信息")
                return
            player = players[0]
            avatar_url = player.get("avatarfull") or player.get("avatar")
            loc_country = player.get("loccountrycode")
            loc_state = player.get("locstatecode")
            loc_city = player.get("loccityid")
            lines = []
            now = int(time.time())
            for k, v in player.items():
                if k in ("avatarmedium", "avatarfull", "loccountrycode", "locstatecode", "loccityid"):
                    continue
                if k == "avatar":
                    continue
                zh_key = field_map.get(k, k)
                if k == "personastate":
                    state_str = personastate_map.get(v, str(v))
                    if v == 0:
                        lastlogoff = player.get("lastlogoff")
                        if lastlogoff:
                            hours_ago = (now - int(lastlogoff)) / 3600
                            state_str += f"-上次在线-{hours_ago:.1f}小时前"
                    v = state_str
                elif k == "communityvisibilitystate":
                    v = communityvisibilitystate_map.get(v, str(v))
                elif k == "profilestate":
                    v = profilestate_map.get(v, str(v))
                elif k == "commentpermission":
                    v = commentpermission_map.get(v, str(v))
                elif k == "personastateflags":
                    v = str(v)
                elif k in ("lastlogoff", "timecreated") and isinstance(v, int):
                    from datetime import datetime
                    v = datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"{zh_key}: {v}")
            if loc_country or loc_state or loc_city:
                loc_str = "-".join(str(x) for x in [loc_country, loc_state, loc_city] if x)
                lines.append(f"位置ID: {loc_str}")
            msg_chain = []
            if avatar_url:
                msg_chain.append(Image.fromURL(avatar_url, width=64, height=64))
            msg_chain.append(Plain("SteamID详细信息：\n" + "\n".join(lines)))
            yield event.chain_result(msg_chain)
    except Exception as e:
        yield event.plain_result(f"请求异常: {e}")
