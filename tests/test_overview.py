import datetime as dt
import os
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

from pmauto import overview

UTC = dt.timezone.utc
CFG = {"client_accounts": {"c1": "Client One"}}


def issue(key, comments=(), history=()):
    return NS(key=key, comments=list(comments), history=list(history))


class Store:
    def __init__(self, rows): self.rows = rows
    def ticket(self, key): return self.rows.get(key)


class Overview(unittest.TestCase):
    def test_client_updates_only_client_accounts_in_window_and_not_new(self):
        since = dt.datetime(2026, 11, 5, tzinfo=UTC)
        a = issue("A-1", [NS(author_id="c1", author="x", created=since + dt.timedelta(days=1), text="please check")])
        b = issue("B-1", [NS(author_id="team", author="Dev", created=since + dt.timedelta(days=1), text="done")])
        c = issue("C-1", [NS(author_id="c1", author="x", created=since - dt.timedelta(days=1), text="old")])
        d = issue("D-1", [NS(author_id="c1", author="x", created=since + dt.timedelta(days=2), text="new ticket")])
        rows = overview.client_updates([a, b, c, d], CFG, since, exclude={"D-1"})
        self.assertEqual([r["issue"].key for r in rows], ["A-1"])
        self.assertEqual(rows[0]["last"][1], "Client One")

    def test_run_delay(self):
        self.assertEqual(overview.run_delay_minutes({"event": "schedule", "createdAt": "2026-11-11T11:21:54Z"}), 472)
        self.assertIsNone(overview.run_delay_minutes({"event": "workflow_dispatch", "createdAt": "2026-11-11T06:10:17Z"}))

    def test_drafts_waiting_skips_posted_unreviewed_and_unanalysed(self):
        with tempfile.TemporaryDirectory() as d:
            for f in ["K-1-comment.md", "K-2-comment.md", "K-3-comment.md", "K-4-comment.md", "K-5-meeting-comment.md"]:
                open(os.path.join(d, f), "w").close()
            store = Store({"K-1": {"analysed_at": "x"}, "K-2": {"analysed_at": "x", "posted_comment_id": "9"},
                           "K-3": {"analysed_at": "x"}})
            posted = lambda key, f=None: key == "K-2"
            got = overview.drafts_waiting(store, d, open_refs={"K-3"}, is_posted=posted)
            # K-2 posted, K-3 still on the work list (my review), K-4 never accepted
            self.assertEqual(got, [("K-1", "K-1-comment.md"), ("K-5", "K-5-meeting-comment.md")])

    def test_posted_by_me_needs_my_comment_after_the_draft(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "K-1-comment.md"); open(p, "w").close()
            written = dt.datetime.fromtimestamp(os.path.getmtime(p), UTC)
            before = issue("K-1", [NS(author_id="me", created=written - dt.timedelta(hours=1))])
            after = issue("K-1", [NS(author_id="me", created=written + dt.timedelta(hours=1))])
            other = issue("K-1", [NS(author_id="dev", created=written + dt.timedelta(hours=1))])
            self.assertFalse(overview.posted_by_me(before, "me", p))
            self.assertTrue(overview.posted_by_me(after, "me", p))
            self.assertFalse(overview.posted_by_me(other, "me", p))
