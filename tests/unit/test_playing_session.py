import unittest

from src.domain.monitoring.session import apply


def _snap(gameid, sid="sid"):
    return {"steamid": sid, "gameid": gameid}


class PlayingSessionApplyTests(unittest.TestCase):
    def test_start_opens_playing(self):
        session, events = apply(None, _snap("730"), 1000, sid="sid", group_id="g1")
        self.assertEqual(session.state, "playing")
        self.assertEqual(session.gameid, "730")
        self.assertEqual(session.started_at, 1000)
        self.assertEqual([event.kind for event in events], ["started"])

    def test_exit_enters_confirming_then_resume(self):
        playing, _ = apply(None, _snap("730"), 1000, sid="sid", group_id="g1")
        confirming, events = apply(playing, _snap(None), 1600, sid="sid", group_id="g1")
        self.assertEqual(confirming.state, "confirming_exit")
        self.assertEqual(confirming.exit_deadline, 1780)
        self.assertEqual(events, ())

        resumed, events = apply(confirming, _snap("730"), 1630, sid="sid", group_id="g1")
        self.assertEqual(resumed.state, "playing")
        self.assertIsNone(resumed.exit_deadline)
        self.assertEqual([event.kind for event in events], ["fluctuation"])

    def test_exit_timeout_closes_once(self):
        playing, _ = apply(None, _snap("730"), 1000, sid="sid", group_id="g1")
        confirming, _ = apply(playing, _snap(None), 1600, sid="sid", group_id="g1")
        closed, events = apply(confirming, _snap(None), 1780, sid="sid", group_id="g1")
        self.assertEqual(closed.state, "closed")
        self.assertEqual([event.kind for event in events], ["closed"])
        self.assertEqual(closed.session_id, "1000_730")

        still, events = apply(closed, _snap(None), 1800, sid="sid", group_id="g1")
        self.assertEqual(still.state, "closed")
        self.assertEqual(events, ())

    def test_switch_closes_previous_immediately(self):
        playing, _ = apply(None, _snap("730"), 1000, sid="sid", group_id="g1")
        current, events = apply(playing, _snap("570"), 1600, sid="sid", group_id="g1")
        self.assertEqual(current.state, "playing")
        self.assertEqual(current.gameid, "570")
        self.assertEqual([event.kind for event in events], ["closed", "started"])
        self.assertEqual(events[0].session.gameid, "730")
        self.assertEqual(events[0].session.state, "closed")

    def test_confirming_exit_then_other_game_closes_immediately(self):
        playing, _ = apply(None, _snap("730"), 1000, sid="sid", group_id="g1")
        confirming, _ = apply(playing, _snap(None), 1600, sid="sid", group_id="g1")
        current, events = apply(confirming, _snap("570"), 1630, sid="sid", group_id="g1")
        self.assertEqual(current.gameid, "570")
        self.assertEqual(current.state, "playing")
        self.assertEqual([event.kind for event in events], ["closed", "started"])
        self.assertEqual(events[0].session.gameid, "730")

    def test_switch_back_to_a_is_a_new_session(self):
        playing_a, _ = apply(None, _snap("730"), 1000, sid="sid", group_id="g1")
        playing_b, _ = apply(playing_a, _snap("570"), 1600, sid="sid", group_id="g1")
        playing_a2, events = apply(playing_b, _snap("730"), 1700, sid="sid", group_id="g1")
        self.assertEqual(playing_a2.gameid, "730")
        self.assertEqual(playing_a2.started_at, 1700)
        self.assertEqual([event.kind for event in events], ["closed", "started"])
        self.assertNotEqual(playing_a2.session_id, playing_a.session_id)

    def test_groups_hold_independent_sessions_with_same_session_id(self):
        a, _ = apply(None, _snap("730"), 1000, sid="sid", group_id="g1")
        b, _ = apply(None, _snap("730"), 1000, sid="sid", group_id="g2")
        _, events_a = apply(a, _snap("570"), 1600, sid="sid", group_id="g1")
        _, events_b = apply(b, _snap("570"), 1600, sid="sid", group_id="g2")
        self.assertEqual(events_a[0].session.group_id, "g1")
        self.assertEqual(events_b[0].session.group_id, "g2")
        self.assertEqual(events_a[0].session.session_id, events_b[0].session.session_id)
        self.assertEqual(events_a[0].session.session_id, "1000_730")


if __name__ == "__main__":
    unittest.main()
