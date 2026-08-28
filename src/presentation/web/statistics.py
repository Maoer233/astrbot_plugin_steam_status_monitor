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


def _heatmap_group_sids(plugin, group_id):
    groups = getattr(plugin, "group_steam_ids", {}) or {}
    if group_id:
        return {str(sid) for sid in groups.get(group_id, [])}
    return {str(sid) for steam_ids in groups.values() for sid in steam_ids}


def _build_heatmap_contributions(plugin, start_key, end_key, allowed_sids):
    """按玩家和日期聚合时长，同日优先采用精确会话以避免重复计数。"""

    contributions = {}
    session_days = set()
    session_records = getattr(plugin, "session_records", {}) or {}
    for raw_sid, sessions in list(session_records.items()):
        sid = str(raw_sid)
        if sid not in allowed_sids:
            continue
        for session in list(sessions):
            date_key = session.get("date", "")
            if not start_key <= date_key <= end_key:
                continue
            minutes = max(0.0, float(session.get("duration_min", 0) or 0))
            if minutes <= 0:
                continue
            player_day = contributions.setdefault(date_key, {}).setdefault(
                sid, {"minutes": 0, "games": {}}
            )
            player_day["minutes"] += minutes
            game_id = str(session.get("gameid", ""))
            game_name = session.get("game_name", game_id or "未知游戏")
            game = player_day["games"].setdefault(
                game_id, {"gameid": game_id, "name": game_name, "minutes": 0}
            )
            game["minutes"] += minutes
            session_days.add((date_key, sid))

    play_records = getattr(plugin, "play_records", {}) or {}
    for date_key, day_records in list(play_records.items()):
        if not start_key <= date_key <= end_key:
            continue
        for raw_sid, games in list(day_records.items()):
            sid = str(raw_sid)
            if sid not in allowed_sids or (date_key, sid) in session_days:
                continue
            player_day = contributions.setdefault(date_key, {}).setdefault(
                sid, {"minutes": 0, "games": {}}
            )
            for raw_game_id, game_info in list(games.items()):
                if not isinstance(game_info, dict):
                    continue
                minutes = max(0, int(game_info.get("minutes", 0) or 0))
                if minutes <= 0:
                    continue
                game_id = str(raw_game_id)
                game_name = game_info.get("name", game_id)
                player_day["minutes"] += minutes
                game = player_day["games"].setdefault(
                    game_id, {"gameid": game_id, "name": game_name, "minutes": 0}
                )
                game["minutes"] += minutes
    return contributions


def build_heatmap_data(plugin, period, now, group_id=None):
    end_date = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if end_date <= now:
        end_date += timedelta(days=1)
    start_date = end_date - timedelta(days=period)
    start_key = start_date.strftime("%Y-%m-%d")
    end_key = end_date.strftime("%Y-%m-%d")

    groups = getattr(plugin, "group_steam_ids", {}) or {}
    allowed_sids = _heatmap_group_sids(plugin, group_id)
    contributions = _build_heatmap_contributions(
        plugin, start_key, end_key, allowed_sids
    )
    display_names = _build_display_names(plugin)
    player_minutes = {}
    heatmap_daily = {}
    daily_contributors = {}

    day = start_date
    while day <= end_date:
        date_key = day.strftime("%Y-%m-%d")
        day_players = []
        for sid, contribution in contributions.get(date_key, {}).items():
            minutes = contribution["minutes"]
            if minutes <= 0:
                continue
            player_minutes[sid] = player_minutes.get(sid, 0) + minutes
            day_players.append({
                "sid": sid,
                "name": display_names.get(sid, sid),
                "total_minutes": minutes,
                "games": sorted(
                    contribution["games"].values(),
                    key=lambda item: -item["minutes"],
                ),
            })
        day_players.sort(key=lambda item: -item["total_minutes"])
        total_minutes = sum(player["total_minutes"] for player in day_players)
        heatmap_daily[date_key] = total_minutes
        daily_contributors[date_key] = day_players
        day += timedelta(days=1)

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
    group_options = [
        {"id": str(group), "player_count": len({str(sid) for sid in steam_ids})}
        for group, steam_ids in groups.items()
    ]
    return {
        "heatmap_data": heatmap_daily,
        "daily_contributors": daily_contributors,
        "players": players,
        "groups": group_options,
        "selected_group": group_id or "",
    }
