"""发送端持续故障后的批次结算与恢复。"""

import asyncio
import unittest

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
