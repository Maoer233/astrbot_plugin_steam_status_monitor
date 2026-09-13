def group_id_of(event, default: str = "default") -> str:
    if hasattr(event, "get_group_id"):
        return str(event.get_group_id() or default)
    return default


from . import monitor, ops, rank, store
