from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class OpenBoxResult:
    text: str
    avatar_url: Optional[str] = None


_FIELD_NAMES = {
    "steamid": "SteamID64",
    "communityvisibilitystate": "资料可见性",
    "profilestate": "资料状态",
    "personaname": "昵称",
    "profileurl": "个人主页",
    "avatar": "头像(小)",
    "avatarmedium": "头像(中)",
    "avatarfull": "头像(大)",
    "avatarhash": "头像Hash",
    "personastate": "在线状态",
    "realname": "真实姓名",
    "primaryclanid": "主群组ID",
    "timecreated": "账号创建时间",
    "personastateflags": "在线状态Flags",
    "gameextrainfo": "正在玩的游戏",
    "gameid": "游戏AppID",
    "loccountrycode": "国家/地区",
    "locstatecode": "州/省代码",
    "loccityid": "城市ID",
    "lastlogoff": "上次离线时间",
    "commentpermission": "留言权限",
}

_PERSONA_STATES = {
    0: "离线",
    1: "在线",
    2: "忙碌",
    3: "离开",
    4: "打盹",
    5: "想交易",
    6: "想玩游戏",
}


def _format_player(player: Dict[str, Any]) -> OpenBoxResult:
    lines: List[str] = []
    for key, value in player.items():
        display_name = _FIELD_NAMES.get(key, key)
        if key == "personastate":
            value = _PERSONA_STATES.get(value, f"未知({value})")
        lines.append(f"{display_name}: {value}")
    return OpenBoxResult("\n".join(lines), player.get("avatarfull"))


async def query_openbox(steam_client, steamid: str) -> Optional[OpenBoxResult]:
    """查询并格式化玩家摘要，不依赖 AstrBot 消息类型。"""
    player = await steam_client.fetch_player_summary(steamid)
    return _format_player(player) if player else None


async def handle_openbox(steam_client, event, steamid: str):
    """AstrBot 兼容适配器；核心查询结果保持框架无关。"""
    from astrbot.api.message_components import Image, Plain

    try:
        result = await query_openbox(steam_client, steamid)
    except Exception as exc:
        yield event.plain_result(f"Steam API 请求失败: {exc}")
        return
    if result is None:
        yield event.plain_result("未查到该SteamID的信息")
        return

    chain = [Plain(result.text)]
    if result.avatar_url:
        chain.append(Image.fromURL(result.avatar_url))
    yield event.chain_result(chain)
