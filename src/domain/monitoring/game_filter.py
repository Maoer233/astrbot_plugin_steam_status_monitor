def should_skip_game(config, gameid) -> bool:
    """按黑白名单判断是否跳过该游戏的监控/播报。"""
    if not gameid:
        return False
    mode = (config or {}).get("game_filter_mode", "全部游戏")
    if mode == "全部游戏":
        return False
    ids_str = (config or {}).get("game_filter_ids", "") or ""
    if not str(ids_str).strip():
        return False
    try:
        filter_ids = [x.strip() for x in str(ids_str).split(",") if x.strip()]
    except Exception:
        return False
    if mode == "白名单":
        return str(gameid) not in filter_ids
    if mode == "黑名单":
        return str(gameid) in filter_ids
    return False
