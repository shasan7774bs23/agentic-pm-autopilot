import os
import tempfile
import unittest

from pmauto.store import Store
from pmauto import mirrors


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = Store(os.path.join(self.dir, "pm.sqlite"))

    def test_same_content_is_listed_once(self):
        self.assertTrue(self.s.add_work("ticket", "APP-1", "h1", "new"))
        self.assertFalse(self.s.add_work("ticket", "APP-1", "h1", "new"))
        self.assertEqual(len(self.s.open_work()), 1)

    def test_newer_change_supersedes_open_item(self):
        self.s.add_work("ticket", "APP-1", "h1", "new")
        self.s.add_work("ticket", "APP-1", "h2", "changed")
        open_items = self.s.open_work()
        self.assertEqual([w["fingerprint"] for w in open_items], ["h2"])

    def test_mark_analysed_closes_work_and_keeps_verdict(self):
        self.s.add_work("ticket", "APP-1", "h1", "new")
        self.s.mark_analysed("APP-1", "h1", verdict="READY", analysis_file="a.md")
        self.assertEqual(self.s.open_work(), [])
        self.s.mark_analysed("APP-1", "h1")          # a later mark without a verdict keeps the old one
        self.assertEqual(self.s.ticket("APP-1")["verdict"], "READY")

    def test_audit_is_append_only_log(self):
        self.s.audit("claude", "post_comment", "APP-1", "dry_run", chars=10)
        self.s.audit("pm", "post_comment", "APP-1", "ok", comment_id="5")
        rows = self.s.audit_rows()
        self.assertEqual([r["outcome"] for r in rows], ["dry_run", "ok"])

    def test_snapshot_history_in_day_order(self):
        self.s.snapshot("2026-11-11", "APP", {"done": 3})
        self.s.snapshot("2026-11-10", "APP", {"done": 1})
        self.assertEqual([d for d, _ in self.s.history("APP")], ["2026-11-10", "2026-11-11"])


class MirrorGuardTest(unittest.TestCase):
    def test_refuses_to_fetch_a_working_clone(self):
        cfg = {"code_repos": {"X": {"repo": "C:/work/acme/app-repo",
                                    "branch": "main"}}}
        with self.assertRaises(SystemExit):
            mirrors.refresh(cfg)


if __name__ == "__main__":
    unittest.main()
