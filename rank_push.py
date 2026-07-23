"""Small, dependency-free helpers for scheduled ranking delivery."""


def build_rank_push_scopes(group_ids, use_global_rank=False):
    """Return ``(target_group, data_group)`` pairs for a scheduled push.

    ``data_group`` is the target group in the normal per-group mode and
    ``None`` only when the administrator explicitly selected a shared global
    ranking. Group IDs are normalized to strings and duplicates are removed
    while preserving their configured order.
    """

    scopes = []
    seen = set()
    for raw_group_id in group_ids or []:
        if raw_group_id is None:
            continue
        group_id = str(raw_group_id).strip()
        if not group_id or group_id in seen:
            continue
        seen.add(group_id)
        scopes.append((group_id, None if use_global_rank else group_id))
    return scopes
