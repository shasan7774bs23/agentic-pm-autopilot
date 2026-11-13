"""Management layer: history, forecasts, effort, risk register, executive report."""
import datetime as dt
import os
import tempfile
import unittest
from types import SimpleNamespace

from pmauto import effort, exec_report, forecast, history, overview, risks
from pmauto.model import Event
from pmauto.store import Store
from tests.test_core import CFG, issue, t


def store():
    d = tempfile.mkdtemp()
    return Store(os.path.join(d, "pm.sqlite"))


def vel_rows(s, project, done, committed):
    for n, (d, c) in enumerate(zip(done, committed)):
        s.snapshot(f"2026-0{1 + n}-15", f"velocity:{project}",
                   {"sprint_id": n, "name": f"S{n}", "sp_completed": d, "sp_committed": c, "bd_length": 10,
                    "issues_completed": 5})


def sprint(issues, start=t(2026, 11, 6), end=t(2026, 11, 19), project="APP", sid=1):
    return {"id": sid, "name": "ACME Sprint 2", "project": project, "startDate": start.isoformat(),
            "endDate": end.isoformat(), "issues": issues}


class TestVelocityAndSprintForecast(unittest.TestCase):
    def test_median_rate_ignores_one_big_closeout(self):
        s = store()
        vel_rows(s, "APP", [5, 6, 90, 5, 6, 5], [10, 10, 100, 10, 10, 10])
        v = forecast.velocity(s, "APP")
        self.assertAlmostEqual(v["sp_per_bd"], 0.55)       # median 5.5 SP / 10 days, not pulled up by the 90
        self.assertEqual(v["sprints"], 6)

    def test_status_is_judged_against_the_teams_own_norm(self):
        v = {"sp_per_bd": 0.5, "median_completed": 5, "sprints": 6, "completion_ratio": 0.25}
        m = {"name": "S", "project": "APP", "end": "2026-11-19", "sp_total": 40, "sp_true_done": 8,
             "sp_ready_or_done": 20, "bd_used": 5, "bd_total": 10}
        f = forecast.sprint_forecast(m, v)
        self.assertEqual(f["expected_done"], 10.5)          # 8 + 0.5 * 5
        self.assertEqual(f["status"], "Usual spillover")    # 26% vs the usual 25%
        self.assertEqual(f["ready_pct"], 50)
        m["sp_true_done"] = 2
        self.assertEqual(forecast.sprint_forecast(m, v)["status"], "Off track")   # 11% is well below the usual 25%
        m["sp_true_done"] = 36
        self.assertEqual(forecast.sprint_forecast(m, v)["status"], "On track")

    def test_no_history_falls_back_to_current_pace(self):
        m = {"name": "S", "project": "APP", "end": "x", "sp_total": 10, "sp_true_done": 5, "bd_used": 5, "bd_total": 10}
        f = forecast.sprint_forecast(m, None)
        self.assertEqual(f["expected_done"], 10)
        self.assertIn("own pace", f["basis"])


class TestReleaseForecast(unittest.TestCase):
    today = dt.date(2026, 11, 12)    # a Thursday

    def vm(self, **kw):
        base = {"project": "WEB", "name": "Web Release v1", "release_date": "2026-11-13", "issues": 10,
                "done": 6, "in_qa": 2, "not_started": 1, "sp_total": 10, "sp_open": 4, "keys": []}
        base.update(kw)
        return base

    def test_all_ready_means_only_deploy_left(self):
        r = forecast.release_forecast(self.vm(done=10, sp_open=0), None, self.today, CFG)
        self.assertEqual(r["status"], "Ready (deploy pending)")

    def test_flow_rate_is_preferred_and_slip_is_counted_in_working_days(self):
        flow = {"sp_per_bd": 1.0, "issues_per_bd": 1, "label": "flow"}
        r = forecast.release_forecast(self.vm(), None, self.today, CFG, flow)
        self.assertEqual(r["forecast"], dt.date(2026, 11, 18))   # 4 working days after Thu = next Wed
        self.assertEqual(r["status"], "At risk")                 # Mon-Wed = 3 working days past Fri 13th (Late from 4)
        self.assertEqual(r["slip_bd"], 3)
        self.assertEqual(r["confirmed"], dt.date(2026, 11, 13))

    def test_overdue(self):
        r = forecast.release_forecast(self.vm(release_date="2026-11-01"), None, self.today, CFG,
                                      {"sp_per_bd": 10, "issues_per_bd": 1, "label": "flow"})
        self.assertEqual(r["status"], "Overdue")

    def test_forecast_change_is_explained(self):
        prev = {"expected_pct": 80, "sp_done": 10, "rate": 1}
        cur = {"expected_pct": 60, "sp_done": 10, "rate": 1}
        why = forecast.explain_change(prev, cur, {"sp_total": 20, "keys": ["A"]}, {"sp_total": 30, "keys": ["A", "B"]})
        self.assertIn("scope 20", why)
        self.assertIn("added B", why)
        self.assertIsNone(forecast.explain_change({"expected_pct": 61}, {"expected_pct": 60, "sp_done": 1, "rate": 1}))


