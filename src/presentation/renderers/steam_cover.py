import json
from urllib.parse import quote

import httpx


STEAM_STORE_BROWSE_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
STEAM_STORE_ASSET_BASE = "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps"


async def get_steam_library_cover_url(appid, api_key, proxy=None, steam_api_base=None):
    """Return Steam's high-resolution vertical library cover URL for an app."""
    if not appid or not api_key:
        return None

    input_json = {
        "ids": [{"appid": str(appid)}],
        "context": {"language": "schinese", "country_code": "CN"},
        "data_request": {"include_assets": True},
    }
    params = {
        "key": api_key,
        "input_json": json.dumps(input_json, ensure_ascii=False, separators=(",", ":")),
    }
    try:
        async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
            browse_url = f"{(steam_api_base or 'https://api.steampowered.com').rstrip('/')}/IStoreBrowseService/GetItems/v1/"
            response = await client.get(browse_url, params=params)
        if response.status_code != 200:
            print(
                "[get_steam_library_cover_url] Steam 接口请求失败: "
                f"appid={appid}, 状态码={response.status_code}"
            )
            return None

        store_items = response.json().get("response", {}).get("store_items", [])
        item = next(
            (entry for entry in store_items if str(entry.get("appid", entry.get("id"))) == str(appid)),
            None,
        )
        assets_path = (item or {}).get("assets", {}).get("library_capsule_2x")
        if not assets_path:
            print(f"[get_steam_library_cover_url] 未获取到 library_capsule_2x 字段: appid={appid}")
            return None
        return f"{STEAM_STORE_ASSET_BASE}/{appid}/{quote(assets_path, safe='/')}"
    except Exception as e:
        print(
            "[get_steam_library_cover_url] Steam 接口异常: "
            f"appid={appid}, 异常类型={type(e).__name__}"
        )
        return None
