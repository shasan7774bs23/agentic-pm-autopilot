import datetime as dt
import unittest

from pmauto.context import content_hash, description_as_of
from tests.test_core import CFG, CLIENT, TEAM


def h(when, old):
    return {"created": when, "items": [{"field": "description", "fromString": old}]}


class TestContext(unittest.TestCase):
    def test_later_edits_are_undone_in_order(self):
        hist = [h("2026-10-17T10:00:00.000-0500", "See Attached"),
                h("2026-10-20T10:00:00.000-0500", "Unlink v2")]
        as_of = dt.datetime(2026, 10, 16, 12, tzinfo=dt.timezone.utc)
        self.assertEqual(description_as_of("Unlink v3", hist, as_of), ("See Attached", 2))

    def test_edits_before_cutoff_are_kept(self):
        hist = [h("2026-10-17T10:00:00.000-0500", "old")]
        as_of = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
        self.assertEqual(description_as_of("current", hist, as_of), ("current", 0))

    def test_hash_changes_only_with_client_input(self):
        base = content_hash("desc", [{"author_id": TEAM, "text": "DEV - 1"}], CFG)
        self.assertEqual(base, content_hash("desc", [], CFG))
        self.assertNotEqual(base, content_hash("desc", [{"author_id": CLIENT, "text": "also X"}], CFG))


if __name__ == "__main__":
    unittest.main()