class TestEffort(unittest.TestCase):
    def test_plan_comes_from_estimate_comment_then_points(self):
        i = issue(estimate={"dev": 0.5, "qa": 0.25}, story_points=3)
        self.assertEqual(effort.planned_hours(i, CFG), (6.0, "estimate comment"))
        self.assertEqual(effort.planned_hours(issue(story_points=2), CFG), (16, "story points"))

    def test_flags(self):
        sig = effort.assess(issue(estimate={"dev": 0.25}, time_spent_h=8, status="Awaiting Sign-off"), t(2026, 11, 12), CFG)
        self.assertEqual(sig["flag"], "Significant overrun")
        near = effort.assess(issue(estimate={"dev": 1}, time_spent_h=7, status="In Progress"), t(2026, 11, 12), CFG)
        self.assertEqual(near["flag"], "Likely to exceed")
        fine = effort.assess(issue(estimate={"dev": 1}, time_spent_h=3, status="In Progress"), t(2026, 11, 12), CFG)
        self.assertIsNone(fine["flag"])

    def test_remaining_work_depends_on_stage(self):
        e = {"dev": 1, "review": 0.25, "qa": 0.5}
        self.assertEqual(effort.remaining_hours(issue(estimate=e, status="QA"), CFG), 4)            # QA part only
        self.assertEqual(effort.remaining_hours(issue(estimate=e, status="Code Review"), CFG), 6)   # review + QA
        self.assertEqual(effort.remaining_hours(issue(estimate=e, status="In Progress", time_spent_h=5), CFG), 9)

    def test_patterns_need_repeats(self):
        a = [{"issue": issue(key=f"K{n}", assignee="X"), "planned": 2, "flag": "Over estimate"} for n in range(3)]
        a.append({"issue": issue(key="K9", assignee="X"), "planned": 2, "flag": None})
        self.assertEqual(effort.assignee_patterns(a)[0]["over"], 3)
        self.assertEqual(effort.assignee_patterns(a[:2]), [])


class TestHistoryMetrics(unittest.TestCase):
    def test_sprint_metrics_count_flow_into_ready(self):
        moved = issue(key="A", status="Ready to Deploy", status_category="indeterminate", story_points=2,
                      history=[Event("x", "Dev", t(2026, 11, 10), "status", "QA", "Ready to Deploy")])
        old = issue(key="B", status="Done", status_category="done", story_points=3,
                    history=[Event("x", "Dev", t(2026, 11, 1), "status", "QA", "Done")])
        m = history.sprint_metrics(sprint([moved, old, issue(key="C", story_points=None)]), t(2026, 11, 12), CFG)
        self.assertEqual(m["sp_total"], 5)
        self.assertEqual(m["sp_true_done"], 3)
        self.assertEqual(m["sp_ready_or_done"], 5)
        self.assertEqual(m["sp_reached_ready"], 2)     # B finished before the sprint started
        self.assertEqual(m["no_points"], 1)

    def test_parking_sprints_are_not_velocity(self):
        self.assertFalse(history.is_delivery_sprint({"name": "Waiting for client", "startDate": "x", "completeDate": "y"}, CFG))
        self.assertTrue(history.is_delivery_sprint({"name": "Web Sprint 1", "startDate": "x", "completeDate": "y"}, CFG))
        self.assertFalse(history.is_delivery_sprint({"name": "Web Sprint 2"}, CFG))   # not dated
        only = dict(CFG, delivery_sprint_pattern=r"(?i)sprint")
        self.assertFalse(history.is_delivery_sprint({"name": "Hotfix pool", "startDate": "x", "completeDate": "y"}, only))


