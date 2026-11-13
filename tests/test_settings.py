"""Per-machine config: team config + personal settings, written by the setup interview."""
import json
import os
import tempfile
import unittest
from unittest import mock

from pmauto import settings, setup


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"PMAUTO_HOME": self.dir}, clear=False)
        self.env.start()
        os.environ.pop("PMAUTO_CONFIG", None)

    def tearDown(self):
        self.env.stop()

    def test_not_set_up_yet(self):
        self.assertIsNone(settings.load_raw())

    def test_interview_answers_write_team_and_machine_config(self):
        answers = {"owner_name": "Pat", "team_name": "Acme", "projects": ["APP"], "boards": {"APP": 1},
                   "fields": {"story_points": "customfield_1", "sprint": "customfield_2"},
                   "status_groups": {"true_done": ["done"], "ready_to_deploy": [], "qa": ["qa"],
                                     "not_started": ["to do"], "parked": []},
                   "timezone_offset_hours": 1, "workdays": [0, 1, 2, 3, 4], "client_accounts": {"c1": "Client"},
                   "team_config": os.path.join(self.dir, "team", "acme.json")}
        path = os.path.join(self.dir, "answers.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(answers, fh)
        setup.write_config(path)
        with open(answers["team_config"], encoding="utf-8") as fh:
            team = json.load(fh)
        self.assertNotIn("owner_name", team)            # personal settings stay on the machine
        self.assertEqual(team["projects"], ["APP"])
        cfg = settings.load_raw()
        self.assertEqual(cfg["owner_name"], "Pat")
        self.assertEqual(cfg["projects"], ["APP"])      # layered from the team config
        self.assertEqual(cfg["focus_hours_per_day"], 6)  # default filled in
        self.assertEqual(cfg["estimate_unit_hours"], 8)
        # a second run keeps the team config unless told to overwrite it, and backs up the machine config
        lines = setup.write_config(path)
        self.assertTrue(any("kept existing team config" in x for x in lines))
        self.assertTrue(os.path.exists(settings.config_path() + ".bak"))

    def test_missing_answers_are_refused(self):
        path = os.path.join(self.dir, "a.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"projects": ["APP"]}, fh)
        with self.assertRaises(SystemExit):
            setup.write_config(path)

    def test_credentials_template_is_created_once(self):
        with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", self.dir)):
            made = setup.ensure_home()
            self.assertEqual(made, [settings.credentials_path()])
            self.assertEqual(setup.ensure_home(), [])


class TestConfigProblems(unittest.TestCase):
    def test_bad_regex_and_missing_groups_are_reported(self):
        from pmauto import settings
        cfg = {"client_reference": {"pattern": "REQ-(\d+"}, "version_pattern": "v\d+",
               "code_repos": {"APP": {"release_branch_pattern": "release/.*"}},
               "estimate_comment_labels": {"DEV": "build"}, "status_groups": {"in_progress": ["in progress"]}}
        errors, warnings = settings.problems(cfg)
        self.assertEqual(len(errors), 4)                       # bad regex, two missing groups, unknown stage
        self.assertTrue(any("status_groups.review" in w for w in warnings))

    def test_a_clean_config_has_no_problems(self):
        from pmauto import settings
        cfg = settings.load_raw(os.path.join(os.path.dirname(__file__), "fixture_config.json"))
        self.assertEqual(settings.problems(cfg), ([], []))


class TestEstimateShape(unittest.TestCase):
    def test_prose_is_not_an_estimate(self):
        from pmauto.model import parse_estimate
        labels = {"DEV": "dev", "QA": "qa"}
        self.assertIsNone(parse_estimate("QA: 2 bugs found on the export page", labels))
        self.assertEqual(parse_estimate("DEV - 1 day / QA - 0.5", labels), {"dev": 1.0, "qa": 0.5})


if __name__ == "__main__":
    unittest.main()
