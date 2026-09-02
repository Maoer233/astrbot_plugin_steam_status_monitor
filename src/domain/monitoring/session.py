from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Tuple


_EMPTY_GAME_IDS = (None, "", "0")
NETWORK_FLUCTUATION_WINDOW = 180


def _normalize_gameid(gameid: Any) -> Optional[str]:
    if gameid in _EMPTY_GAME_IDS:
        return None
    return str(gameid)


@dataclass(frozen=True)
class PlayingSession:
    """同一 (group_id, sid) 同一时刻最多一局 playing。"""

    sid: str
    gameid: str
    started_at: int
    state: str
    group_id: str = ""
    exit_deadline: Optional[int] = None
    exited_at: Optional[int] = None
    closed_at: Optional[int] = None

    @property
    def session_id(self) -> str:
        return f"{self.started_at}_{self.gameid}"

    @property
    def duration_min(self) -> float:
        end = self.exited_at or self.closed_at
        if end is None:
            return 0.0
        return max(0.0, (end - self.started_at) / 60)


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    session: PlayingSession


def _open(sid: str, gameid: str, now: int, group_id: str) -> PlayingSession:
    return PlayingSession(
        sid=sid,
        gameid=gameid,
        started_at=now,
        state="playing",
        group_id=group_id,
    )


def _close(session: PlayingSession, now: int) -> PlayingSession:
    exited_at = session.exited_at or now
    return replace(
        session,
        state="closed",
        closed_at=now,
        exited_at=exited_at,
        exit_deadline=None,
    )


def apply(
    session: Optional[PlayingSession],
    snapshot: Mapping[str, Any],
    now: int,
    *,
    sid: str = "",
    group_id: str = "",
) -> Tuple[Optional[PlayingSession], Tuple[SessionEvent, ...]]:
    """纯函数状态机：idle / playing / confirming_exit / closed。

    只有这里会把一局标成 closed。switch 立即 close，不进 confirming_exit。
    3 分钟缓冲只用于 exit 后回到同一 gameid。
    """
    sid = sid or (session.sid if session else str(snapshot.get("steamid") or ""))
    group_id = group_id or (session.group_id if session else str(snapshot.get("group_id") or ""))
    current = _normalize_gameid(snapshot.get("gameid"))

    if session is None or session.state == "closed":
        if current is None:
            return session, ()
        opened = _open(sid, current, now, group_id)
        return opened, (SessionEvent("started", opened),)

    if session.state == "playing":
        if current == session.gameid:
            return session, ()
        if current is None:
            confirming = replace(
                session,
                state="confirming_exit",
                exited_at=now,
                exit_deadline=now + NETWORK_FLUCTUATION_WINDOW,
            )
            return confirming, ()
        closed = _close(replace(session, exited_at=now), now)
        opened = _open(sid, current, now, group_id)
        return opened, (SessionEvent("closed", closed), SessionEvent("started", opened))

    if session.state == "confirming_exit":
        deadline = session.exit_deadline if session.exit_deadline is not None else now
        if current == session.gameid:
            if now <= deadline:
                resumed = replace(session, state="playing", exited_at=None, exit_deadline=None)
                return resumed, (SessionEvent("fluctuation", resumed),)
            closed = _close(session, now)
            opened = _open(sid, current, now, group_id)
            return opened, (SessionEvent("closed", closed), SessionEvent("started", opened))
        if current is None:
            if now >= deadline:
                closed = _close(session, now)
                return closed, (SessionEvent("closed", closed),)
            return session, ()
        closed = _close(session, now)
        opened = _open(sid, current, now, group_id)
        return opened, (SessionEvent("closed", closed), SessionEvent("started", opened))

    return session, ()
