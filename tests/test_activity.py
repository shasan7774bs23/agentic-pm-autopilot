import datetime as dt
import unittest

from pmauto.activity import actions_between, forward_moves
from pmauto.model import Comment, Event
from tests.test_core import CFG, CLIENT, TEAM, issue, t

TEMPO = "557058:tempo-app"


class TestActivity(unittest.TestCase):
    def test_forward_moves_credit_the_mover_only(self):
        i = issue(history=[
            Event(TEAM, "Dev A", t(2026, 11, 6), "status", "In Progress", "Code Review"),
            Event("qa1", "QA B", t(2026, 11, 7), "status", "QA", "Awaiting Sign-off"),
            Event("qa1", "QA B", t(2026, 11, 7, 11), "status", "Awaiting Sign-off", "QA"),   # backwards: not a completion
            Event(CLIENT, "Client One", t(2026, 11, 7), "status", "Awaiting Sign-off", "Done"),  # client: excluded
        ])
        m = forward_moves([i], t(2026, 11, 6, 0), t(2026, 11, 9), CFG)
        self.assertEqual(set(m), {TEAM, "qa1"})
        self.assertEqual(m[TEAM]["moves"]["APP-1"][0], "Code Review")
        self.assertEqual(m["qa1"]["moves"]["APP-1"][0], "Awaiting Sign-off")

    def test_release_closeout_not_a_task(self):
        i = issue(history=[Event("rm", "Release Mgr", t(2026, 11, 6), "status", "Ready To Deploy", "Done")])
        m = forward_moves([i], t(2026, 11, 6, 0), t(2026, 11, 9), CFG)
        self.assertEqual((m["rm"]["moves"], m["rm"]["closeouts"]), ({}, 1))

    def test_bots_and_out_of_window_ignored(self):
        i = issue(history=[Event(TEAM, "Dev A", t(2026, 10, 1), "status", "In Progress", "Code Review")],
                  comments=[Comment(TEMPO, "Timesheets by Tempo - Jira Time Tracking", t(2026, 11, 6), "x", []),
                            Comment(TEAM, "Dev A", t(2026, 11, 6), "fixed", [])])
        a = actions_between([i], t(2026, 11, 6, 0), t(2026, 11, 7, 0), CFG)
        self.assertEqual(list(a), [TEAM])
        self.assertEqual(a[TEAM]["items"]["APP-1"], ["commented"])


if __name__ == "__main__":
    unittest.main()
