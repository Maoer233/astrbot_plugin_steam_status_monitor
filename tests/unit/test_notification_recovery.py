"""发送端持续故障后的批次结算与恢复。"""

import asyncio
import time
import unittest
from types import SimpleNamespace

from unittest.mock import patch

from src.application.services.notification_queue import enqueue_notifications, trim_notifications
from tests.unit.test_presence_notify import _build_send_plugin, _load_notification_methods


def notifications(count):
    return [dict(type="start", name="player", game="game", sid="sid", gameid="1", start_time=i)
            for i in range(count)]


def plugin():
    result = _build_send_plugin()
    methods = _load_notification_methods(("_is_intentionally_unrendered", "_flush_pending_end_notifications"))
    for name, method in methods.items():
        if name.startswith("_") and callable(method):
            setattr(type(result), name, staticmethod(method) if name == "_is_intentionally_unrendered" else method)
    return result


class NotificationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_backlog_is_batched(self):
        p = plugin()
        self.assertEqual([], await p._send_merged_notification("g1", notifications(243)))
        self.assertEqual(49, len(p.sent))
        self.assertTrue(all(len(chain.chain) <= 5 for _, chain in p.sent))

    async def test_only_failed_and_unsent_batches_retry(self):
        p = plugin()
        calls = []

        async def send(session, chain):
            calls.append(chain)
            if len(calls) == 2:
                raise RuntimeError("offline")

        p.context.send_message = send
        items = notifications(12)
        failed = await p._send_merged_notification("g1", items)
        self.assertEqual(2, len(calls))
        self.assertEqual(list(range(5, 12)), [n["start_time"] for n in failed])
        self.assertFalse(p._inflight_notification_events)

    async def test_partial_render_failure_is_not_acknowledged(self):
        p = plugin()
        p.config = {"notify_send_image": True, "notify_send_text": False}
        rendered = []

        async def render(n):
            rendered.append(n["start_time"])
            return None if n["start_time"] == 1 else "image.png"

        p._render_notification_image = render
        failed = await p._send_merged_notification("g1", notifications(12))
        self.assertEqual([1, *range(5, 12)], [n["start_time"] for n in failed])
        self.assertEqual(list(range(5)), rendered)
        self.assertEqual(4, len(p.sent[0][1].chain))

    async def test_target_results_are_independent(self):
        p = plugin()
        p.push_groups = {"sid": ["g2"]}
        p.notify_sessions["g2"] = "test:GroupMessage:456"

        async def send(session, chain):
            if session.endswith("456"):
                raise RuntimeError("offline")

        p.context.send_message = send
        failed = await p._send_merged_notification("g1", notifications(7))
        self.assertEqual(7, len(failed))
        self.assertTrue(all(n["_target_session"].endswith("456") for n in failed))

    async def test_cancel_restores_all_groups_and_releases_inflight(self):
        p = plugin()
        p.notify_sessions["g2"] = "test:GroupMessage:456"
        entered = asyncio.Event()

        async def send(session, chain):
            entered.set()
            await asyncio.Event().wait()

        p.context.send_message = send
        p._pending_end_notifications = {"g1": notifications(7), "g2": notifications(2)}
        task = asyncio.create_task(p._flush_pending_end_notifications())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(7, len(p._pending_end_notifications["g1"]))
        self.assertEqual(2, len(p._pending_end_notifications["g2"]))
        self.assertFalse(p._inflight_notification_events)

    async def test_cooldown_blocks_new_events_and_rendering(self):
        p = plugin()
        p.config = {"notify_send_image": True, "notify_send_text": False}
        rendered = []
        calls = []

        async def render(n):
            rendered.append(n)
            return "image.png"

        async def send(session, chain):
            calls.append(session)
            raise RuntimeError("offline")

        p._render_notification_image = render
        p.context.send_message = send
        clock = [100]
        fake_time = SimpleNamespace(time=time.time, monotonic=lambda: clock[0])
        with patch.dict(type(p)._send_merged_notification.__globals__, time=fake_time):
            failed = await p._send_merged_notification("g1", notifications(1))
            clock[0] = 129
            deferred = await p._send_merged_notification("g1", failed + notifications(2)[1:])
            self.assertEqual(2, len(deferred))
            self.assertEqual(1, len(calls))
            self.assertEqual(1, len(rendered))
            clock[0] = 130
            await p._send_merged_notification("g1", deferred)
        self.assertEqual((2, 190), p._notification_cooldowns["test:GroupMessage:123"])

    async def test_ttl_survives_requeue(self):
        p = plugin()
        p._pending_end_notifications = {}
        with patch("time.time", return_value=100):
            enqueue_notifications(p, "g1", notifications(1))
        item = p._pending_end_notifications.pop("g1")
        with patch("time.time", return_value=1899):
            enqueue_notifications(p, "g1", item)
        self.assertEqual(100, p._pending_end_notifications["g1"][0]["_enqueued_at"])
        trim_notifications(p, now=1900)
        self.assertEqual({}, p._pending_end_notifications)

    async def test_capacity_keeps_newest_and_includes_active(self):
        p = plugin()
        p._pending_end_notifications = {}
        with patch("time.time", return_value=100):
            for n in notifications(120):
                enqueue_notifications(p, "g1", [n])
        kept = p._pending_end_notifications["g1"]
        self.assertEqual(100, len(kept))
        self.assertEqual(list(range(20, 120)), [n["start_time"] for n in kept])
        p._active_notification_flush = p._pending_end_notifications
        p._pending_end_notifications = {}
        with patch("time.time", return_value=101):
            enqueue_notifications(p, "g1", notifications(1))
        self.assertEqual({}, p._pending_end_notifications)

    async def test_global_limit(self):
        p = plugin()
        p._pending_end_notifications = {}
        for i in range(11):
            group = str(i)
            p.notify_sessions[group] = f"test:GroupMessage:{1000 + i}"
            enqueue_notifications(p, group, notifications(100))
        self.assertEqual(1000, sum(map(len, p._pending_end_notifications.values())))

    async def test_cancel_after_success_does_not_restore_successful_batch(self):
        p = plugin()
        p._pending_end_notifications = {}
        enqueue_notifications(p, "g1", notifications(12))
        entered = asyncio.Event()
        calls = []

        async def send(session, chain):
            calls.append(chain)
            if len(calls) == 2:
                entered.set()
                await asyncio.Event().wait()

        p.context.send_message = send
        task = asyncio.create_task(p._flush_pending_end_notifications())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(list(range(5, 12)), [n["start_time"] for n in p._pending_end_notifications["g1"]])

    async def test_quiet_hours_consume_pending_without_sending(self):
        p = plugin()
        p.config["notify_quiet_hours"] = "0,0"
        p._pending_end_notifications = {}
        enqueue_notifications(p, "g1", notifications(3))
        namespace = type(p)._send_merged_notification.__globals__
        with patch.dict(namespace, in_quiet_hours=lambda config: True):
            await p._flush_pending_end_notifications()
        self.assertEqual({}, p._pending_end_notifications)
        self.assertEqual([], p.sent)

    async def test_owned_retries_remain_under_owned_service_control(self):
        p = plugin()
        target = "test:GroupMessage:123"
        p._notification_cooldowns = {target: (1, float("inf"))}
        item = dict(type="owned", sid="sid", gameid="1", name="p", game="g", added_at=1)
        self.assertEqual([], await p._send_merged_notification("g1", [item]))
        self.assertEqual(1, len(p.sent))
        self.assertIn(target, p._notification_cooldowns)

    async def test_success_resets_cooldown(self):
        p = plugin()
        target = "test:GroupMessage:123"
        p._notification_cooldowns = {target: (3, 0)}
        await p._send_merged_notification("g1", notifications(1))
        self.assertNotIn(target, p._notification_cooldowns)
