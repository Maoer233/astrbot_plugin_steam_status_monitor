from datetime import datetime, timedelta


def _build_display_names(plugin):
    names = {}
    for info in (getattr(plugin, "_bind_data", {}) or {}).values():
        sid = str(info.get("sid", ""))
        nickname = info.get("nickname", "")
        if sid and nickname and nickname != "*":
            names.setdefault(sid, nickname)

    for states in (getattr(plugin, "group_last_states", {}) or {}).values():
        for sid, state in list(states.items()):
            sid = str(sid)
            if sid not in names and state.get("name"):
                names[sid] = state["name"]
    return names


def build_dashboard_stats(plugin, today, last_update):
    groups = getattr(plugin, "group_steam_ids", {}) or {}
    all_sids = {str(sid) for sids in groups.values() for sid in list(sids)}
    bind_data = getattr(plugin, "_bind_data", {}) or {}
    today_records = (getattr(plugin, "play_records", {}) or {}).get(today, {})
    display_names = _build_display_names(plugin)

    game_totals = {}
    player_totals = {}
    for sid, sid_data in list(today_records.items()):
        total = 0
        for gid, game_info in list(sid_data.items()):
            minutes = game_info.get("minutes", 0) if isinstance(game_info, dict) else 0
            name = game_info.get("name", gid) if isinstance(game_info, dict) else str(gid)
            aggregate = game_totals.setdefault(
                gid, {"name": name, "minutes": 0, "player_count": 0}
            )
            aggregate["minutes"] += minutes
            aggregate["player_count"] += 1
            total += minutes
        if total > 0:
            player_totals[str(sid)] = total

    top_games = sorted(
        list(game_totals.values()), key=lambda item: -item["minutes"]
    )[:10]
    top_players = sorted(
        [
            {"sid": sid, "name": display_names.get(sid, sid), "minutes": minutes}
            for sid, minutes in player_totals.items()
        ],
        key=lambda item: -item["minutes"],
    )[:10]

    online_count = 0
    players = []
    seen_players = set()
    for states in (getattr(plugin, "group_last_states", {}) or {}).values():
        for sid, state in list(states.items()):
            sid = str(sid)
            if sid in seen_players:
                continue
            seen_players.add(sid)
            if state.get("gameid") or state.get("personastate", 0) > 0:
                online_count += 1
            players.append({
                "sid": sid,
                "name": display_names.get(sid, sid),
                "gameid": state.get("gameid", ""),
                "game": state.get("gameextrainfo", ""),
                "personastate": state.get("personastate", 0),
                "avatar_url": state.get("avatarfull") or state.get("avatar", ""),
            })

    return {
        "total_groups": len(groups),
        "total_players": len(all_sids),
        "total_bindings": len(bind_data),
        "today_active_players": len(today_records),
        "online_players": online_count,
        "top_games_today": top_games,
        "top_players_today": top_players,
        "players": players,
        "last_update": last_update,
    }


def build_groups(plugin):
    display_names = _build_display_names(plugin)
    groups = getattr(plugin, "group_steam_ids", {}) or {}
    last_states = getattr(plugin, "group_last_states", {}) or {}
    result = {}
    for group_id, steam_ids in list(groups.items()):
        states = last_states.get(group_id, {})
        result[group_id] = [
            {
                "sid": str(sid),
                "name": display_names.get(str(sid), str(sid)),
                "gameid": states.get(str(sid), {}).get("gameid", ""),
                "game": states.get(str(sid), {}).get("gameextrainfo", ""),
                "personastate": states.get(str(sid), {}).get("personastate", 0),
            }
            for sid in list(steam_ids)
        ]
    return result


def build_player_search_index(plugin):
    display_names = _build_display_names(plugin)
    groups = getattr(plugin, "group_steam_ids", {}) or {}
    return [
        {
            "sid": str(sid),
            "name": display_names.get(str(sid), str(sid)),
            "group_id": group_id,
        }
        for group_id, steam_ids in list(groups.items())
        for sid in list(steam_ids)
    ]


def build_heatmap_data(plugin, period, now):
    end_date = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if end_date <= now:
        end_date += timedelta(days=1)
    start_date = end_date - timedelta(days=period)
    start_key = start_date.strftime("%Y-%m-%d")
    end_key = end_date.strftime("%Y-%m-%d")

    heatmap_daily = {}
    player_minutes = {}
    session_records = getattr(plugin, "session_records", {}) or {}
    for sid, sessions in list(session_records.items()):
        total = 0
        for session in list(sessions):
            date_key = session.get("date", "")
            if start_key <= date_key <= end_key:
                minutes = session.get("duration_min", 0)
                heatmap_daily[date_key] = heatmap_daily.get(date_key, 0) + minutes
                total += minutes
        if total > 0:
            player_minutes[str(sid)] = total

    play_records = getattr(plugin, "play_records", {}) or {}
    day = start_date
    while day <= end_date:
        date_key = day.strftime("%Y-%m-%d")
        day_records = play_records.get(date_key, {})
        for sid, games in list(day_records.items()):
            minutes = sum(
                game_info.get("minutes", 0) if isinstance(game_info, dict) else 0
                for game_info in list(games.values())
            )
            if minutes > 0:
                heatmap_daily[date_key] = heatmap_daily.get(date_key, 0) + minutes
                sid = str(sid)
                player_minutes[sid] = player_minutes.get(sid, 0) + minutes
        heatmap_daily.setdefault(date_key, 0)
        day += timedelta(days=1)

    display_names = _build_display_names(plugin)
    players = sorted(
        [
            {
                "sid": sid,
                "name": display_names.get(sid, sid),
                "total_minutes": minutes,
            }
            for sid, minutes in player_minutes.items()
        ],
        key=lambda item: -item["total_minutes"],
    )
    return {"heatmap_data": heatmap_daily, "players": players}
