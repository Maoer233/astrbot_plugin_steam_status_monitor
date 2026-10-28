"""普通状态通知的有界内存队列。"""

import time

from ...shared.logging import logger


TTL_SECONDS = 1800
PER_SESSION_LIMIT = 100
GLOBAL_LIMIT = 1000


def enqueue_notifications(plugin, group_id, notifications):
    now = time.time()
    queue = plugin._pending_end_notifications
    queue.setdefault(str(group_id), []).extend(
        dict(n, _enqueued_at=n.get("_enqueued_at", now)) for n in notifications
    )
    trim_notifications(plugin, now)


def trim_notifications(plugin, now=None):
    now = time.time() if now is None else now
    queue = plugin._pending_end_notifications
    resolver = getattr(plugin, "_get_notify_sessions", None)
    records = []
    expired = 0
    for group_id, items in queue.items():
        for n in items:
            n.setdefault("_enqueued_at", now)
            if now - n["_enqueued_at"] >= TTL_SECONDS:
                expired += 1
                continue
            targets = resolver(group_id, n["sid"]) if resolver else [str(group_id)]
            if "_target_session" in n:
                targets = [s for s in targets if s == n["_target_session"]]
            for target in targets:
                records.append((group_id, dict(n, _target_session=target)))
    records = [entry for _, entry in sorted(
        enumerate(records), key=lambda item: (item[1][1]["_enqueued_at"], item[0]), reverse=True
    )]
    counts = {}
    active_count = 0
    for items in getattr(plugin, "_active_notification_flush", {}).values():
        for n in items:
            target = n.get("_target_session")
            counts[target] = counts.get(target, 0) + 1
            active_count += 1
    kept = []
    seen = set()
    key_fn = getattr(plugin, "_notification_event_key", None)
    for group_id, n in records:
        target = n["_target_session"]
        key = key_fn(n, target) if key_fn else None
        if key is not None and key in seen:
            continue
        if counts.get(target, 0) >= PER_SESSION_LIMIT or len(kept) + active_count >= GLOBAL_LIMIT:
            continue
        if key is not None:
            seen.add(key)
        counts[target] = counts.get(target, 0) + 1
        kept.append((group_id, n))
    queue.clear()
    for group_id, n in reversed(kept):
        queue.setdefault(group_id, []).append(n)
    dropped = len(records) - len(kept)
    if expired or dropped:
        logger.info("通知队列清理: expired=%s, dropped=%s, pending=%s, active=%s",
                    expired, dropped, len(kept), active_count)
    return expired + dropped