def intel_fixture(s):
    fc = {"velocity": {"APP": {"completion_ratio": 0.3, "sprints": 6, "completed": [3] * 6, "committed": [10] * 6,
                                   "median_completed": 3, "avg_completed": 3, "trend": None, "names": ["S"] * 6}},
          "sprints": [{"sprint": "ACME Sprint 2", "project": "APP", "end": "2026-11-19", "sp_total": 40,
                       "sp_done": 2, "expected_done": 4, "expected_pct": 10, "spill_sp": 36, "status": "Off track",
                       "bd_left": 5, "basis": "b", "norm_pct": 30, "ready_pct": 20, "rate": 0.4}],
          "releases": [{"version": "R1", "project": "APP", "confirmed": dt.date(2026, 11, 13),
                        "forecast": dt.date(2026, 11, 20), "slip_bd": 5, "overdue": False, "open_issues": 3,
                        "issues": 5, "in_qa": 1, "not_started": 1, "status": "Late", "basis": "b"}]}
    eff = {"flagged": [], "patterns": [], "idle": [], "load": {}, "coverage": {}, "past_checked": 0}
    return fc, eff


class TestRiskRegister(unittest.TestCase):
    def test_identify_and_remember(self):
        s = store()
        fc, eff = intel_fixture(s)
        found = risks.identify(CFG, fc, eff, [], [], dt.date(2026, 11, 12))
        titles = [r["title"] for r in found]
        self.assertTrue(any("Sprint 2" in x and "usually" in x for x in titles))
        self.assertTrue(any(x.startswith("R1 is forecast") for x in titles))
        self.assertTrue(any("finish only 30%" in x for x in titles))
        self.assertEqual(found[0]["severity"], "High")
        reg = risks.Register(s)
        _, new, closed = reg.update(found, dt.date(2026, 11, 12))
        self.assertEqual(len(new), len(found))
        _, new, closed = reg.update(found[:1], dt.date(2026, 11, 13))
        self.assertEqual(new, [])
        self.assertEqual(len(closed), len(found) - 1)
        self.assertEqual(found[0]["first_seen"], "2026-11-12")   # persists across days


class TestReports(unittest.TestCase):
    def test_exec_report_and_dashboard_render(self):
        s = store()
        fc, eff = intel_fixture(s)
        found = risks.identify(CFG, fc, eff, [], [], dt.date(2026, 11, 12))
        intel = {"forecast": fc, "effort": eff, "risks": found, "new_risks": [r["id"] for r in found],
                 "closed_risks": [], "errors": [], "recent_done": []}
        snap = SimpleNamespace(now=t(2026, 11, 12, 10), sprints=[], open_issues=[], new_client_issues=[])
        doc = exec_report.build(snap, [], intel, CFG, {"versions": []}, drafts=[("APP-1", "APP-1-comment.md")])
        titles = [x.title for x in doc.sections]
        self.assertEqual(titles[:8], ["1. Executive summary", "2. What happened", "3. Items requiring my attention",
                                      "4. Delivery and sprint health", "5. Risks and blockers",
                                      "6. Resource and time concerns", "7. Upcoming releases", "8. Recommended actions"])
        self.assertTrue(doc.sections[0].note.startswith("[URGENT]"))
        self.assertTrue(any("post it" in b for b in doc.sections[7].bullets))
        html = "".join(overview.intel_sections(intel, CFG))
        self.assertIn("Risk register", html)
        self.assertIn("CONFIRMED 13 Nov 2026", html)


if __name__ == "__main__":
    unittest.main()
