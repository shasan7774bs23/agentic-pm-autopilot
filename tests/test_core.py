import datetime as dt
import json
import os
import unittest

from pmauto import rules, settings
from pmauto.model import Comment, Issue, business_days, client_ref, parse_estimate, previous_workday

CFG = settings.load_raw(os.path.join(os.path.dirname(__file__), "fixture_config.json"))
TZ = dt.timezone(dt.timedelta(hours=1))
CLIENT = "client-0001"   # Client One
TEAM = "team-0001"


def t(y, m, d, h=10):
    return dt.datetime(y, m, d, h, 0, tzinfo=TZ)


def issue(**kw):
    base = dict(key="APP-1", url="u", project="APP", summary="Export fails - REQ-1234", type="Task",
                status="To Do", status_category="new", assignee_id=TEAM, assignee="Dev", reporter_id=CLIENT,
                reporter="Client One", created=t(2026, 11, 2), updated=t(2026, 11, 2), story_points=1,
                fix_versions=[], sprints=[], comments=[], status_since=t(2026, 11, 2), estimate=None)
    base.update(kw)
    return Issue(**base)


class TestParsing(unittest.TestCase):
    def test_estimate_comment_in_the_teams_labels(self):
        labels = CFG["estimate_comment_labels"]
        self.assertEqual(parse_estimate("DEV - 0.75  \nREV - 0.25  \nQA - 0.5", labels), {"dev": 0.75, "review": 0.25, "qa": 0.5})
        self.assertEqual(parse_estimate("DEV - 1 / QA - 0.5", labels), {"dev": 1.0, "qa": 0.5})
        self.assertIsNone(parse_estimate("Observation: the DEV site is down", labels))
        self.assertIsNone(parse_estimate("DEV - 1", {}))   # no labels configured: comment estimates are off

    def test_client_reference_from_the_title(self):
        self.assertEqual(client_ref("Export fails on the summary page REQ-1234", CFG), "1234")
        self.assertIsNone(client_ref("FEEDBACK 10/29/2026", CFG))
        self.assertIsNone(client_ref("Export fails REQ-1234", {"client_reference": None}))


class TestBusinessTime(unittest.TestCase):
    def test_weekend_excluded(self):
        # Fri 10:00 -> Mon 10:00 is one business day (Fri 14h + Mon 10h)
        self.assertAlmostEqual(business_days(t(2026, 11, 6), t(2026, 11, 9), CFG), 1.0)

    def test_same_day(self):
        self.assertAlmostEqual(business_days(t(2026, 11, 9, 9), t(2026, 11, 9, 21), CFG), 0.5)

    def test_previous_workday_skips_weekend(self):
        self.assertEqual(previous_workday(dt.date(2026, 11, 9), CFG["workdays"]), dt.date(2026, 11, 6))


class TestRules(unittest.TestCase):
    now = t(2026, 11, 9, 14)

    def test_untriaged_fires_and_clears_with_team_comment(self):
        i = issue(created=t(2026, 11, 5))
        self.assertEqual(len(rules.untriaged_client_tickets([i], self.now, CFG)), 1)
        i.comments = [Comment(TEAM, "Lead", t(2026, 11, 6), "DEV - 1\nQA - 0.5", [])]
        self.assertEqual(rules.untriaged_client_tickets([i], self.now, CFG), [])

    def test_untriaged_ignores_internal_tickets(self):
        self.assertEqual(rules.untriaged_client_tickets([issue(reporter_id=TEAM, created=t(2026, 11, 2))], self.now, CFG), [])

    def test_waiting_on_us(self):
        i = issue(status="In Progress", status_category="indeterminate",
                  comments=[Comment(TEAM, "Dev", t(2026, 11, 3), "Could you confirm?", [CLIENT]),
                            Comment(CLIENT, "Client One", t(2026, 11, 5), "Yes, submit is missing", [])])
        f = rules.waiting_on_us([i], self.now, CFG)
        self.assertEqual(len(f), 1)
        self.assertIn("Client One", f[0].detail)

    def test_waiting_on_client_by_mention(self):
        i = issue(status="In Progress", status_category="indeterminate",
                  comments=[Comment(TEAM, "Dev", t(2026, 11, 5), "@Client please check", [CLIENT])])
        self.assertEqual(rules.waiting_on_client([i], self.now, CFG)[0].rule, "Waiting on client")
        self.assertEqual(rules.waiting_on_us([i], self.now, CFG), [])

    def test_stuck_counts_business_days_and_skips_todo_done(self):
        stuck = issue(status="Code Review", status_category="indeterminate", status_since=t(2026, 11, 3))
        fresh = issue(key="X-2", status="Code Review", status_category="indeterminate", status_since=t(2026, 11, 6))
        todo = issue(key="X-3", status="To Do", status_since=t(2026, 10, 1))
        done = issue(key="X-4", status="Done", status_category="done", status_since=t(2026, 10, 1))
        self.assertEqual([f.issue.key for f in rules.stuck([stuck, fresh, todo, done], self.now, CFG)], ["APP-1"])

    def test_parked_status_is_parked_not_stuck_and_not_done(self):
        parked = issue(status="Awaiting Sign-off", status_category="done", status_since=t(2026, 10, 1))
        self.assertEqual(rules.stuck([parked], self.now, CFG), [])
        self.assertEqual(rules.parked([parked], self.now, CFG)[0].rule, "Parked")
        self.assertEqual(len(rules.waiting_on_us([issue(status="Awaiting Sign-off", comments=[
            Comment(CLIENT, "Client One", t(2026, 11, 2), "still broken", [])])], self.now, CFG)), 1)

    def test_old_items_downgraded_to_info(self):
        old = issue(created=t(2025, 1, 6))
        f = rules.untriaged_client_tickets([old], self.now, CFG)
        self.assertEqual((f[0].rule, f[0].severity), ("Old client ticket never answered", "info"))

    def test_ready_to_deploy_needs_fix_version(self):
        i = issue(status="Ready To Deploy", status_category="indeterminate")
        self.assertEqual(rules.gate_checks([i], self.now, CFG)[0].rule, "Ready to Deploy without Fix Version")
        i.fix_versions = ["Web Release v1.2.0"]
        self.assertEqual(rules.gate_checks([i], self.now, CFG), [])


if __name__ == "__main__":
    unittest.main()
