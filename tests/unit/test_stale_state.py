import os
import time
from src.infrastructure.persistence.plugin_data import PersistenceMixin


class PersistenceHost(PersistenceMixin):
    pass


def test_is_group_state_stale_when_mtime_is_old(tmp_path):
    host = PersistenceHost()
    host.data_dir = str(tmp_path)
    host._startup_time = time.time()
    path = host._get_group_data_path("111", "states")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    old = host._startup_time - 7200
    os.utime(path, (old, old))
    assert host._is_group_state_stale("111", threshold=3600) is True


def test_is_group_state_fresh_when_file_missing_or_recent(tmp_path):
    host = PersistenceHost()
    host.data_dir = str(tmp_path)
    host._startup_time = time.time()
    assert host._is_group_state_stale("111") is False

    path = host._get_group_data_path("111", "states")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    os.utime(path, (host._startup_time + 1, host._startup_time + 1))
    assert host._is_group_state_stale("111", threshold=3600) is False
